"""计划模式。

计划模式是**会话级的协作状态**，不是提示词开关：

* 状态本身以 log-only 事件 ``plan/mode`` 落库，**不进消息历史** ——
  否则每次模式切换都会在上下文里留下一条与对话无关的噪声消息；
* 真正影响模型的是「下一次请求装配出来的系统提示」里多了一段
  ``plan:policy``（只有 active 时才渲染）；
* 切换发生在回合中途时不会打断当前回合，而是排队，等**下一个 step 边界**
  结算 —— 这样模型不会在同一段回复的前半段按一种模式、后半段按另一种模式行事。

不变量：

1. 最后一个 ``plan/mode`` 事件获胜（fold 语义）；
2. ``exit_plan_mode`` 工具**永远注册**，即使计划模式没开 ——
   保证跨模式切换时发给模型的工具目录完全不变（request-cache 稳定性）；
3. 排队中的切换只有 append 成功后才清除，失败可重试。
"""

from __future__ import annotations

from harness.i18n import tr

import logging
import asyncio
import re
from dataclasses import dataclass
from typing import Any, Literal

from ..core.errors import HarnessError, SessionNotFoundError
from ..core.operation_lock import OperationLock
from ..session.events import AppendOp
from ..session.session import Session

logger = logging.getLogger(__name__)

#: ``exit_plan_mode`` 的入参必须以 markdown 一级标题开头。
PLAN_TITLE_RE = re.compile(r"^#[ \t]+\S")

#: 取出计划的第一行标题，用于 UI 卡片与审批问题。
FIRST_HEADING_RE = re.compile(r"^#[ \t]+(.+)$", re.MULTILINE)

# 计划阶段只需要仓库事实，不应把本机凭据读进模型上下文、计划正文或审阅卡片。
# 判定规则唯一来源是 core.redaction；此处再导出以保持既有调用方与测试不变。
from ..core.redaction import contains_sensitive_literal, is_sensitive_path, is_sensitive_reference


def plan_policy_section() -> str:
    """按本次请求语言解析计划策略，资源是唯一文案来源。"""
    return tr('owned.plan_mode.f50c7c7b353e')


def first_heading(plan: str) -> str:
    """返回计划的第一个一级标题；没有则返回空串。"""
    match = FIRST_HEADING_RE.search(plan or "")
    return match.group(1).strip() if match else ""


@dataclass(frozen=True, slots=True)
class PlanProjection:
    """会话日志 fold 出来的计划模式视图。"""

    active: bool = False
    pending: bool | None = None

    @property
    def effective(self) -> bool:
        """模型当前实际所处的模式（pending 优先）。"""
        return self.pending if self.pending is not None else self.active

    def to_dict(self) -> dict[str, Any]:
        return {"active": self.active, "pending": self.pending, "effective": self.effective}


@dataclass(frozen=True, slots=True)
class PlanIntent:
    """一次待结算的模式切换。"""

    active: bool
    narrate: bool = True


SetOutcome = Literal["committed", "queued", "cancelled", "noop"]


class PlanModeController:
    """按会话维护计划模式状态与待结算意图。

    :param sessions: 会话仓库，用于按 id 反查 :class:`Session`
                     （工具层只有 ``session_id``，需要拿到会话才能落事件）。
    """

    def __init__(self, sessions: Any = None) -> None:
        self._sessions = sessions
        self._pending: dict[str, PlanIntent] = {}
        self._parents: dict[str, str] = {}
        self._review_locks: dict[str, OperationLock] = {}
        self._review_epochs: dict[str, int] = {}

    # -- 视图 ------------------------------------------------------------
    def implementation_approval(self, session: Session) -> dict[str, Any] | None:
        """仅返回尚未跨过回合终态的最新批准，旧计划不强制新问题建单。"""
        from .plans import versions
        records = versions(session)
        if not records or records[-1]['status'] != 'approved':
            return None
        plan_id = records[-1]['id']
        decision = next(event for event in reversed(session.events_of_type('note'))
                        if event.data.get('kind') == 'plan_decision' and event.data.get('id') == plan_id)
        ended = session.latest('turn/end')
        if ended is not None and ended.seq > decision.seq:
            return None
        latest_user = session.latest('user/message')
        if latest_user is not None and latest_user.seq > decision.seq and latest_user.data.get('source') == 'steer':
            return None
        return {'id': plan_id, 'source_seq': decision.seq}

    def projection(self, session: Session) -> PlanProjection:
        """fold 会话日志得出计划模式视图。"""
        active = False
        for event in session.events_of_type("plan/mode"):
            if isinstance(event.data.get("active"), bool):
                active = bool(event.data["active"])
        intent = self._pending.get(session.id)
        return PlanProjection(active=active, pending=intent.active if intent else None)

    def is_active(self, session: Session) -> bool:
        """模型当前实际是否处于计划模式（含排队中的切换）。"""
        return self.projection(session).effective or self._parent_active(session.id)

    def is_active_session_id(self, session_id: str) -> bool:
        """按 id 判断（系统提示装配时只有 ``session_id``）。"""
        session = self._resolve(session_id)
        if session is None:
            return self._parent_active(session_id)
        return self.is_active(session)

    def bind_parent(self, session_id: str, parent_id: str) -> None:
        """子代理实时继承祖先限制，不能通过自己的模式切换解除。"""
        self._parents[session_id] = parent_id

    def _parent_active(self, session_id: str) -> bool:
        seen = {session_id}
        def parent_of(sid):
            session = self._resolve(sid)
            return self._parents.get(sid) or (session.header.parent_session if session else None)
        parent = parent_of(session_id)
        while parent:
            if parent in seen:
                return True  # 损坏的父链不能解除限制。
            seen.add(parent)
            session = self._resolve(parent)
            if session is not None:
                projection = self.projection(session)
                if projection.active or projection.effective:
                    return True
            parent = parent_of(parent)
        return False

    def guard(self, tool: Any, args: Any, ctx: Any) -> str | None:
        """计划模式限制与执行权限独立；并行安全不代表只读。"""
        session = ctx.services.get("session") or self._resolve(ctx.session_id)
        if session is not None:
            projection = self.projection(session)
            active = projection.active or projection.effective or self._parent_active(ctx.session_id)
        else:
            active = self.is_active_session_id(ctx.session_id)
        if not active:
            return None
        name = tool.schema.name
        if name in {'read', 'glob', 'grep'}:
            fields = ('pattern',) if name == 'glob' else ('path', 'glob') if name == 'grep' else ('path',)
            references = [args.get(key) for key in fields if isinstance(args, dict)]
            if any(isinstance(value, str) and is_sensitive_path(value) for value in references):
                return tr('python.plan_mode.sensitive_reference')
        if name in {'terminal_read', 'terminal_list', 'terminal_send', 'terminal_open'}:
            return tr('python.plan_mode.a55832dec5b4')
        controls = {"ask_user_question", "list_agents", "wait_agent", "wait_agents", "spawn_agent", "followup_task", "send_message", "interrupt_agent"}
        reads = {"get_goal", "session_search", "session_trace", "session_event_search", "session_event_trace", "session_event_read", "list_subagent_models", "load_workspace_dependencies", "list_mcp_resources", "list_mcp_resource_templates", "read_mcp_resource", "web_fetch", "web_search"}
        if name == "exit_plan_mode":
            if self._parent_active(ctx.session_id):
                return tr('python.plan_mode.d8a11d8e83e9')
            return None
        if tool.dangerous or tool.approval_required:
            allowed = False
        elif name == "browser":
            allowed = args.get("action") in {"navigate", "observe", "screenshot", "tabs", "diagnostics", "options"}
        else:
            allowed = tool.effect == "read" or tool.effect == "unknown" and name in controls | reads
        if allowed:
            return None
        return (
            tr('python.plan_mode.a55832dec5b4')
        )

    def snapshot(self, session_id: str) -> dict[str, Any]:
        """给 UI / API 用的状态快照。"""
        session = self._resolve(session_id)
        if session is None:
            return {"session_id": session_id, "known": False}
        data = self.projection(session).to_dict()
        data["session_id"] = session_id
        data["known"] = True
        return data

    def completion_feedback(self, session: Session) -> str | None:
        """计划轮次须提交审批；只认本轮事件，不从回复文字猜测意图。"""
        if not self.is_active(session) or self._parent_active(session.id):
            return None
        start = session.latest('turn/start')
        if start is None:
            return None
        for event in reversed(session.events):
            if event.seq <= start.seq:
                break
            # 仅明确的通道故障可以报告阻塞；参数错误不能豁免审批。
            if event.type == 'note':
                kind = event.data.get('kind')
                if kind == 'plan_review_unavailable':
                    return None
                if kind == 'plan_decision':
                    if event.data.get('status') == 'closed':
                        return None
                    break
                if kind == 'plan_submission':
                    break
        return tr('plan.completion.required')

    # -- 切换 ------------------------------------------------------------
    async def set(self, session: Session, active: bool, *, narrate: bool = True) -> SetOutcome:
        """切换计划模式。

        * 回合之间 → 立即落事件，返回 ``committed``；
        * 回合进行中 → 排队等下一个 step 边界，返回 ``queued``；
        * 已经是目标状态 → ``noop``（不产生事件、不叙述）。
        """
        async with self.review_lock(session.id):
            return await self._set(session, active, narrate=narrate)

    def review_lock(self, session_id: str) -> asyncio.Lock:
        return self._review_locks.setdefault(session_id, OperationLock())

    def review_epoch(self, session_id: str) -> int:
        return self._review_epochs.get(session_id, 0)

    async def _set(self, session: Session, active: bool, *, narrate: bool = True) -> SetOutcome:
        """调用方持有 review_lock，防止旧审批跨越模式切换生效。"""
        intent = self._pending.get(session.id)
        if intent is None and self.projection(session).active == active:
            return "noop"
        if session.open_turn is not None:
            self._pending[session.id] = PlanIntent(active=active, narrate=narrate)
            self._review_epochs[session.id] = self.review_epoch(session.id) + 1
            # 所有调用入口（包括计划审批工具）都即时公布待生效状态。
            await session.bus.emit("plan_mode", self.snapshot(session.id))
            return "queued"
        await self._commit(session, active, narrate=narrate)
        self._review_epochs[session.id] = self.review_epoch(session.id) + 1
        return "committed"

    def assert_idle(self, session_id: str) -> None:
        """同步取消和删除不得越过已接受的状态操作或等待者。"""
        lock = self._review_locks.get(session_id)
        if lock is not None and lock.busy:
            raise HarnessError(tr('ui.collaboration.error.plan/busy'), code='plan/busy')

    def cancel(self, session: Session) -> SetOutcome:
        """丢弃尚未进入提交的切换，正在操作时明确拒绝。"""
        self.assert_idle(session.id)
        if self._pending.pop(session.id, None) is None:
            return "noop"
        self._review_epochs[session.id] = self.review_epoch(session.id) + 1
        return "cancelled"

    async def settle(self, session: Session) -> SetOutcome:
        """在 step 边界结算排队中的切换（agent 的 pre-step 钩子调用）。"""
        async with self.review_lock(session.id):
            intent = self._pending.get(session.id)
            if intent is None:
                return "noop"
            # append 失败时**不要**清 pending，让下一个边界还能重试。
            await self._commit(session, intent.active, narrate=intent.narrate)
            if self._pending.get(session.id) is intent:
                self._pending.pop(session.id, None)
            return "committed"

    def session_of(self, session_id: str) -> Session | None:
        """按 id 取会话；工具层只有 ``ctx.session_id``，需要它来落事件。"""
        return self._resolve(session_id)

    def forget(self, session_id: str) -> None:
        """会话被删除时清掉它的排队意图。"""
        self.assert_idle(session_id)
        self._pending.pop(session_id, None)
        self._parents.pop(session_id, None)
        self._review_locks.pop(session_id, None)
        self._review_epochs.pop(session_id, None)

    # -- 内部 ------------------------------------------------------------
    async def _commit(self, session: Session, active: bool, *, narrate: bool) -> None:
        previous = self.projection(session).active
        await session.append("plan/mode", {"active": active})
        await session.bus.emit("plan_mode", self.snapshot(session.id))
        if narrate and previous != active:
            text = tr('python.plan_mode.6d0ccc16d935' if active else 'python.plan_mode.0f01235ff956')
            await session.append(
                "developer/message",
                {"text": text, "source": "plan-mode"},
                surface_op=AppendOp(),
            )
            logger.info(tr('log.63f0be1e97ca'), previous, active)
        else:
            logger.info(tr('feedback.533de23888ef'), previous, active)

    def _resolve(self, session_id: str) -> Session | None:
        if self._sessions is None or not session_id:
            return None
        try:
            return self._sessions.get(session_id)
        except SessionNotFoundError:
            # 尚未物化或已删除的会话没有独立模式；损坏状态必须向调用方报错。
            return None


__all__ = [
    "plan_policy_section",
    "PlanIntent",
    "PlanModeController",
    "PlanProjection",
    "PLAN_TITLE_RE",
    "SetOutcome",
    "first_heading",
    "is_sensitive_reference",
    "contains_sensitive_literal",
]
