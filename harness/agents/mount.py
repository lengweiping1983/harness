"""Agent挂载（召唤）状态机。

对齐 :mod:`harness.app.plan_mode` 的设计纪律：

* 挂载状态以 log-only 事件 ``agent/mode`` 落库，**不进消息历史** ——
  否则每次召唤/退场都会在上下文里留下一条噪声消息；
* 真正影响模型的是「下一次请求装配出来的系统提示」里多了一段
  ``agent:active``（只有挂载时才渲染）；
* 挂载发生在回合中途时不会打断当前回合——下一回合开始时生效。

不变量：

1. 最后一个 ``agent/mode`` 事件获胜（fold 语义），重启后重放日志即恢复；
2. Agent不存在时挂载失败，会话状态不变；
3. 单 Agent 使用角色指令；团队主理人使用团队说明、成员目录和协作原语。
   常驻选择持续生效，显式命令使用单次选择上下文。
"""

from __future__ import annotations

from harness.i18n import tr

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Callable

from ..core.errors import AgentError, AgentNotFoundError, SessionNotFoundError
from ..session.session import Session
from .parser import AgentDefinition
from .registry import AgentRegistry

logger = logging.getLogger(__name__)

_ONCE_SELECTION: ContextVar[tuple[object, str, AgentDefinition, dict] | None] = ContextVar('once_agent_selection', default=None)

_PERSONA_SNAPSHOT: ContextVar[tuple[object, str, str] | None] = ContextVar('agent_persona_snapshot', default=None)


@dataclass(frozen=True, slots=True)
class AgentProjection:
    """一次挂载的投影（从事件日志 fold 出来的当前状态）。"""

    agent: str
    model: str | None = None
    team_worktrees: bool = False
    team_working_copy: bool = False
    agent_type: str | None = None
    #: 团队使用 prompt 原语调度；单体不携带团队策略。
    #: （提示词驱动：主理人成为会话主 agent 现场编排）。

    def to_dict(self) -> dict[str, Any]:
        """选项保持稀疏，团队编排显式保存，避免默认值变化影响重放。"""
        data: dict[str, Any] = {"agent": self.agent, "model": self.model}
        if self.team_worktrees:
            data["team_worktrees"] = True
        if self.team_working_copy:
            data["team_working_copy"] = True
        if self.agent_type == "team":
            data["agent_type"] = "team"
        return data


class AgentMountController:
    """会话级Agent挂载控制器。

    :param registry: Agent注册表（按名解析）。
    :param sessions: SessionStore（按 id 取会话重放挂载状态）。
    """

    def __init__(self, registry: AgentRegistry, sessions: Any = None, *, registry_for_session: Callable[[Session], AgentRegistry] | None = None, session_resolver: Callable[[str], Session | None] | None = None) -> None:
        self.registry = registry
        self._sessions = sessions
        self._registry_for_session = registry_for_session
        self._session_resolver = session_resolver

    # -- 投影 ------------------------------------------------------------
    def projection(self, session: Session) -> AgentProjection | None:
        """从事件日志 fold 出当前挂载（最后一个 ``agent/mode`` 获胜）。"""
        latest = session.latest("agent/mode")
        if latest is None:
            return None
        data = latest.data or {}
        unknown = set(data) - {'agent', 'model', 'agent_type', 'team_worktrees', 'team_working_copy'}
        if unknown:
            raise AgentError(tr('agent.definition.fields_invalid', p0=', '.join(sorted(unknown))))
        raw = data.get("agent")
        # JSON null 落库后是 None，str(None) 会变成 "None" 字符串——必须
        # 先判空再转字符串，否则"退场"会被读成一个叫 None 的Agent。
        if raw is None:
            return None
        name = str(raw).strip()
        if not name:
            return None
        model = data.get("model")
        return AgentProjection(agent=name, model=str(model).strip() if model else None,
                                team_worktrees=data.get("team_worktrees") is True,
                                team_working_copy=data.get("team_working_copy") is True,
                                agent_type="team" if data.get("agent_type") == "team" else None)

    def session_of(self, session_id: str) -> Session | None:
        """按 id 取会话（容忍会话已删除）。"""
        if self._session_resolver is not None:
            return self._session_resolver(session_id)
        if self._sessions is None:
            return None
        try:
            return self._sessions.get(session_id)
        except SessionNotFoundError:
            return None

    @contextmanager
    def selection_scope(self):
        """一次性调用只在当前执行上下文生效，退出时不改持久选择。"""
        token = _ONCE_SELECTION.set(None)
        try:
            yield
        finally:
            _ONCE_SELECTION.reset(token)

    def select_once(self, session_id: str, agent: AgentDefinition, **options) -> None:
        """覆盖本次任务的解析目标，不写入 agent/mode。"""
        _ONCE_SELECTION.set((self, session_id, agent, options))

    def active_projection(self, session_id: str) -> AgentProjection | None:
        once = _ONCE_SELECTION.get()
        if once is not None and once[0] is self and once[1] == session_id:
            return AgentProjection(agent=once[2].name, model=once[2].model, agent_type=once[2].agent_type,
                                    team_worktrees=bool(once[3].get("team_worktrees") or once[3].get("team_working_copy")),
                                    team_working_copy=bool(once[3].get("team_working_copy")))
        session = self.session_of(session_id)
        if session is None:
            return None
        return self.projection(session)

    def resolve_active(self, session_id: str) -> tuple[AgentProjection, AgentDefinition] | None:
        """解析当前挂载对应的Agent定义。

        Agent已被删除或停用时返回 ``None``；存储损坏及注册失败继续报错。
        """
        once = _ONCE_SELECTION.get()
        if once is not None and once[0] is self and once[1] == session_id:
            return self.active_projection(session_id), once[2]
        projection = self.active_projection(session_id)
        if projection is None:
            return None
        try:
            session = self.session_of(session_id)
            registry = self._registry_for_session(session) if self._registry_for_session and session else self.registry
            agent = registry.get(projection.agent)
        except AgentNotFoundError:
            return None
        return projection, agent

    # -- 切换 ------------------------------------------------------------
    async def set(self, session: Session, name: str | None, *, team_worktrees: bool = False, team_working_copy: bool = False) -> None:
        """切换挂载（``None`` 表示退场）。

        挂载前校验Agent存在；退场无条件生效。

        团队挂载后主理人成为会话主 Agent，工作区选项仅约束成员隔离与交付。
        """
        if name is None:
            await session.append("agent/mode", {"agent": None})
            return
        name = name.strip()
        if not name:
            await session.append("agent/mode", {"agent": None})
            return
        registry = self._registry_for_session(session) if self._registry_for_session else self.registry
        agent = registry.get(name)  # AgentNotFoundError 冒泡给调用方
        model = None if agent.is_team else agent.model
        if agent.is_team:
            from .prompt_team import lead_model
            model = lead_model(agent, registry)
        await session.append(
            "agent/mode",
            AgentProjection(
                agent=agent.name,
                model=model,
                team_worktrees=agent.is_team and (team_worktrees or team_working_copy),
                team_working_copy=agent.is_team and team_working_copy,
                agent_type="team" if agent.is_team else None,
            ).to_dict(),
        )

    # -- 渲染 ------------------------------------------------------------
    @contextmanager
    def freeze_persona(self, session_id: str, *, text: str | None = None):
        """固定当前回合的人格文本，步骤重装仍可更新计划与权限策略。"""
        text = self._render_current_section(session_id) if text is None else text
        token = _PERSONA_SNAPSHOT.set((self, session_id, text))
        try:
            yield
        finally:
            _PERSONA_SNAPSHOT.reset(token)

    def render_section(self, session_id: str) -> str:
        """渲染挂载Agent的人格 section（未挂载时为空字符串）。

        该方法被注册成 ``agent:active`` 动态提示片段的提供函数，
        每次请求装配时按 ``session_id`` 现算——这正是挂载状态
        「下一回合自然生效」的机制；执行中的回合使用已固定的人格。
        """
        snapshot = _PERSONA_SNAPSHOT.get()
        if snapshot is not None and snapshot[0] is self and snapshot[1] == session_id:
            return snapshot[2]
        return self._render_current_section(session_id)

    def _render_current_section(self, session_id: str) -> str:
        """直接读取当前选择，使新回合不借用异步任务继承的旧快照。"""
        resolved = self.resolve_active(session_id)
        if resolved is None:
            return ""
        projection, agent = resolved
        if agent.is_team:
            from .prompt_team import render_team_lead_section
            session = self.session_of(session_id)
            registry = self._registry_for_session(session) if self._registry_for_session and session else self.registry
            return render_team_lead_section(agent, registry)
        body = agent.system_prompt.strip()
        if not body:
            body = tr('python.mount.0f382e63a5b3', p0=f'{agent.name}', p1=f'{agent.description}')
        return (
            tr('python.mount.fb74e7ed2c15', p0=f'{agent.name}', p1=f'{body}')
        )


__all__ = ["AgentMountController", "AgentProjection"]
