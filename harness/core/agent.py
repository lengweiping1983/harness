"""Agent 循环：整个 harness 的驱动核心。

一次"回合（turn）"的结构::

    turn/start
      ├─ step 1:  组装系统提示 → 流式请求 → 得到助手消息
      │              ├─ assistant/message（含内嵌流）
      │              ├─ tool/call × N
      │              └─ tool/result
      ├─ step 2:  把工具结果喂回模型 → 得到最终回答
      │              └─ assistant/message（无工具调用 → 回合结束）
      └─ turn/end (completed)

设计要点：

1. **历史来自日志**：每次请求的 messages 都由 :meth:`Session.build_messages`
   实时派生，不维护第二份状态。
2. **流是事件**：模型的每个增量都转成 ``assistant/delta`` 事件广播给 UI，
   同时累积；累积完成后把完整消息 + 原始流记录写进 ``assistant/message``，
   于是"重放一条事件"就能完整复原当时的输出。
3. **工具循环**：模型返回 tool_calls 就执行并回灌，直到模型不再调用工具。
4. **可取消**：:meth:`AgentLoop.cancel` 阻止后续请求和工具启动，保留已提交结果。
5. **上下文压缩**：超过阈值时先压缩再请求。
"""

from __future__ import annotations

from harness.llm.context_policy import DEFAULT_WORKING_BUDGET

from harness.i18n import tr
from ..llm.stream_merge import merge_thinking_event

import asyncio
import contextvars
from contextlib import aclosing, asynccontextmanager
import inspect
import json
import logging
from collections.abc import Mapping
import time
from dataclasses import replace
from typing import Any, Sequence
from uuid import uuid4

from ..llm.adapter import OpenRouterAdapter
from ..llm.retry import retry_delay
from ..llm.context_accounting import estimate_tokens, reasoning_mode
from ..llm.vision import IMAGE_TOOL_NAMES, omit_image_data
from .cleanup import join_cleanup, preserve_cleanup_errors, raise_cleanup_errors
from .errors import HarnessError, LlmError, LlmContextLengthError, LlmResponseError, SessionInvariantError
from .response_cost import ResponseCost
from .turn import AgentStatus, TurnResult
from .todo_progress import TodoProgressReminder, is_business_result
from .browser_verification import BrowserVerificationReminder
from .todo_initialization import TodoInitialization
from .jsonutil import to_jsonable
from ..llm.types import (
    AssistantMessage,
    DeveloperMessage,
    UserMessage,
    LlmCallConfig,
    TextBlock,
    ThinkingBlock,
    TokenUsage,
    ToolUseBlock,
)
from ..prompt.registry import SystemPromptRegistry
from ..session.events import AppendOp, ReplaceOp, EpochHeader, TurnEndReason
from ..session.session import Session
from ..skills.registry import SkillRegistry
from ..skills.tool import render_catalog
from ..tools.registry import ToolRegistry, ToolResult, ToolRunContext

logger = logging.getLogger(__name__)

_MESSAGE_SPEAKER: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar('message_speaker', default=None)


_MESSAGE_AUTHOR: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar("message_author", default=None)


def _adapter_parameters(method: Any) -> Mapping[str, inspect.Parameter]:
    """无法反射第三方调用签名时记录诊断，并沿用基础调用协议。"""
    try:
        return inspect.signature(method).parameters
    except (ValueError, TypeError):
        logger.warning(
            tr('log.a96f2ef32b81'),
            type(method).__name__, getattr(method, '__name__', '<unknown>'),
            exc_info=True,
        )
        return {}


def _speaker_metadata() -> dict[str, Any]:
    """界面发言身份固定于本回合，副本避免事件消费者改写后续历史。"""
    speaker = _MESSAGE_SPEAKER.get()
    author = _MESSAGE_AUTHOR.get()
    return {**({'speaker': dict(speaker)} if speaker else {}), **({'author': dict(author)} if author else {})}


#: 每回合步骤上限；None 表示无限，仍可主动停止。
MAX_STEPS_PER_TURN = None
#: 流中途瞬时故障的基础恢复预算；多 key 池至少允许轮换一圈。
#: 正文续传与无正文重试共享 max(基础预算, key 数量 - 1) 次恢复。
MAX_STREAM_CONTINUATIONS = 3


class _RequestBudgetExhausted(HarnessError):
    """等待任务树预算结算后，没有发送请求。"""


class AgentLoop:
    """驱动单个会话的 agent 循环。

    :param adapter:  模型适配器。
    :param tools:    工具注册表。
    :param prompt:   系统提示词注册表。
    :param skills:   技能注册表（可为 ``None``）。
    :param config:   默认调用配置。
    :param bus:      事件总线，用于把流式增量广播给 UI。
    """

    def __init__(
        self,
        adapter: OpenRouterAdapter,
        tools: ToolRegistry,
        prompts: SystemPromptRegistry,
        *,
        skills: SkillRegistry | None = None,
        config: LlmCallConfig | None = None,
        bus: Any = None,
        max_steps: int | None = MAX_STEPS_PER_TURN,
    ) -> None:
        self.adapter = adapter
        self.tools = tools
        self.prompts = prompts
        self.skills = skills
        #: 宿主可按聊天解析技能目录，独立 AgentLoop 保持默认注册表兼容。
        self.skills_for_session: Any = None
        self.config = config or LlmCallConfig(model=adapter.model)
        self.bus = bus
        self.max_steps = max_steps

        self.status = AgentStatus(session_id="")
        self._cancel_requested = False
        self._cancelled_reason: str | None = None
        self._unstarted_call_ids: set[str] = set()
        # step 边界钩子：签名 ``async (agent, session, turn, step) -> None``。
        # 用来在"请求已经发出之前"结算跨回合状态（如计划模式的排队切换），
        # 这样同一个 step 里的系统提示与工具表必然自洽。
        self._pre_step_hooks: list[Any] = []
        # 由宿主负责完成检查；非空结果会请求追加一步，
        # 仍受本回合现有预算限制，发生异常则终止回合。
        self.stop_guard: Any = None
        #: 宿主统一生命周期入口，独立循环不依赖插件实现。
        self.lifecycle_hook: Any = None
        self._stop_feedback: str | None = None
        #: 可选的宿主服务句柄（如计划模式控制器）。设为 None 时 pre-step
        #: 钩子里的相关逻辑自动跳过，AgentLoop 不需要知道具体是什么服务。
        self.plan_controller: Any = None
        #: 重复工具调用提醒器。
        #: 为 None 时整条护栏关闭。挂在 AgentLoop 上而不是全局单例，是为了让
        #: 每个会话（含子代理）各自维护独立的重复链。
        self.repeat_reminder: Any = None
        #: 过重工具结果裁剪器。为 None 时压缩流程不做预裁剪。
        self.tool_result_pruner: Any = None
        # 本次运行的统计
        self._usage_total = TokenUsage()
        self._compaction_charged = 0
        self._compaction_usage = TokenUsage()
        self._compaction_restart = False
        self.pending_input: list[str] = []
        self.steering_source: Any = None
        self.team_steering: Any = None
        self.steering_lock = asyncio.Lock()
        self.accepting_steering = False
        self.compact_threshold: int | None = None
        self.summary_max_tokens: int | None = 8192
        self.context_policy = None
        self.context_control = None
        self.context_auto_manage = True
        self.context_working_budget = DEFAULT_WORKING_BUDGET
        self.tool_result_max_chars: int | None = 12000
        from ..tools.discovery import ToolDiscovery
        self.tool_discovery = ToolDiscovery()
        self.tool_schema_mode = 'all'
        self.tool_schema_policy = None
        #: 会话运行可投影模型工具参数，不改变共享注册表或执行授权。
        self.tool_schema_transform = None
        self._tool_projection_policy: tuple | None = None
        self.output_reserve = 16_000
        #: 部署模型的真实上下文容量；用于把 max_tokens 收敛到
        #: 「窗口 - 本请求输入 - 余量」，避免端点按 输入+输出 拒绝请求。
        self.context_window: int | None = None
        #: 端点通过 400 自报真实窗口后的通知回调（上层据此持久化）。
        self.on_context_window_learned: Any = None
        self.token_budget: int | None = None
        self.budget: Any = None
        self.cancel_cleanup: Any = None
        #: 宿主绑定的当前执行身份；回合、工具及消息应用共用，不继承父运行。
        self.run_id: str | None = None
        self.recovery_read_only = False
        #: 可信运行时声明的只读任务，独立于失败恢复检查语义。
        self.task_read_only = False
        self.isolated_team_coordinator = False
        self.reply_max_chars: int | None = None
        #: 子代理提问应送达的根会话，由可信宿主绑定。
        self.root_session_id: str | None = None
        #: 运行中的直接父代理会话，与历史分叉关系分开记录。
        self.parent_session_id: str | None = None
        #: 宿主指定的成员文件写入根目录，不接收模型参数。
        self.workspace_write_root: str | None = None
        self._todo_progress: TodoProgressReminder | None = None
        self._browser_verification: BrowserVerificationReminder | None = None
        self._todo_initialization: TodoInitialization | None = None
        self.stream_checkpoints = True
        self._checkpoint_clock = time.monotonic
        self._language_policy_enabled = False
        self._language_drift_detected = False

    def _reminder_for(self) -> Any:
        """返回宿主显式绑定的提醒器；None 表示关闭。"""
        return self.repeat_reminder

    def add_pre_step_hook(self, hook: Any) -> Any:
        """注册一个 step 边界钩子，返回它本身（便于装饰器写法）。"""
        if not callable(hook):
            raise TypeError(tr('python.agent.28ea5f367dae'))
        self._pre_step_hooks.append(hook)
        return hook

    async def _run_pre_step_hooks(self, session: Session, turn: int, step: int) -> None:
        """按序完成步骤状态结算，失败交给轮次收尾，不继续使用未知状态。"""
        for hook in list(self._pre_step_hooks):
            self._check_cancelled()
            result = hook(self, session, turn, step)
            if inspect.isawaitable(result):
                await result

    # ==================================================================
    # 取消控制
    # ==================================================================
    def cancel(self, reason: str = "unknown") -> None:
        """请求停止当前回合，不再启动后续模型请求或工具。"""
        self._cancel_requested = True
        self._cancelled_reason = reason
        self.status.status = "cancelled"

    def _check_cancelled(self) -> None:
        if self._cancel_requested:
            raise asyncio.CancelledError

    @property
    def cancelled(self) -> bool:
        return self._cancel_requested

    def _has_pending_steering(self, session: Session, turn: int) -> bool:
        """外部协作源在同一执行边界阻止成员启动过时工具。"""
        return bool(self.pending_input or session.steering.pending(turn)
                    or self.steering_source is not None and self.steering_source.pending())

    @asynccontextmanager
    async def _final_steering_admission(self):
        """成员结束决定与父会话引导提交共享准入锁，避免提交期间提前结束。"""
        async with self.steering_lock:
            if self.steering_source is None:
                yield
            else:
                async with self.steering_source.admission_lock:
                    yield

    def reset(self) -> None:
        """重置取消标志（下次 prompt 前调用）。"""
        self._cancel_requested = False
        self._cancelled_reason = None
        self._unstarted_call_ids.clear()
        self.pending_input.clear()
        self.accepting_steering = False
        self._stop_feedback = None

    # ==================================================================
    # 主入口
    # ==================================================================
    async def prompt(self, session: Session, user_input: str, *, config: LlmCallConfig | None = None, source: str = "user", goal_id: str | None = None, original_text: str | None = None, attachments: list[dict[str, Any]] | None = None, team_recipients: dict[str, Any] | None = None) -> TurnResult:
        """固定本回合语言，并在所有退出路径恢复调用方上下文。"""
        from .language import current_language, resolve_language
        preference = getattr(self, 'language_preference', lambda: current_language.get())()
        self.language = resolve_language(preference).effective_language
        token = current_language.set(self.language)
        speaker_token = None
        author_token = _MESSAGE_AUTHOR.set(None)
        try:
            provider = getattr(self, 'message_speaker_for_session', None)
            speaker = provider(session) if provider is not None else None
            speaker_token = _MESSAGE_SPEAKER.set(dict(speaker) if speaker else None)
            author_provider = getattr(self, 'message_author_for_session', None)
            author = author_provider(session) if author_provider else None
            _MESSAGE_AUTHOR.set(dict(author) if author else None)
            return await self._prompt(session, user_input, config=config, source=source, goal_id=goal_id, original_text=original_text, attachments=attachments, team_recipients=team_recipients)
        finally:
            if speaker_token is not None:
                _MESSAGE_SPEAKER.reset(speaker_token)
            _MESSAGE_AUTHOR.reset(author_token)
            current_language.reset(token)

    async def _prompt(
        self,
        session: Session,
        user_input: str,
        *,
        config: LlmCallConfig | None = None,
        source: str = "user",
        goal_id: str | None = None,
        original_text: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
        team_recipients: dict[str, Any] | None = None,
    ) -> TurnResult:
        """处理一次用户输入，完成整个回合。

        :param session:    目标会话。
        :param user_input: 用户文本。
        :param config:     本次覆盖的调用配置（模型、思考强度等）。
        :returns: :class:`TurnResult`。
        """
        # 注意：这里 **不** 调用 reset()。调用方可能在 prompt() 之前调用了
        # cancel() 来阻止排队中的输入；重置会把这个取消意图抹掉。
        cfg = config or self.config
        turn = session.current_turn + 1
        self.status = AgentStatus(session_id=session.id, turn=turn, status="thinking")

        start_event = await session.start_turn(turn, run_id=self.run_id, language=self.language)
        self.accepting_steering = True

        final_text = ""
        final_response_seq = None
        final_thinking = ""
        all_tool_calls: list[ToolUseBlock] = []
        usage = TokenUsage()
        charged_tokens = 0
        stop_continuations = 0
        output_continuation = False
        output_prefix = ""
        self._language_drift_detected = False
        self._stop_feedback = None
        self._compaction_usage = TokenUsage()
        self._compaction_charged = 0
        step = 0
        reason = "completed"

        try:
            # 已提交轮次后，运行辅助状态初始化同样属于本轮错误与取消收尾边界。
            self._todo_progress = TodoProgressReminder()
            self._todo_progress.sync(session.latest('todo/write'))
            self._browser_verification = BrowserVerificationReminder()
            self._todo_initialization = TodoInitialization(start_event.seq)
            # 1) 记录用户输入
            from ..skills.context import user_spans
            skill_loads = user_spans(session.id, user_input)
            origin = getattr(self, 'user_origin_for_session', lambda current: {})(session.id)
            user_event = await session.append(
                "user/message",
                {"text": user_input, **origin, **({"skill_loads": skill_loads} if skill_loads else {}), **({"original_text": original_text} if original_text is not None and original_text != user_input else {}), "source": source, "turn": turn, **({"team_recipients": team_recipients, "recipients": [row["name"] for row in team_recipients["members"]]} if team_recipients else {}), **({"attachments": attachments} if attachments else {}), **({"goal_id": goal_id} if goal_id else {})},
                surface_op=AppendOp(),
            )
            if self.lifecycle_hook is not None:
                await self.lifecycle_hook(self, session, 'SessionStart', turn, 0, {})
                await self.lifecycle_hook(self, session, 'UserPromptSubmit', turn, 0, {'prompt': user_input})
            # 1.1) 用户插话会改变上下文，跨插话的重复不算死循环 → 重置重复链。
            #      与用户来源消息的步骤前重置语义一致。
            if source == "user":
                reminder = self._reminder_for()
                if reminder is not None:
                    reminder.reset(session.id)
            await self._emit("user/message", {"session_id": session.id, **user_event.data, "source_seq": user_event.seq, "source_time": user_event.time, **({"original_text": original_text} if original_text is not None else {})})
            await self._emit("turn/start", {"session_id": session.id, "turn": turn,
                "time": start_event.time, "source_seq": start_event.seq, "language": self.language})

            # 2) 首次请求前，把系统提示写进日志（派生历史的节点 0）
            await self._ensure_system_prompt(session)

            # 3) 记录请求头
            await self._log_request_header(session, cfg, turn, step, reason_hint="initial")

            # 会话由运行时串行执行：每回合扫描一次既有身份，随后增量更新，
            # 避免每个模型步骤都重新遍历长会话的完整事件日志。
            seen_ids = {str(block.get("id", "")) for event in session.events
                        if event.type == "assistant/message"
                        for block in event.data.get("content", [])
                        if isinstance(block, Mapping) and block.get("type") == "tool_use"}

            # 4) 工具循环
            todo_completion_feedback = None
            browser_completion_feedback = None
            while self.max_steps is None or step < self.max_steps:
                if self._cancel_requested:
                    reason = "aborted"
                    break

                if ((self.token_budget is not None and charged_tokens >= self.token_budget)
                        or (self.budget is not None and self.budget.exhausted)):
                    reason = "budget"
                    break
                async with self.steering_lock:
                    if self.steering_source is not None:
                        await self.steering_source.prepare(self, session, turn)
                    if self._has_pending_steering(session, turn):
                        self._stop_feedback = None
                        output_continuation = False
                        output_prefix = ""
                        self._todo_initialization = TodoInitialization(user_event.seq)
                    for receipt in list(session.steering.pending(turn)):
                        user_event = await session.append('user/message', {'text': receipt['text'], 'source': 'steer',
                            **({'original_text': receipt['original_text']} if 'original_text' in receipt else {}),
                            **({'attachments': receipt['attachments']} if receipt.get('attachments') else {}),
                            'request_id': receipt['request_id'], 'turn': turn}, surface_op=AppendOp())
                        await self._emit('user/message', {'session_id': session.id, 'text': receipt['text'],
                            **({'original_text': receipt['original_text']} if 'original_text' in receipt else {}),
                            **({'attachments': receipt['attachments']} if receipt.get('attachments') else {}),
                            'source': 'steer', 'request_id': receipt['request_id'], 'turn': turn,
                            'source_seq': user_event.seq, 'source_time': user_event.time})
                        await self._emit('steer/state', {'session_id': session.id,
                            'receipt': session.steering.receipt(receipt['request_id'], active_turn=turn, summary=True)})
                    while self.pending_input:
                        text = self.pending_input.pop(0)
                        user_event = await session.append("user/message", {"text": text, "source": "steer", "turn": turn}, surface_op=AppendOp())
                        await self._emit("user/message", {"session_id": session.id, "text": text, "source": "steer", "turn": turn, "source_seq": user_event.seq, "source_time": user_event.time})

                    if self.steering_source is not None:
                        await self.steering_source.applied(self, session, turn)

                step += 1
                self.status.step = step
                self.status.status = "thinking"
                await session.append("step/start", {"turn": turn, "step": step})
                await self._emit("step/start", {"session_id": session.id, "turn": turn, "step": step})

                # 4.0) step 边界：结算跨回合状态（如计划模式的排队切换）。
                # 必须在 _build_messages 之前，否则这一 step 的系统提示
                # 还会停留在旧状态。
                if self._pre_step_hooks:
                    await self._run_pre_step_hooks(session, turn, step)
                self._check_cancelled()
                await session.agent_messages.apply(turn, run_id=self.run_id)
                if self._todo_progress_enabled(session):
                    self._todo_progress.begin_request(session)
                else:
                    self._todo_progress.clear_request()
                self._check_cancelled()
                await self._ensure_system_prompt(session)

                # 4.1) 上下文超限则先压缩
                self._check_cancelled()
                runtime_messages = ()
                if todo_completion_feedback:
                    runtime_messages = (DeveloperMessage(text=todo_completion_feedback, source='todo-final-review'),)
                    todo_completion_feedback = None
                if output_continuation:
                    runtime_messages = (DeveloperMessage(text=tr('agent.output_continuation'),
                                                         source='output-continuation'),)
                if browser_completion_feedback:
                    runtime_messages = (*runtime_messages, DeveloperMessage(
                        text=browser_completion_feedback, source='browser-final-review'))
                    browser_completion_feedback = None
                initialization = self._todo_initialization_for(session)
                if initialization is not None:
                    notice = initialization.take_notice(session.latest('todo/write'))
                    if notice is not None:
                        await session.append('note', {**notice, 'turn': turn, 'step': step}, ignorable=True)
                        runtime_messages = (*runtime_messages, DeveloperMessage(text=notice['text'], source='todo-initialization'))
                if self._todo_progress_enabled(session):
                    snapshot = self._todo_progress.snapshot_context(self._todo_progress.request_event,
                        session_id=session.id, start_seq=start_event.seq)
                    if snapshot is not None:
                        runtime_messages = (*runtime_messages, DeveloperMessage(text=snapshot, source='todo-snapshot'))
                    notice = self._todo_progress.take_notice(self._todo_progress.request_event, session_id=session.id)
                    if notice is not None:
                        await session.append('note', {**notice, 'turn': turn, 'step': step}, ignorable=True)
                        runtime_messages = (*runtime_messages, DeveloperMessage(text=notice['text'], source='todo-progress-reminder'))
                previous_compaction_charge = self._compaction_charged
                if self.context_control is not None:
                    control = await self.context_control(session, self, cfg, turn, runtime_messages)
                    if control and control['status'] == 'failed':
                        current_input = self._estimate_tokens(self._build_messages(session, runtime_messages=runtime_messages)) + self._estimate_tokens(self._active_tool_schemas())
                        if current_input >= (self.compact_threshold or self.context_working_budget):
                            raise HarnessError(tr('context.compaction.noReduction'), code='agent/compaction-capacity')
                await self._maybe_compact(session, cfg, turn, runtime_messages=runtime_messages)
                charged_tokens += self._compaction_charged - previous_compaction_charge
                self._check_cancelled()
                if self.budget is not None and self.budget.exhausted:
                    reason = "budget"
                    break

                # 4.2) 组装消息并发请求
                messages = self._build_messages(session, runtime_messages=runtime_messages)
                request_config = cfg
                remaining = []
                if self.token_budget is not None:
                    remaining.append(self.token_budget - charged_tokens)
                if self.budget is not None and self.budget.limit is not None:
                    remaining.append(self.budget.limit - self.budget.spent)
                if remaining:
                    available = min(remaining) - self._request_input_cost(messages, self._active_tool_schemas())
                    if available < 1:
                        reason = "budget"
                        break
                    request_config = cfg.merged(max_tokens=min(cfg.max_tokens or available, available))
                self.status.status = "streaming"
                self._check_cancelled()
                author_provider = getattr(self, 'message_author_for_session', None)
                if author_provider:
                    author = author_provider(session)
                    _MESSAGE_AUTHOR.set(dict(author) if author else None)
                assistant, stream_record = await self._stream_once(
                    session, messages, request_config, turn=turn, step=step, runtime_messages=runtime_messages
                )
                # 供应商的调用 ID 可能仅在一次请求内唯一。会话里的输出、
                # 审批和恢复按调用 ID 关联，碰撞时必须分配新的内部身份。
                # 原始 ID 仍保留在 stream_record 中供诊断，后续请求和结果
                # 统一使用新 ID，不能把新的副作用当作旧调用的完成记录。
                content = []
                for block in assistant.content:
                    if isinstance(block, ToolUseBlock):
                        if not block.id or block.id in seen_ids:
                            block = replace(block, id="call_" + uuid4().hex)
                        seen_ids.add(block.id)
                    content.append(block)
                assistant = replace(assistant, content=tuple(content))

                # 4.3) 落 assistant/message（含完整流记录）
                response_event = await session.append(
                    "assistant/message",
                    {
                        "content": [b.to_dict() for b in assistant.content],
                        **_speaker_metadata(),
                        "model": assistant.model,
                        "stop_reason": assistant.stop_reason,
                        "usage": assistant.usage.to_dict() if assistant.usage else None,
                        "reasoning_details": to_jsonable(assistant.reasoning_details),
                        "stream": stream_record,
                        "request_id": f"req_{turn}_{step}",
                    },
                    surface_op=AppendOp(),
                )
                await self._observe_response_language(session, assistant, response_event.seq, turn, step)
                await self._emit(
                    "assistant/message",
                    {
                        "session_id": session.id,
                        "source_seq": response_event.seq,
                        "turn": turn,
                        "step": step,
                        "text": assistant.text,
                        "thinking": assistant.thinking,
                        "tool_calls": [c.to_dict() for c in assistant.tool_uses],
                        "usage": assistant.usage.to_dict() if assistant.usage else None,
                    },
                )
                await session.append("step/end", {"turn": turn, "step": step, "reason": "completed"})

                if assistant.usage is not None:
                    usage = usage + assistant.usage
                    self._usage_total = self._usage_total + assistant.usage
                    self.status.input_tokens = self._usage_total.input_tokens
                    self.status.output_tokens = self._usage_total.output_tokens
                    self.status.reasoning_tokens = self._usage_total.reasoning_tokens

                # 供应商未提供用量时，安全预算仍须生效。
                # 估算值不能作为供应商账单统计展示。
                charge = assistant.usage.total_tokens if assistant.usage is not None and assistant.usage.total_tokens > 0 else (
                    self._request_input_cost(messages, self._active_tool_schemas()) + estimate_tokens([assistant]))
                charged_tokens += charge

                final_text = output_prefix + assistant.text
                final_thinking = assistant.thinking
                calls = assistant.tool_uses
                # 等待响应时收到的停止请求仍控制本回合，
                # 包括稍后到达且不再进入下一循环的纯文本回复。
                self._check_cancelled()

                # 输出上限是传输边界，不是任务完成；按正常步骤续写，
                # 继承预算、压缩、引导与取消，不重放任何已执行工具。
                if not calls and assistant.stop_reason in {'length', 'max_tokens'}:
                    output_prefix = final_text
                    output_continuation = True
                    await session.append('note', {'kind': 'output_continuation', 'turn': turn,
                        'step': step, 'stop_reason': assistant.stop_reason,
                        'response_seq': response_event.seq}, ignorable=True)
                    continue
                output_continuation = False
                output_prefix = ""

                # 宿主验证完成结果时，仍开放引导准入。
                if not calls:
                    async with self.steering_lock:
                        if self._has_pending_steering(session, turn):
                            continue
                    todo_event = session.latest('todo/write')
                    initialization = self._todo_initialization_for(session)
                    if initialization is not None and initialization.defer_completion(todo_event):
                        continue
                    notice = self._browser_verification.take_notice()
                    if notice is not None:
                        await session.append('note', {**notice, 'turn': turn, 'step': step}, ignorable=True)
                        browser_completion_feedback = notice['text']
                        continue
                    todo_event = self._todo_progress.selected_event(session)
                    if self._todo_progress_enabled(session) and todo_event is not None and (
                        todo_event.seq > start_event.seq or self._todo_progress.work_steps > 0
                    ):
                        notice = self._todo_progress.take_final_notice(todo_event)
                        if notice is not None:
                            await session.append('note', {**notice, 'turn': turn, 'step': step}, ignorable=True)
                            todo_completion_feedback = notice['text']
                            continue
                    plan_feedback = None
                    if self.plan_controller is not None and not (self.recovery_read_only or self.task_read_only):
                        plan_feedback = self.plan_controller.completion_feedback(session)
                    feedback = plan_feedback
                    if feedback is None and self.stop_guard is not None:
                        feedback = await self.stop_guard(self, session, turn, step,
                            stop_continuations > 0, final_text)
                    self._check_cancelled()
                    async with self._final_steering_admission():
                        if self._has_pending_steering(session, turn):
                            self._stop_feedback = None
                            continue
                        # 结束钩子可能等待；模式切换必须以最终准入时的状态为准。
                        current_plan_feedback = None
                        if self.plan_controller is not None and not (self.recovery_read_only or self.task_read_only):
                            current_plan_feedback = self.plan_controller.completion_feedback(session)
                        feedback = current_plan_feedback or (feedback if plan_feedback is None else None)
                        plan_feedback = current_plan_feedback
                        if feedback:
                            stop_continuations += 1
                            self._stop_feedback = feedback[:8192]
                            await session.append('note', {'kind': 'stop_hook_feedback', 'turn': turn,
                                'step': step, 'continuation': stop_continuations, 'text': self._stop_feedback})
                            continue
                        self._stop_feedback = None
                        self.accepting_steering = False
                    final_response_seq = response_event.seq
                    reason = "completed"
                    break

                if ((self.budget is not None and self.budget.exhausted)
                        or (self.token_budget is not None and charged_tokens >= self.token_budget)):
                    await self._settle_unfinished_tools(session, tr('python.agent.dde95ad2eec7'))
                    reason = "budget"
                    break

                # 4.5) 有工具调用 → 执行并回灌
                # 收尾反馈只驱动本次纠正；后续收尾重新核验，避免过期状态诱发重复执行。
                self._stop_feedback = None
                all_tool_calls.extend(calls)
                self.status.status = "tool"
                await self._execute_tools(session, calls, turn=turn, step=step)
                self._check_cancelled()

            else:
                # 达到 max_steps 上限
                reason = "max-steps"

        except _RequestBudgetExhausted:
            reason = "budget"
        except asyncio.CancelledError as cancelled:
            cleanup_errors = await self._capture_cancel_cleanup()
            with preserve_cleanup_errors(cleanup_errors, cancelled):
                await self._settle_unfinished_tools(session)
                await self._end_turn_safely(session, TurnEndReason(kind="aborted", cause=self._cancelled_reason or "unknown", message=tr('python.agent.f20b0b121f0a')))
                self.status.status = "cancelled"
                await self._emit("turn/end", {"session_id": session.id, "turn": turn, "reason": "aborted"})
                if self.lifecycle_hook is not None:
                    await self.lifecycle_hook(self, session, 'Interrupt', turn, step, {'reason': self._cancelled_reason or 'unknown'})
                await session.drain()
                self.reset()
                return TurnResult(session.id, turn, "aborted", text=final_text, thinking=final_thinking,
                                  tool_calls=all_tool_calls, usage=(usage + self._compaction_usage) if
                                  (usage + self._compaction_usage).total_tokens else None, error=tr('python.agent.f20b0b121f0a'))
        except Exception as exc:
            cleanup_errors = await self._capture_cancel_cleanup()
            with preserve_cleanup_errors(cleanup_errors, exc):
                await self._settle_unfinished_tools(session, tr('python.agent.34b50909a1e0', p0=f'{exc}'))
                logger.exception(tr('log.e5d728b71658'))
                self.status.status = "error"
                self.status.last_error = str(exc)
                error_info = exc.to_dict() if isinstance(exc, HarnessError) else {"code": "run/error", "message": str(exc)}
                await self._end_turn_safely(
                    session, TurnEndReason(kind="error", error=error_info, message=str(exc))
                )
                # 失败也必须广播 turn/end，否则 UI 会一直停在"思考中"。
                committed_end = session.latest('turn/end')
                if committed_end is not None and committed_end.data.get('turn') != turn:
                    committed_end = None
                await self._emit(
                    "turn/end",
                    {"session_id": session.id, "turn": turn, "reason": "error", "error": str(exc),
                     **error_info, "committed": committed_end is not None,
                     "time": committed_end.time if committed_end else None,
                     "source_seq": committed_end.seq if committed_end else None},
                )
                await session.drain()
                return TurnResult(session.id, turn, "error", text=final_text, thinking=final_thinking,
                                  tool_calls=all_tool_calls, usage=(usage + self._compaction_usage) if
                                  (usage + self._compaction_usage).total_tokens else None,
                                  error=str(exc), error_info=error_info)

        # 5) 结束回合
        cleanup_errors = await self._capture_cancel_cleanup() if reason != "completed" else []
        with preserve_cleanup_errors(cleanup_errors):
            limit_error = None
            if reason in {"budget", "max-steps"}:
                limit_error = HarnessError(
                    tr('python.agent.04c1bc66ddf1') if reason == "budget" else
                    tr('python.agent.db7d28ceead3'),
                    code="agent/token-budget" if reason == "budget" else "agent/step-limit")
                self.status.last_error = str(limit_error)
            end_reason = {
                "completed": TurnEndReason(kind="completed"),
                "aborted": TurnEndReason(kind="aborted", cause=self._cancelled_reason or "unknown"),
                "max-steps": TurnEndReason(kind="error", error=limit_error.to_dict() if limit_error else None),
                "budget": TurnEndReason(kind="error", error=limit_error.to_dict() if limit_error else None),
            }.get(reason, TurnEndReason(kind="completed"))
            await self._end_turn_safely(session, end_reason, final_response_seq=final_response_seq if reason == 'completed' else None)

            self.status.status = "done" if reason == "completed" else reason
            committed_end = session.latest('turn/end')
            if committed_end is not None and committed_end.data.get('turn') != turn:
                committed_end = None
            await self._emit("turn/end", {"session_id": session.id, "turn": turn, "reason": "error" if limit_error else reason,
                                         "committed": committed_end is not None,
                                         "time": committed_end.time if committed_end else None,
                                         "source_seq": committed_end.seq if committed_end else None,
                                         "final_response_seq": final_response_seq if reason == 'completed' else None,
                                         **(limit_error.to_dict() if limit_error else {}),
                                         "error": str(limit_error) if limit_error else None})
            await session.drain()
            # 回合正常收尾后清掉取消标志，下一次 prompt 可以正常开始。
            self.reset()

            return TurnResult(
                session_id=session.id,
                turn=turn,
                reason=reason,
                text=final_text,
                thinking=final_thinking,
                tool_calls=all_tool_calls,
                usage=(usage + self._compaction_usage) if (usage + self._compaction_usage).total_tokens else None,
                error=self.status.last_error,
                error_info=limit_error.to_dict() if limit_error else None,
            )

    # ==================================================================
    # 内部：系统提示
    # ==================================================================
    def _eligible_tool_schemas(self) -> list[Any]:
        schemas = [schema.resolve_description(getattr(self, "language", "zh-CN"))
                   for schema in self.tools.schemas()]
        if self.tool_schema_transform is not None:
            schemas = self.tool_schema_transform(schemas)
        if self.isolated_team_coordinator:
            from ..tools.write_scope import coordinator_tool_allowed
            schemas = [schema for schema in schemas if coordinator_tool_allowed(self.tools.get(schema.name))]
        if self.recovery_read_only or self.task_read_only:
            return [schema for schema in schemas if self.tools.get(schema.name).effect == 'read' or
                    schema.name == 'update_goal' and not self.recovery_read_only
                    and getattr(self, 'team_goal', None) is not None
                    and getattr(self, 'team_goal_writer', False)]
        reminder = getattr(self, '_todo_progress', None)
        if reminder is not None:
            schemas = reminder.project_schemas(schemas)
        return schemas

    def _active_tool_schemas(self) -> list[Any]:
        active, deferred = self.tool_discovery.split(self._eligible_tool_schemas(), self.tools, self.tool_schema_mode)
        return [schema for schema in active if schema.name != 'search_tools' or deferred
                or 'search_tools' in self.tool_discovery.loaded]

    async def _search_tools(self, args):
        return await self.tool_discovery.search(args, self._eligible_tool_schemas(), self.tools)

    async def _ensure_system_prompt(self, session: Session) -> None:
        """把装配好的系统提示写进日志（只在首次或内容变化时写）。"""
        self.tool_discovery.bind(session)
        if self.tool_schema_policy is not None:
            self.tool_schema_mode = self.tool_schema_policy()
        variables = {
            "session_id": session.id,
            "cwd": session.header.cwd or "",
            "date": time.strftime("%Y-%m-%d"),
            "language": getattr(self, "language", "zh-CN"),
            "task_read_only": self.task_read_only,
            "recovery_read_only": self.recovery_read_only,
        }
        assembly = self.prompts.assemble(variables=variables)
        _, deferred = self.tool_discovery.split(self._eligible_tool_schemas(), self.tools, self.tool_schema_mode)
        catalog = self.tool_discovery.catalog(deferred, self.tools)
        if catalog:
            assembly.sections.append(('tool-discovery:catalog', catalog))
            assembly.volatile_names.add('tool-discovery:catalog')
        self._language_policy_enabled = any(name == 'harness:language' for name, _ in assembly.sections)
        assembly.tool_names = [s.name for s in self._active_tool_schemas()]
        prefix, runtime = assembly.cache_parts(final_runtime_names=('harness:language',))
        text = assembly.render()

        catalog = ""
        # 技能目录作为一个独立片段追加
        skills = self.skills_for_session(session) if self.skills_for_session else self.skills
        if skills is not None:
            catalog = render_catalog(skills)
            if catalog:
                text = f"{text}\n\n{catalog}"
                prefix = f"{prefix}\n\n{catalog}".strip()

        def source_spans(parts):
            spans, offset = [], 0
            for name, part in parts:
                if not part.strip():
                    continue
                chunk = ('\n\n' if spans else '') + part.strip()
                spans.append({'source': name, 'start': offset, 'end': offset + len(chunk)})
                offset += len(chunk)
            return spans

        stable_parts = [(name, value) for name, value in assembly.sections if name not in assembly.volatile_names]
        if catalog:
            stable_parts.append(('skills:catalog', catalog))
        dynamic_parts = [(name, value) for name, value in assembly.sections
                         if name in assembly.volatile_names and name != 'harness:language']
        dynamic_parts.extend(assembly.contexts)
        dynamic_parts.extend((name, value) for name, value in assembly.sections
                             if name in assembly.volatile_names and name == 'harness:language')
        sources = {'stable': source_spans(stable_parts), 'runtime': source_spans(dynamic_parts)}
        # 已经有 system/message 且内容一致 → 不重复写
        latest = session.latest("system/message")
        in_surface = latest is not None and any(event.seq == latest.seq for event in session.surface)
        if in_surface and latest.data.get("text") == text and latest.data.get("cache_prefix") == prefix and latest.data.get("runtime_text") == runtime:
            return
        # 模型上下文仅保留一份当前策略；只追加的审计日志
        # 仍保留全部历史版本，供重放和检查。
        operation = ReplaceOp(start_seq=latest.seq, end_seq=latest.seq) if in_surface else AppendOp()
        await session.append("system/message", {"text": text, "cache_prefix": prefix, "runtime_text": runtime, "context_sources": sources}, surface_op=operation)
        await self._emit("system/prompt", {"session_id": session.id, "chars": len(text)})

    # ==================================================================
    # 内部：请求头
    # ==================================================================
    async def _log_request_header(
        self,
        session: Session,
        config: LlmCallConfig,
        turn: int,
        step: int,
        *,
        reason_hint: str,
    ) -> None:
        """记录一次请求配置快照。"""
        header = EpochHeader(
            config=config.to_dict(),
            tools=[s.to_dict() for s in self._active_tool_schemas()],
        )
        # 已有请求头时：内容相同则不重复记
        latest = session.latest("request/header")
        data = header.to_dict()
        if latest is not None and to_jsonable(EpochHeader.from_dict(latest.data).to_dict()) == data:
            return
        await session.append(
            "request/header",
            {**data, "reason": reason_hint, "turn": turn, "step": step},
        )

    # ==================================================================
    # 内部：消息组装
    # ==================================================================
    def _build_messages(self, session: Session, *, runtime_messages: Sequence[Any] = ()) -> list[Any]:
        """从会话日志派生本次请求的消息列表。"""
        messages = session.build_messages()
        messages.extend(self._request_runtime_messages(runtime_messages))
        return messages

    def _request_runtime_messages(self, runtime_messages: Sequence[Any] = ()) -> list[Any]:
        messages = []
        if self._stop_feedback:
            messages.append(DeveloperMessage(text=tr('python.agent.265f40ad2938')
                + json.dumps(self._stop_feedback, ensure_ascii=False)))
        if self.recovery_read_only:
            messages.append(DeveloperMessage(text=tr('python.agent.1995c0fee451')))
        elif self.task_read_only:
            messages.append(DeveloperMessage(text=tr('agent.task_read_only.instructions')))
        if self.isolated_team_coordinator:
            messages.append(DeveloperMessage(text=tr('agent.team.coordinator_write_denied'),
                                             source='isolated-team-coordinator'))
        if self.reply_max_chars is not None:
            messages.append(DeveloperMessage(text=tr('agent.team.reply_limit_instructions',
                limit=self.reply_max_chars), source='reply-character-limit'))
        if self.workspace_write_root is not None:
            messages.append(DeveloperMessage(text=tr('agent.workspace.instructions', root=self.workspace_write_root)))
        if self.budget is not None:
            budget = self.budget.snapshot()
            if budget['limit'] is not None and (budget['spent'] or budget['reserved']):
                messages.append(DeveloperMessage(text=tr('agent.execution_budget.instructions',
                    budget=json.dumps(budget, ensure_ascii=False)), source='execution-budget'))
        messages.extend(runtime_messages)
        if self._language_policy_enabled and self._language_drift_detected:
            from ..i18n import resources
            messages.append(DeveloperMessage(text=resources(self.language)['language.drift_reminder'],
                                             source='language-drift-reminder'))
        return messages

    async def _observe_response_language(self, session, assistant, source_seq, turn, step):
        """只观测供应商返回文本；后续已有步骤可提醒，不新增请求或重放工具。"""
        if not self._language_policy_enabled:
            return
        self._language_drift_detected = False
        if not assistant.thinking:
            return
        from .language_observation import observe_thinking_language
        observation = observe_thinking_language(assistant.thinking, self.language,
                                                proper_names=(*self.tools.names(), assistant.model or ''))
        self._language_drift_detected = observation['status'] == 'suspected_drift'
        await session.append('note', {'kind': 'language_observation', **observation,
            'actual_model': assistant.model, 'turn': turn, 'step': step,
            'request_id': f'req_{turn}_{step}', 'source_seq': source_seq}, ignorable=True)

    def _todo_progress_enabled(self, session: Session) -> bool:
        if self._todo_progress is None or self.recovery_read_only or self.task_read_only or self._cancel_requested:
            return False
        if self.plan_controller is not None and self.plan_controller.is_active(session):
            return False
        names = {schema.name for schema in self._active_tool_schemas()}
        return bool({'todo_write', 'todo_update'} & names)

    def _todo_initialization_for(self, session: Session) -> TodoInitialization | None:
        """建单必须有正式写工具；计划探索和可信只读执行不进入此保障。"""
        if not self._todo_progress_enabled(session) or 'todo_write' not in {
                schema.name for schema in self._active_tool_schemas()}:
            return None
        initialization = self._todo_initialization
        if initialization is not None:
            provider = getattr(self.plan_controller, 'implementation_approval', None)
            initialization.sync(provider(session) if callable(provider) else None)
        return initialization

    # ==================================================================
    # 内部：单次流式请求
    # ==================================================================
    async def _stream_once(
        self,
        session: Session,
        messages: Sequence[Any],
        config: LlmCallConfig,
        *,
        turn: int,
        step: int,
        runtime_messages: Sequence[Any] = (),
    ) -> tuple[AssistantMessage, list[dict[str, Any]]]:
        tools = self._active_tool_schemas()
        cost = self._request_cost(messages, tools)
        input_cost = cost.input_tokens
        reservation = None
        charge = None
        if self.budget is not None:
            reservation = await self.budget.acquire(input_cost, config.max_tokens)
            if reservation is None:
                raise _RequestBudgetExhausted(tr('python.agent.e16ced84712b'), code='agent/token-budget')
            config = config.merged(max_tokens=reservation.max_tokens)
        if self.context_window is not None and not hasattr(self.adapter, 'resolve_capabilities'):
            # 端点按「输入 + max_tokens」校验上下文上限：max_tokens 配置得再大，
            # 也必须按本请求的真实输入收敛，否则输入稍大就整请求 400。
            # 压缩流程保证输入低于 compact_threshold，正常路径 fit 恒为正。
            from ..llm.request_limits import context_margin
            fit = self.context_window - input_cost - context_margin(self.context_window)
            if fit > 0 and config.max_tokens is not None:
                config = config.merged(max_tokens=min(config.max_tokens, fit))
        try:
            try:
                assistant, stream_record = await self._stream_response(
                    session, messages, config, turn=turn, step=step, tools=tools, cost=cost)
            except LlmContextLengthError as exc:
                # 端点 400 自报真实窗口：学习它，把 max_tokens 收敛到窗口内，
                # 原地修正一次。必需输入仍超窗时保留历史并明确报告。
                learned = exc.details.get('limit')
                if type(learned) is not int or learned <= 0 or exc.details.get('capacity_corrections', 0) >= 1:
                    raise
                self._learn_context_window(learned, exc.details.get('base_url'), exc.details.get('model'),
                    notify=not exc.details.get('preflight'))
                from ..llm.request_limits import context_margin
                if input_cost + context_margin(learned) >= learned:
                    if reservation is not None:
                        reservation.settle(cost.total)
                        reservation = None
                    await self._maybe_compact(session, config, turn, force=True, runtime_messages=runtime_messages)
                    messages = self._build_messages(session, runtime_messages=runtime_messages)
                    replacement_cost = self._request_cost(messages, tools)
                    input_cost = replacement_cost.input_tokens
                    if input_cost + context_margin(learned) >= learned:
                        raise LlmContextLengthError(tr('python.agent.04bb15e1682a'), limit=learned,
                            model=exc.details.get('model'), base_url=exc.details.get('base_url')) from exc
                    cost = replacement_cost
                    if self.budget is not None:
                        reservation = await self.budget.acquire(input_cost, config.max_tokens)
                        if reservation is None:
                            raise _RequestBudgetExhausted(tr('python.agent.624eb8cb175d'), code='agent/token-budget')
                        config = config.merged(max_tokens=reservation.max_tokens)
                # 适配器使用同一路由重新计算上限，包含刚获知的值。
                # 此调用尚未提交任何工具执行。
                assistant, stream_record = await self._stream_response(
                    session, messages, config, turn=turn, step=step, tools=tools, cost=cost, capacity_retries=0)
            except LlmError as exc:
                # 多次网络中断却没有正文时，不继续盲发同一份长历史。
                # 只允许一次有明确缩减收益的上下文恢复，不学习或虚构模型容量。
                details = exc.details
                if not (exc.code in {'llm/request', 'llm/timeout', 'llm/progress-timeout'}
                        and exc.transient and details.get('no_text_progress') is True
                        and details.get('attempts', 0) >= 2 and self.context_auto_manage
                        and input_cost >= max(1, self.context_working_budget // 2)):
                    raise
                self._check_cancelled()
                # 摘要也是模型请求，换恢复策略不能绕过供应商要求的冷却。
                delay = retry_delay(details, 0)
                if delay:
                    await self._emit('request/progress', {'session_id': session.id,
                        'request_id': f'req_{turn}_{step}', 'generation': {
                            'phase': 'retrying', 'retry_in_seconds': delay,
                            'attempt': details.get('attempts', 0)}})
                    await asyncio.sleep(delay)
                    self._check_cancelled()
                turn_start = session.latest('turn/start')
                regions = _compaction_regions(session.surface,
                    protect_images_after=turn_start.seq if turn_start is not None else None)
                if not regions:
                    raise
                if reservation is not None:
                    reservation.settle(cost.total)
                    reservation = None
                await self._maybe_compact(session, config, turn, force=True, runtime_messages=runtime_messages,
                                          require_summary=True)
                messages = self._build_messages(session, runtime_messages=runtime_messages)
                replacement_cost = self._request_cost(messages, tools)
                reduced_input = replacement_cost.input_tokens
                if reduced_input >= input_cost:
                    raise
                await session.append('note', {'kind': 'network_context_recovery',
                    'request_id': f'req_{turn}_{step}', 'error_code': exc.code,
                    'before_tokens': input_cost, 'after_tokens': reduced_input,
                    'previous_attempts': details.get('attempts', 0)})
                input_cost = reduced_input
                previous_request_bytes = cost.request_bytes
                cost = replacement_cost
                if self.budget is not None:
                    reservation = await self.budget.acquire(input_cost, config.max_tokens)
                    if reservation is None:
                        raise _RequestBudgetExhausted(tr('python.agent.624eb8cb175d'), code='agent/token-budget')
                    config = config.merged(max_tokens=reservation.max_tokens)
                # 整理只允许一次，并继承已用的物理尝试和续接额度。
                assistant, stream_record = await self._stream_response(
                    session, messages, config, turn=turn, step=step, tools=tools, cost=cost,
                    allow_context_recovery=False, recovery_offset=details.get('recoveries', 0),
                    attempt_offset=details.get('attempts', 0),
                    request_bytes_limit=previous_request_bytes or None)
            charge = assistant.usage.total_tokens if assistant.usage is not None and assistant.usage.total_tokens > 0 else (
                input_cost + estimate_tokens([assistant]))
            return assistant, stream_record
        finally:
            if reservation is not None:
                reservation.settle(cost.settlement(charge))

    def _learn_context_window(self, limit: int, base_url=None, model=None, *, notify=True) -> None:
        """端点自报的真实窗口：更新钳制值、按比例迁移压缩阈值，并通知上层。"""
        limit = max(1, int(limit))
        if self.context_window == limit:
            return
        from ..llm.context_policy import compact_threshold
        threshold = compact_threshold(limit, self.context_working_budget, self.output_reserve)
        if notify and self.on_context_window_learned is not None:
            if base_url and model:
                self.on_context_window_learned(limit, base_url, model)
            else:
                self.on_context_window_learned(limit)
        self.context_window = limit
        self.compact_threshold = threshold

    async def _stream_response(
        self,
        session: Session,
        messages: Sequence[Any],
        config: LlmCallConfig,
        *,
        turn: int,
        step: int,
        tools: Sequence[Any],
        cost: ResponseCost,
        capacity_retries: int = 1,
        allow_context_recovery: bool = True,
        recovery_offset: int = 0,
        attempt_offset: int = 0,
        request_bytes_limit: int | None = None,
    ) -> tuple[AssistantMessage, list[dict[str, Any]]]:
        """发起一次流式请求，边收边广播，最后返回完整消息与流记录。"""
        content: list[Any] = []
        reasoning_details: list[Any] = []
        stream_record: list[dict[str, Any]] = []
        usage: TokenUsage | None = None
        stop_reason: str | None = None
        request_id = f"req_{turn}_{step}"

        request_start = await session.append(
            "request/attempt",
            {"request_id": request_id, "turn": turn, "step": step, "status": "started"},
        )
        pending_text: list[str] = []
        pending_thinking: list[str] = []
        pending_chars = text_end = thinking_end = 0
        last_checkpoint: float | None = None
        checkpoint_enabled = self.stream_checkpoints and session.commit_event is not None

        async def checkpoint(*, force: bool = False) -> None:
            nonlocal pending_chars, text_end, thinking_end, last_checkpoint
            if not checkpoint_enabled or not pending_chars:
                return
            now = self._checkpoint_clock()
            if not force and last_checkpoint is not None and pending_chars < 4096 and now - last_checkpoint < 1:
                return
            text, thinking = ''.join(pending_text), ''.join(pending_thinking)
            await session.append('note', {'kind': 'assistant_checkpoint', 'request_id': request_id,
                'request_seq': request_start.seq, 'turn': turn, 'step': step, 'model': config.model,
                'text': text, 'thinking': thinking, 'text_offset': text_end, 'thinking_offset': thinking_end, **_speaker_metadata()}, ignorable=True)
            text_end += len(text)
            thinking_end += len(thinking)
            pending_text.clear()
            pending_thinking.clear()
            pending_chars = 0
            last_checkpoint = now
        await self._emit("request/start", {"session_id": session.id, "request_id": request_id, "turn": turn, "step": step,
            'generation': {'model': config.model,
                'phase': 'waiting', 'elapsed_ms': 0, 'text_chars': 0, 'thinking_chars': 0,
                'max_tokens': config.max_tokens}})

        continuations = recovery_offset
        request_attempts = attempt_offset
        key_pool = getattr(self.adapter, 'key_pool', None)
        pool_size = getattr(key_pool, 'size', 1)
        recovery_limit = max(MAX_STREAM_CONTINUATIONS, pool_size - 1)
        routed = getattr(self.adapter, 'route_pool', None) is not None
        actual_model = config.model

        async def observed_attempt(data: dict[str, Any]) -> None:
            nonlocal request_attempts, actual_model
            if data.get('model'):
                actual_model = data['model']
            if data.get('phase') == 'waiting':
                request_attempts += 1
                data = {**data, 'attempt': request_attempts,
                        'max_attempts': max(recovery_limit + 1, request_attempts, data.get('max_attempts', 1))}
            await self._emit('request/progress', {'session_id': session.id, 'request_id': request_id, 'generation': data})

        # 现有第三方适配器可能仍实现原始的流接口签名。
        stream_options: dict[str, Any] = {}
        parameters = _adapter_parameters(self.adapter.stream)
        if 'cache_session_id' in parameters:
            stream_options['cache_session_id'] = session.id
        if 'on_request' in parameters:
            from ..session.request_traces import request_observer
            trace_request = request_observer(session, request_id=request_id)
            async def observed_request(data):
                if data['phase'] == 'started':
                    estimate = data.get('estimated_input_tokens')
                    if (self.budget is not None and cost.route_models is not None
                            and type(estimate) is int and estimate > cost.input_tokens):
                        # 普通续接会合法增加输入；仅拒绝快照以外模型突破原预留。
                        if json.loads(data['body']).get('model') not in cost.route_models:
                            raise _RequestBudgetExhausted(tr('python.agent.e16ced84712b'), code='agent/token-budget',
                                estimated_input_tokens=estimate, reserved_input_tokens=cost.input_tokens)
                    cost.begin_physical(data.get('estimated_input_tokens'))
                    size = len(data['body'].encode('utf-8'))
                    if request_bytes_limit is not None and size >= request_bytes_limit:
                        # 在传输前比较实际编码后的请求，避免估算变小但真实负载未变。
                        raise HarnessError(tr('context.compaction.noWireReduction'),
                            code='agent/compaction-capacity', before_request_bytes=request_bytes_limit,
                            after_request_bytes=size)
                    cost.request_bytes = size
                else:
                    cost.finish_physical(data)
                return await trace_request(data)
            stream_options['on_request'] = observed_request
        if 'on_attempt' in parameters:
            stream_options['on_attempt'] = observed_attempt
        if 'capacity_retries' in parameters:
            stream_options['capacity_retries'] = capacity_retries

        request_messages: list[Any] = list(messages)

        async def consume(stream_messages: Sequence[Any]) -> None:
            nonlocal usage, stop_reason, pending_chars
            async with aclosing(self.adapter.stream(stream_messages, config=config, tools=tools, **stream_options)) as response_stream:
                while True:
                    try:
                        event = await anext(response_stream)
                    except StopAsyncIteration:
                        break
                    cost.observe(event)
                    stream_record.append(event.to_dict())

                    if event.kind == 'start':
                        await observed_attempt({'phase': 'generating'})
                    elif event.kind == "text":
                        if checkpoint_enabled:
                            pending_text.append(event.text)
                            pending_chars += len(event.text)
                        # 合并相邻文本块
                        if content and isinstance(content[-1], TextBlock):
                            content[-1] = TextBlock(text=content[-1].text + event.text)
                        else:
                            content.append(TextBlock(text=event.text))
                        await checkpoint(force=text_end == 0)
                        await self._emit(
                            "assistant/delta",
                            {"session_id": session.id, "request_id": request_id, "type": "text", "delta": event.text},
                        )
                    elif event.kind == "thinking":
                        if checkpoint_enabled:
                            pending_thinking.append(event.thinking)
                            pending_chars += len(event.thinking)
                        merge_thinking_event(content, event)
                        await checkpoint(force=thinking_end == 0)
                        await self._emit(
                            "assistant/delta",
                            {"session_id": session.id, "request_id": request_id, "type": "thinking", "delta": event.thinking},
                        )
                    elif event.kind == 'signature':
                        merge_thinking_event(content, event)
                    elif event.kind == 'reasoning_details':
                        reasoning_details.extend(event.reasoning_details)
                    elif event.kind == "tool_use" and event.tool_use is not None:
                        content.append(event.tool_use)
                    elif event.kind == "usage":
                        usage = event.usage
                    elif event.kind == "done":
                        stop_reason = event.stop_reason
                        # 这是适配器的终止事件，位于用量统计和完整工具调用之后。
                        # 不再请求流结束，直接关闭；自定义适配器
                        # 可能保持传输连接不关闭。
                        break
                await checkpoint(force=True)

        while True:
            try:
                await consume(request_messages)
                break
            except asyncio.CancelledError:
                await self._preserve_partial_response(session, content, config, request_id, turn, step, 'stopped')
                await session.append('request/attempt', {'request_id': request_id, 'status': 'cancelled'})
                raise
            except Exception as exc:
                # 仅恢复本次模型请求，不重放整个回合已经完成的工具。
                # 文本可作为续接锚点；仅有思考内容时，需新建请求，
                # 不携带未完成的思考内容。
                partial_text = ''.join(block.text for block in content if isinstance(block, TextBlock))
                has_tool_fragment = any(isinstance(block, ToolUseBlock) for block in content)
                details = getattr(exc, 'details', {})
                # 凭据被拒绝也可能发生在思考或文本已经开始之后。
                # 适配器已经将凭据池切换到下一个密钥。
                recoverable = getattr(exc, 'transient', False) or (
                    pool_size > 1 and details.get('partial_output') is True
                    and details.get('status') in {401, 402, 403})
                # 共享适配器存在路由池，不代表本次请求经过路由；订阅通道优先直连。
                # 只有本次异常明确携带路由决策时，才覆盖瞬时故障分类。
                if routed and 'route_retry' in details:
                    recoverable = details.get('route_retry') is True
                    if recoverable:
                        # 丢弃失败响应中的工具片段，它们从未执行。
                        content[:] = [b for b in content if isinstance(b, TextBlock)]
                        reasoning_details.clear()
                        has_tool_fragment = False
                attempts = max(request_attempts, continuations + 1)
                if isinstance(exc, HarnessError):
                    exc.details.update(attempts=attempts, recoveries=continuations,
                                       max_recoveries=recovery_limit, no_text_progress=not partial_text.strip())
                # 同一长历史已经重试且仍只有思考时，提前换策略，而不是耗尽额度后再盲发。
                context_recovery = (allow_context_recovery and recoverable and not partial_text.strip()
                    and not has_tool_fragment and continuations >= 1 and self.context_auto_manage
                    and cost.input_tokens >= max(1, self.context_working_budget // 2)
                    and getattr(exc, 'code', None) in {'llm/request', 'llm/timeout', 'llm/progress-timeout'})
                if context_recovery:
                    turn_start = session.latest('turn/start')
                    context_recovery = bool(_compaction_regions(session.surface,
                        protect_images_after=turn_start.seq if turn_start is not None else None))
                if (not recoverable or has_tool_fragment
                        or continuations >= recovery_limit
                        or self._cancel_requested or context_recovery):
                    if isinstance(exc, HarnessError) and recoverable and continuations >= recovery_limit:
                        exc.details.update(route_retry=False, request_recovery_exhausted=True)
                    await self._preserve_partial_response(session, content, config, request_id, turn, step, 'failed')
                    await session.append(
                        "request/attempt",
                        {"request_id": request_id, "status": "error", "error": str(exc), "attempts": attempts},
                    )
                    raise
                continuations += 1
                await session.append(
                    "request/attempt",
                    {"request_id": request_id, "status": "error", "error": str(exc),
                     "attempts": attempts,
                     "continuation": continuations,
                     "recovery": "continue_text" if partial_text.strip() else "retry_request"},
                )
                try:
                    delay = retry_delay(details, 0)
                except LlmError:
                    await self._preserve_partial_response(session, content, config, request_id, turn, step, 'failed')
                    raise
                await observed_attempt({'phase': 'retrying', 'attempt': attempts,
                                        'max_attempts': max(recovery_limit + 1, attempts + 1),
                                        'elapsed_ms': 0, 'idle_ms': 0, 'retry_in_seconds': delay})
                if delay:
                    try:
                        await asyncio.sleep(delay)
                    except asyncio.CancelledError:
                        await self._preserve_partial_response(session, content, config, request_id, turn, step, 'stopped')
                        await session.append('request/attempt', {'request_id': request_id, 'status': 'cancelled'})
                        raise
                if partial_text.strip():
                    from ..llm.types import UserMessage
                    request_messages = [
                        *messages,
                        AssistantMessage(content=(TextBlock(text=partial_text),), model=config.model),
                        UserMessage(text=tr('python.agent.fbd4be605711')),
                    ]
                else:
                    await checkpoint(force=True)
                    content.clear()
                    reasoning_details.clear()
                    usage = None
                    stop_reason = None
                    request_messages = list(messages)

        # 逻辑请求的已报告用量包含其全部物理尝试；缺失项仍只留在本地预算估算。
        if cost.physical_usage:
            usage = sum(cost.physical_usage.values(), TokenUsage())
        await session.append(
            "request/attempt",
            {
                "request_id": request_id,
                "status": "done",
                "stop_reason": stop_reason,
                "usage": usage.to_dict() if usage else None,
            },
        )
        await self._emit(
            "request/end",
            {"session_id": session.id, "request_id": request_id, "usage": usage.to_dict() if usage else None},
        )

        assistant = AssistantMessage(
            content=tuple(content),
            model=actual_model,
            stop_reason=stop_reason,
            usage=usage,
            reasoning_details=tuple(reasoning_details),
        )
        return assistant, stream_record

    async def _preserve_partial_response(self, session: Session, content: list[Any], config: LlmCallConfig,
                                         request_id: str, turn: int, step: int, status: str) -> None:
        # 部分工具调用不是可执行证据；只保留已经交付给用户的文本，
        # 并放在模型有序消息表面之外。
        text = ''.join(block.text for block in content if isinstance(block, TextBlock))
        thinking = ''.join(block.thinking for block in content if isinstance(block, ThinkingBlock))
        if not (text or thinking) or session.persistence_error:
            return
        data = {'kind': 'assistant_partial', 'status': status, 'text': text, 'thinking': thinking,
                'model': config.model, 'request_id': request_id, 'turn': turn, 'step': step, **_speaker_metadata()}
        event = await session.append('note', data, ignorable=True)
        await self._emit('assistant/partial', {'session_id': session.id, **data,
                                             'source_seq': event.seq, 'source_time': event.time})

    # ==================================================================
    # 内部：工具执行
    # ==================================================================
    async def _execute_tools(
        self,
        session: Session,
        calls: Sequence[ToolUseBlock],
        *,
        turn: int,
        step: int,
    ) -> None:
        """执行一批工具调用，把结果写进日志。"""
        from .language import current_language
        language = getattr(self, 'language', current_language.get())
        run_ctx = ToolRunContext(
            call_id="",
            session_id=session.id,
            cwd=session.header.cwd,
            turn=turn,
            step=step,
            services={
                "language": language,
                "skills": self.skills,
                "registry": self.tools,
                "tool_search": self._search_tools,
                "jobs": self.tools.services.get("jobs"),
                "automations": self.tools.services.get("automations"),
                "bus": self.bus,
                # 会话对象：需要读/写会话日志的工具（如计划模式切换）
                # 只有 session_id 不够，必须能拿到 Session 本身。
                "session": session,
                # 只使用宿主绑定的运行时归属，历史分叉的 parent_session
                # 不代表私有子代理，不能据此把表单送回原聊天。
                "parent_session_id": self.parent_session_id,
                "root_session_id": self.root_session_id,
                "team_goal": getattr(self, "team_goal", None),
                "team_goal_writer": getattr(self, "team_goal_writer", False),
                "identity": getattr(self, "identity", None),
                "request_id": getattr(self, "request_id", None),
                "run_id": self.run_id,
                "recovery_read_only": self.recovery_read_only,
                "task_read_only": self.task_read_only,
                "workspace_write_root": self.workspace_write_root,
                "isolated_team_coordinator": lambda: self.isolated_team_coordinator,
                "plan_controller": self.plan_controller,
            },
        )

        def output_reporter(current_call: ToolUseBlock):
            # 每个调用独占状态；不能捕获随后会改变的 for 循环变量。
            output_seq = 0
            output_totals = {"stdout": 0, "stderr": 0}
            active = True

            async def report_output(output: dict[str, Any]) -> None:
                nonlocal output_seq
                if not active:
                    return
                output_seq += 1
                delta = {}
                for stream in output_totals:
                    text = output[stream]
                    dropped = output[stream + "_dropped"]
                    offset = max(output_totals[stream], dropped)
                    delta[stream] = text[max(0, offset - dropped):]
                    delta[stream + "_offset"] = offset
                    output_totals[stream] = dropped + len(text)
                payload = {**delta, "session_id": session.id, "call_id": current_call.id,
                           "name": current_call.name, "turn": turn, "step": step,
                           "run_id": self.run_id, "output_seq": output_seq,
                           **({"encoding_errors": output["encoding_errors"]} if output.get("encoding_errors") else {})}
                committed = await session.append("tool/output", payload, ignorable=True)
                await self._emit("tool/output", {**payload, "event_seq": committed.seq})

            def close_output() -> None:
                nonlocal active
                active = False

            return report_output, close_output

        active_tools: dict[str, str] = {}
        todo_deferred: set[str] = set()

        def steering_check(_definition, _args, _ctx):
            # 接受状态提交后才在此处可见；完成已经开始的工具，
            # 但在模型读取新引导前，不启动已经过时的调用。
            # 为满足供应商工具调用配对约束，所有结果必须位于
            # 引导用户消息之前。
            if self._has_pending_steering(session, turn):
                return (tr('python.agent.622aea56a832'))
            return None

        async def execute_one(call):
            self._check_cancelled()
            deferred_by_steering = False
            execution_admitted = False

            def call_steering_check(definition, args, ctx):
                nonlocal deferred_by_steering
                reason = steering_check(definition, args, ctx)
                deferred_by_steering = deferred_by_steering or reason is not None
                return reason

            def call_execution_check(definition, args, ctx):
                nonlocal execution_admitted
                self._check_cancelled()
                reason = call_steering_check(definition, args, ctx)
                # 后续权限检查同样同步执行；调用前保守标记，
                # 此处拒绝时返回正常配对的结果。
                execution_admitted = reason is None
                return reason

            # 先落 tool/call，让 UI 立刻显示"正在调用 X"
            definition = self.tools.get(call.name) if self.tools.has(call.name) else None
            await session.append("tool/call", {"call_id": call.id, "name": call.name,
                "description": definition.schema.resolve_description(language).description if definition else "",
                "input": call.input, "status": "running", "turn": turn, "step": step,
                "run_id": self.run_id,
                "effect": definition.effect if definition else 'unknown',
                "idempotent": definition.idempotent if definition else False})
            await self._emit("tool/start", {"session_id": session.id, "call_id": call.id,
                "name": call.name, "input": call.input, "turn": turn})
            report_output, close_output = output_reporter(call)
            ctx = ToolRunContext(
                call_id=call.id,
                session_id=session.id,
                cwd=session.header.cwd,
                turn=turn,
                step=step,
                services={**run_ctx.services, "tool_output": report_output},
                execution_checks=(call_execution_check,),
            )
            self.status.current_tool = call.name
            active_tools[call.id] = call.name
            try:
                self._check_cancelled()
                deferred = call_steering_check(definition, call.input, ctx)
                if deferred:
                    outcome = ToolResult(content=[TextBlock(text=deferred)], is_error=True,
                        display={'steering_deferred': True}, progress=False)
                else:
                    initialization = self._todo_initialization_for(session)
                    denial = initialization.denial(session.latest('todo/write'), definition) if initialization is not None else None
                    if denial is not None:
                        todo_deferred.add(call.id)
                        outcome = ToolResult(content=[TextBlock(text=json.dumps(denial, ensure_ascii=False))],
                            is_error=True, display=denial, progress=False)
                    else:
                        outcome = await self.tools.execute_call(call, ctx)
                if deferred_by_steering:
                    outcome.display = {**(outcome.display if isinstance(outcome.display, Mapping) else {}),
                                       'steering_deferred': True}
                    outcome.progress = False
            except asyncio.CancelledError:
                if not execution_admitted:
                    self._unstarted_call_ids.add(call.id)
                raise
            finally:
                close_output()
                active_tools.pop(call.id, None)
                self.status.current_tool = next(iter(active_tools.values()), None)

            text = "\n".join(b.text for b in outcome.content)
            from ..session.tool_projection import project_tool_result
            projection = project_tool_result(text, name=call.name, call_id=call.id,
                max_chars=self.tool_result_max_chars)
            skill_loads = []
            if call.name == 'skill' and not outcome.is_error:
                from ..skills.parser import SkillMetadata
                if isinstance(outcome.data, SkillMetadata):
                    from ..skills.context import snapshot, spans
                    skill_loads = spans(text, [snapshot(outcome.data)])
            # 提交结果前，先持久化有界的私有文件证据。
            # 此处失败不能把已经执行的写入变成可重试错误。
            if not outcome.is_error and call.name in {'write', 'edit'} and isinstance(outcome.data, Mapping):
                evidence = outcome.data.get('_file_change')
                if isinstance(evidence, Mapping):
                    display = dict(outcome.display) if isinstance(outcome.display, Mapping) else {}
                    try:
                        display['snapshot'] = session.file_evidence.capture(evidence)
                    except Exception:
                        logger.exception(tr('log.af5409792c3a'))
                        display['snapshot_unavailable'] = tr('python.agent.44386ddfae76')
                    outcome.display = display
            # tool/result 是 surface 事件：结果会回灌给模型
            result_event = await session.append(
                "tool/result",
                {
                    "call_id": call.id,
                    "name": call.name,
                    "turn": turn,
                    "results": [
                        {"tool_use_id": call.id, "content": text, "is_error": outcome.is_error, **projection}
                    ],
                    "display": outcome.display,
                    **({"skill_loads": skill_loads} if skill_loads else {}),
                    "duration_ms": outcome.duration_ms,
                    # 私有可信证据，不从模型文本重建。
                    "file_observation": (outcome.data.get('_fs_observation')
                        if isinstance(outcome.data, Mapping) else None),
                },
                surface_op=AppendOp(),
            )
            if self._browser_verification is not None:
                self._browser_verification.observe(call, outcome, source_seq=result_event.seq)
            if self._todo_progress is not None:
                self._todo_progress.observe_result(call, outcome, session)
            if self.bus is not None and call.name == 'skill' and not outcome.is_error and isinstance(outcome.display, Mapping) and outcome.display.get('skill'):
                await self.bus.emit('skill/loaded', session.id, {'session_id': session.id,
                    'name': outcome.display['skill'], 'path': outcome.display.get('path'),
                    'call_id': call.id, 'result_seq': result_event.seq})
            # 同步落 tool/call 的终态，供 UI 渲染结果
            await session.append(
                "tool/call",
                {
                    "call_id": call.id,
                    "name": call.name,
                    "status": "error" if outcome.is_error else "done",
                    "turn": turn,
                    "duration_ms": outcome.duration_ms,
                    "display": outcome.display,
                },
            )
            await self._emit(
                "tool/end",
                {
                    "session_id": session.id,
                    "call_id": call.id,
                    "name": call.name,
                    "is_error": outcome.is_error,
                    "result_seq": result_event.seq,
                    "turn": turn,
                    "content": text,
                    "display": outcome.display,
                    "duration_ms": outcome.duration_ms,
                },
            )

            return definition, outcome, text

        pending_notices = []
        successful_work = False
        successful_write = False
        list_attempted = False

        async def record_progress(call, result):
            nonlocal successful_work, successful_write, list_attempted
            definition, outcome, _text = result
            list_attempted = list_attempted or call.name in {'todo_write', 'todo_update', 'todo_bind'}
            successful_work = successful_work or is_business_result(call.name, outcome)
            successful_write = successful_write or (
                is_business_result(call.name, outcome) and definition is not None
                and definition.effect in {'workspace_write', 'external_write'})
            if call.id in todo_deferred:
                # 建单拒绝由独立的有限恢复预算处理，不把未执行工具算作空转周期。
                return
            if outcome.progress is True and not outcome.is_error:
                reminder = self._reminder_for()
                if reminder is not None:
                    reminder.reset(session.id)
                return
            # 4.6) 重复调用提醒（咨询型护栏，不拦截）。
            # 刻意在结果落库 **之后** 计数，且不区分 is_error：被 guard 拒绝、
            # 参数非法这类"没跑起来"的调用同样计入——模型死磕一个被拒绝的
            # 调用恰恰是最该被提醒的场景。
            reminder = self._reminder_for()
            if reminder is not None:
                notice = reminder.observe(session.id, call.name, call.input)
                if notice:
                    pending_notices.append((call.name, notice))

        async def execute_reads(batch):
            tasks = [asyncio.create_task(execute_one(call)) for call in batch]
            batch_result = asyncio.gather(*tasks)
            try:
                await asyncio.wait((batch_result,))
                results = batch_result.result()
            except BaseException as primary:
                # 回合收尾前等待所有已启动操作结束，已投递的取消不重复打断finally。
                for task in tasks:
                    if not task.done() and not task.cancelling():
                        task.cancel()
                cleanup = asyncio.gather(*tasks, return_exceptions=True)
                try:
                    await join_cleanup(cleanup)
                finally:
                    # 原批次聚合也属于本次等待；取回终态，避免取消诊断悬空。
                    if batch_result.done() and not batch_result.cancelled():
                        batch_result.exception()
                    if cleanup.done() and not cleanup.cancelled():
                        failures = [error for error in cleanup.result()
                                    if isinstance(error, BaseException)
                                    and not isinstance(error, asyncio.CancelledError) and error is not primary]
                        if failures:
                            raise_cleanup_errors([primary, *failures])
                raise
            for call, result in zip(batch, results):
                await record_progress(call, result)

        reads = []
        for call in calls:
            definition = self.tools.get(call.name) if self.tools.has(call.name) else None
            if definition is not None and definition.effect == 'read' and definition.concurrency_safe:
                reads.append(call)
                if len(reads) == 4:
                    await execute_reads(reads)
                    reads.clear()
            else:
                if reads:
                    await execute_reads(reads)
                    reads.clear()
                await record_progress(call, await execute_one(call))
        if reads:
            await execute_reads(reads)

        # 每个完整提交的调用批次只计数一次，不在结果之间计数。
        if self._todo_progress_enabled(session):
            self._todo_progress.observe_batch(self._todo_progress.selected_event(session), successful_work=successful_work)
        initialization = self._todo_initialization_for(session)
        if initialization is not None:
            initialization.observe_batch(session.latest('todo/write'), successful_write=successful_write,
                                         list_attempted=list_attempted)

        # 一条助手消息中的全部工具调用结果必须先于下一条用户消息，
        # 包括建议性提醒；不能打断一个调用批次。
        for name, notice in pending_notices:
            await session.append(
                "user/message",
                {"text": notice, "source": "repeat-tool-reminder"},
                surface_op=AppendOp(),
            )
            await self._emit("notice/repeat-tool", {"session_id": session.id, "name": name, "text": notice})

    # ==================================================================
    # 内部：上下文压缩
    # ==================================================================
    def _request_cost(self, messages, tools):
        """预留上界和模型集合来自同一快照，不租用线路等待预算。"""
        planner = getattr(self.adapter, 'request_budget_plan', None)
        if callable(planner):
            plan = planner(messages, tools)
            if (isinstance(plan, tuple) and len(plan) == 2 and type(plan[0]) is int and plan[0] >= 0
                    and (plan[1] is None or isinstance(plan[1], frozenset) and all(isinstance(name, str) for name in plan[1]))):
                return ResponseCost(plan[0], route_models=plan[1])
        return ResponseCost(self._request_input_cost(messages, tools))

    def _request_input_cost(self, messages, tools):
        """预算使用当前线路的保守上界，自定义适配器保持原投影回退。"""
        estimate = getattr(self.adapter, 'estimate_budget_context', None)
        if callable(estimate):
            value = estimate(messages, tools)
            if type(value) is int and value >= 0:
                return value
        return self._estimate_tokens(messages) + self._estimate_tokens(tools)

    def _estimate_tokens(self, messages):
        """预算与展示共用当前适配器实际思考传输口径。"""
        return estimate_tokens(messages, reasoning=reasoning_mode(self.adapter))

    def _context_capacity(self, session: Session, runtime_messages=(), *, tool_tokens=None):
        """普通压缩与无模型精简共享同一输入口径和容量快照。"""
        message_tokens = self._estimate_tokens(session.build_messages())
        runtime_tokens = self._estimate_tokens(self._request_runtime_messages(runtime_messages))
        if tool_tokens is None:
            tool_tokens = self._estimate_tokens(self._active_tool_schemas())
        total = message_tokens + runtime_tokens + tool_tokens
        return {'version': 1, 'message_tokens': message_tokens, 'runtime_tokens': runtime_tokens,
                'tool_tokens': tool_tokens, 'input_tokens': total, 'context_window': self.context_window,
                'compact_threshold': self.compact_threshold,
                'remaining_tokens': self.context_window - total if self.context_window is not None else None}

    async def _project_tool_history(self, session: Session, turn: int, *, runtime_messages=(), trigger='manual') -> int:
        """在已持有会话锁的安全边界精简已有结果；不请求模型或重跑工具。"""
        from ..session.tool_projection import plan_tool_result_projections, apply_tool_result_projections
        if self.context_policy is not None:
            for name, value in self.context_policy().items():
                setattr(self, name, value)
        changes = plan_tool_result_projections(session, self.tool_result_max_chars)
        if not changes:
            return 0
        self._check_cancelled()
        before = self._context_capacity(session, runtime_messages)
        count = sum(change.results for change in changes)
        start = await session.append('compaction/start', {'turn': turn, 'stage': 'tool-result-projection',
            'trigger': trigger, 'approx_tokens': before['input_tokens'], 'capacity_before': before,
            'planned_results': count, 'pruned_results': 0, 'chars_removed': 0})
        metrics = {'turn': turn, 'compaction_id': start.seq}
        try:
            await self._emit('compaction/start', {'session_id': session.id, **start.data, 'compaction_id': start.seq})
            await apply_tool_result_projections(session, changes, check_cancelled=self._check_cancelled)
            self._check_cancelled()
            metrics.update(after_prune=True, skipped=True, before_approx_tokens=before['input_tokens'],
                           after_approx_tokens=self._context_capacity(session, runtime_messages)['input_tokens'])
        except (Exception, asyncio.CancelledError) as exc:
            metrics.update({'cancelled': True} if isinstance(exc, asyncio.CancelledError) else {'error': str(exc)})
            raise
        finally:
            # 取消可能发生在落盘成功、监听器尚未返回时，以事件事实结算已提交部分。
            committed = {source for event in session.events[start.seq + 1:] if event.type == 'tool/result'
                         for source in event.source_event_seqs or ()}
            applied = [change for change in changes if change.seq in committed]
            metrics.update(pruned_results=sum(change.results for change in applied),
                           chars_removed=sum(change.chars_before - change.chars_after for change in applied))
            metrics['capacity_after'] = self._context_capacity(session, runtime_messages)
            await session.append('compaction/end', metrics)
            await self._emit('compaction/end', {'session_id': session.id, **metrics})
        return count

    async def _maybe_compact(self, session: Session, config: LlmCallConfig, turn: int, *, force=False,
                             runtime_messages: Sequence[Any] = (), manual=False, restart=False,
                             require_summary=False) -> None:
        """上下文超阈值时压缩历史。

        策略：让模型把"较早的对话"总结成一条摘要，用 ``replace`` 操作
        替换掉那段表面节点。压缩本身也是一次普通模型请求。
        """
        self._check_cancelled()
        from .compaction import recover_committed
        if await recover_committed(session, self._emit):
            return
        if self.context_policy is not None:
            for name, value in self.context_policy().items():
                setattr(self, name, value)
        if hasattr(self.adapter, 'resolve_capabilities'):
            from ..llm.capabilities import minimum
            caps = await self.adapter.resolve_capabilities(config.model)
            self.context_window = minimum(caps.get('context_window'), getattr(self.adapter, 'context_window', None))
        from ..llm.context_policy import compact_threshold
        self.compact_threshold = compact_threshold(self.context_window, self.context_working_budget, self.output_reserve)
        self._check_cancelled()
        # 使用与普通请求相同的输入组成；工具结构和运行时反馈
        # 同样占用上下文窗口，虽然它们不是可替换的对话历史。
        # 候选区间预检沿用当前定义快照，不预先假定未来可释放工具；
        # 实际容量回执则重新读取提交后的消息与定义工作集。
        tool_tokens = self._estimate_tokens(self._active_tool_schemas())

        def input_capacity():
            return self._context_capacity(session, runtime_messages)

        capacity_before = input_capacity()
        approx_tokens = capacity_before['input_tokens']
        if not self.context_auto_manage and not manual:
            if force or (self.context_window is not None and approx_tokens >= self.compact_threshold):
                raise HarnessError(tr('context.compaction.disabled'), code='agent/compaction-required')
            return
        trigger = 'manual' if manual else 'forced' if force else 'threshold'
        threshold = self.compact_threshold if self.compact_threshold is not None else float("inf")

        # 新预算首次应用到这个历史世代时补齐已有结果；普通后续请求不反复改写前缀。
        projection_policy = (session.id, session.history_generation, self.tool_result_max_chars)
        if self._tool_projection_policy != projection_policy or force or approx_tokens >= threshold:
            projected = await self._project_tool_history(session, turn, runtime_messages=runtime_messages, trigger=trigger)
            self._tool_projection_policy = projection_policy
            if projected:
                capacity_before = input_capacity()
                approx_tokens = capacity_before['input_tokens']
                if approx_tokens < threshold and not require_summary and not restart:
                    return

        # 先做一次**无模型调用**的过重工具结果裁剪。裁剪后若已回到阈值之内，
        # 就完全省掉这次摘要
        # 请求——长编码会话里这往往是最大的一笔省。
        if self.tool_result_pruner is not None and (force or approx_tokens >= threshold):
            # 每一步都改写旧工具结果，会使原本可复用的历史前缀失效。
            # 仅在上下文容量紧张时裁剪；无论是否裁剪，
            # 都保留原始的只追加证据。
            eligible = _compaction_head(session.surface, keep_tail=12)
            prune_result = await self.tool_result_pruner.prune_session(session, eligible_seqs={event.seq for event in eligible})
            if prune_result.changed:
                logger.info(
                    tr('log.8711845c53a4'),
                    session.id,
                    len(prune_result.pruned),
                    prune_result.chars_removed,
                )
                prune_start = await session.append(
                    "compaction/start",
                    {
                        "turn": turn,
                        "approx_tokens": approx_tokens,
                        "pruned_results": len(prune_result.pruned),
                        "chars_removed": prune_result.chars_removed,
                        "stage": "tool-result-prune",
                        "capacity_before": capacity_before,
                        "trigger": trigger,
                    },
                )
                await self._emit("compaction/start", {"session_id": session.id, **prune_start.data,
                    "compaction_id": prune_start.seq})
                await self._emit(
                    "compaction/prune",
                    {
                        "session_id": session.id,
                        "pruned": len(prune_result.pruned),
                        "chars_removed": prune_result.chars_removed,
                    },
                )
                capacity_after = input_capacity()
                approx_tokens = capacity_after['input_tokens']
                metrics = {"turn": turn, "skipped": True, "after_prune": True,
                    "compaction_id": prune_start.seq,
                    "before_approx_tokens": capacity_before['input_tokens'], "after_approx_tokens": approx_tokens,
                    "capacity_after": capacity_after}
                await session.append("compaction/end", metrics)
                await self._emit("compaction/end", {"session_id": session.id, **metrics})
                # 网络故障恢复的目标是缩减失败负载，不能用常规预算阈值提前结束。
                if approx_tokens < threshold and not require_summary:
                    return
                capacity_before = capacity_after

        if not force and approx_tokens < threshold:
            return

        logger.info(tr('log.78418f071ef7'), session.id, approx_tokens)
        start_event = await session.append("compaction/start", {"turn": turn, "approx_tokens": approx_tokens,
            "capacity_before": capacity_before, "trigger": trigger})
        await self._emit("compaction/start", {"session_id": session.id, "turn": turn, "approx_tokens": approx_tokens,
            "compaction_id": start_event.seq})

        if self.lifecycle_hook is not None:
            try:
                await self.lifecycle_hook(self, session, "PreCompact", turn, self.status.step, {"trigger": trigger})
            except (Exception, asyncio.CancelledError) as exc:
                # 前置处理器停止或取消时尚未进入摘要事务，也必须结束该次整理证据。
                metrics = {"turn": turn, "compaction_id": start_event.seq, "capacity_after": input_capacity()}
                if isinstance(exc, asyncio.CancelledError):
                    metrics['cancelled'] = True
                else:
                    metrics['error'] = str(exc)
                await session.append('compaction/end', metrics)
                await self._emit('compaction/end', {'session_id': session.id, **metrics})
                raise

        # 固定系统和开发者指令，并保留至少十二个近期节点。
        # 两端都必须避开尚未完成的工具调用批次。
        turn_start = session.latest('turn/start')
        surface_snapshot = tuple(session.surface)
        source_version = tuple(event.seq for event in surface_snapshot)
        regions = _compaction_regions(surface_snapshot, keep_tail=12,
                                      protect_images_after=turn_start.seq if turn_start is not None else None)
        from .compaction_selection import select_region
        import threading
        selection_cancelled = threading.Event()
        try:
            # 预检只读不可变快照；大量历史的多区间估算不占用事件循环。
            head, selection = await asyncio.to_thread(select_region, surface_snapshot, regions,
                session.build_messages, self._estimate_tokens, cancelled=selection_cancelled,
                stop_requested=lambda: self.cancelled)
            self._check_cancelled()
            if source_version != tuple(event.seq for event in session.surface):
                raise HarnessError(tr('context.compaction.stale'), code='agent/compaction-stale')
        except (Exception, asyncio.CancelledError) as exc:
            selection_cancelled.set()
            metrics = {'turn': turn, 'compaction_id': start_event.seq, 'capacity_after': input_capacity()}
            if isinstance(exc, asyncio.CancelledError):
                metrics['cancelled'] = True
            else:
                metrics['error'] = str(exc)
            await session.append('compaction/end', metrics)
            await self._emit('compaction/end', {'session_id': session.id, **metrics})
            raise
        if len(head) < 3:
            metrics = {"turn": turn, "skipped": True, 'selection': selection,
                "capacity_after": input_capacity(), "compaction_id": start_event.seq}
            await session.append("compaction/end", metrics)
            await self._emit("compaction/end", {"session_id": session.id, **metrics})
            if not manual:
                raise HarnessError(tr('context.compaction.protected'), code='agent/compaction-capacity')
            return

        try:
            head_seqs = {event.seq for event in head}

            def projected_tokens(text):
                node = replace(head[0], type='user/message', data={
                    'text': f"<conversation_summary>\n{text}\n</conversation_summary>", 'source': 'compaction'})
                projected = [node if event.seq == head[0].seq else event for event in session.surface
                             if event.seq not in head_seqs or event.seq == head[0].seq]
                return (self._estimate_tokens(session.build_messages(surface=projected))
                        + self._estimate_tokens(self._request_runtime_messages(runtime_messages)) + tool_tokens)

            # 先检查所有可压缩区间都移除后的下界，避免不可满足的预算消耗摘要请求。
            removable = {event.seq for region in regions for event in region}
            required = [event for event in session.surface if event.seq not in removable]
            required_tokens = (self._estimate_tokens(session.build_messages(surface=required))
                               + self._estimate_tokens(self._request_runtime_messages(runtime_messages)) + tool_tokens)
            if required_tokens >= threshold:
                raise HarnessError(tr('context.compaction.protected'), code='agent/compaction-capacity')
            self._compaction_restart = restart
            try:
                summary = await self._summarize(head, config, session=session, compaction_id=start_event.seq)
            finally:
                self._compaction_restart = False
            self._check_cancelled()
            if not summary.strip():
                raise HarnessError(tr('context.compaction.noReduction'), code='agent/compaction-capacity')
            if source_version != tuple(event.seq for event in session.surface):
                raise HarnessError(tr('context.compaction.stale'), code='agent/compaction-stale')
            projected_total = projected_tokens(summary)
            if projected_total >= approx_tokens:
                raise HarnessError(tr('context.compaction.noReduction'), code='agent/compaction-capacity')
            # 先记录摘要（log-only 事件，不进表面）
            from ..session.compaction_policy import SUMMARY_VERSION
            await session.append(
                "compaction/summary",
                {
                    "turn": turn,
                    "summary": summary,
                    "shadowed_seqs": [e.seq for e in head],
                    "shadowed_count": len(head),
                    "approx_tokens": approx_tokens,
                    "summary_policy_version": SUMMARY_VERSION,
                    "retained_nodes": len(session.surface) - len(head),
                    'selection': selection,
                },
            )
            # 再用一条摘要消息**替换**被压缩的表面区间（消息表面替换）。
            # head 是连续的表面节点区间，用 ReplaceOp 一次性替换，
            # source_event_seqs 记录被覆盖的原始事件，便于 UI 还原。
            from ..session.events import ReplaceOp

            self._check_cancelled()
            if source_version != tuple(event.seq for event in session.surface):
                raise HarnessError(tr('context.compaction.stale'), code='agent/compaction-stale')
            await session.append(
                "user/message",
                {"text": f"<conversation_summary>\n{summary}\n</conversation_summary>", "source": "compaction", "compaction_id": start_event.seq},
                surface_op=ReplaceOp(start_seq=head[0].seq, end_seq=head[-1].seq),
                source_event_seqs=[e.seq for e in head],
            )
        except asyncio.CancelledError:
            metrics = {"turn": turn, "cancelled": True, "capacity_after": input_capacity(), "compaction_id": start_event.seq}
            await session.append("compaction/end", metrics)
            await self._emit("compaction/end", {"session_id": session.id, **metrics})
            raise
        except Exception as exc:
            self._compaction_restart = False
            logger.warning(tr('log.04139d775cf9'), exc)
            # 失败也保留压缩序号，使落盘与实时事件关联到同一次压缩。
            metrics = {"turn": turn, "error": str(exc), "capacity_after": input_capacity(),
                       "compaction_id": start_event.seq}
            await session.append("compaction/end", metrics)
            await self._emit("compaction/end", {"session_id": session.id, **metrics})
            # 每一步之前重复尝试失败的摘要，会消耗词元而无法缩减上下文。
            # 此时保留原始历史并停止。
            raise

        capacity_after = input_capacity()
        after_tokens = capacity_after['input_tokens']
        metrics = {"turn": turn, "ok": True, "before_approx_tokens": approx_tokens,
                   "after_approx_tokens": after_tokens, "shadowed_count": len(head),
                   'selection': selection,
                   "summary_policy_version": SUMMARY_VERSION, "capacity_after": capacity_after, "compaction_id": start_event.seq}
        await session.append("compaction/end", metrics)
        await self._emit("compaction/end", {"session_id": session.id, **metrics})
        if self.lifecycle_hook is not None:
            await self.lifecycle_hook(self, session, "PostCompact", turn, self.status.step, {"trigger": trigger})
        # 图片批次将旧历史分成多个连续区间；每次提交有收益的摘要后继续整理。
        if after_tokens >= threshold:
            await self._maybe_compact(session, config, turn, force=force,
                                      runtime_messages=runtime_messages, manual=manual, restart=restart,
                                      require_summary=require_summary)

    async def _summarize(self, events: Sequence[Any], config: LlmCallConfig, *, session=None, compaction_id=None) -> str:
        """压缩同一回合的记录时沿用该回合语言，包括外部主动触发。"""
        from .language import current_language
        token = current_language.set(getattr(self, "language", current_language.get()))
        try:
            return await self._summarize_in_language(events, config, session=session, compaction_id=compaction_id)
        finally:
            current_language.reset(token)

    async def _summarize_in_language(self, events: Sequence[Any], config: LlmCallConfig, *, session=None, compaction_id=None) -> str:
        """让模型把一段历史总结成摘要。"""
        def record(event, kind, data):
            # 逐行 JSON 保留来源和角色，防止内嵌 XML、换行或伪造角色标签
            # 被解释为独立的对话边界。
            from ..session.summary_chunks import summary_record
            return summary_record(getattr(event, 'seq', None), kind, data)

        transcript_lines: list[str] = []
        image_calls: set[str] = set()
        skill_versions: dict = {}
        for event in events:
            if event.type in {"system/message", "developer/message"}:
                continue
            text = event.data.get("text") if isinstance(event.data, Mapping) else None
            if text:
                if 'skill_loads' in event.data:
                    from ..skills.context import project
                    text = project(text, event.data['skill_loads'], skill_versions)
                transcript_lines.append(record(event, event.type, {'text': text, 'source': event.data.get('source')}))
            elif event.type == "assistant/message":
                blocks = event.data.get("content", []) if isinstance(event.data, Mapping) else []
                for b in blocks:
                    if b.get("type") == "text":
                        transcript_lines.append(record(event, 'assistant', {'text': b['text']}))
                    elif b.get("type") == "tool_use":
                        if b.get('name') in IMAGE_TOOL_NAMES:
                            image_calls.add(b['id'])
                        transcript_lines.append(record(event, 'tool_call', {
                            "id": b.get("id"), "name": b.get("name"), "input": b.get("input", {})
                        }))
            elif event.type == "tool/result":
                for result in event.data.get("results", []):
                    from ..session.tool_projection import model_result_content
                    content = model_result_content(result)
                    if 'skill_loads' in event.data:
                        from ..skills.context import project
                        if len(event.data['results']) != 1 or result.get('is_error') is not False:
                            raise SessionInvariantError(tr('skill.context.invalid'))
                        content = project(content, event.data['skill_loads'], skill_versions)
                    if result.get('tool_use_id') in image_calls and isinstance(content, str):
                        content = omit_image_data(content)
                    transcript_lines.append(record(event, 'tool_result', {
                        "tool_use_id": result.get("tool_use_id"),
                        "is_error": bool(result.get("is_error", False)),
                        "content": content,
                    }))
        from .compaction import CompactionExecutor
        chunk_chars = min(32_000, max(1, self.context_window // 3)) if self.context_window else 32_000
        identity = self._summary_identity(config, session)
        executor = CompactionExecutor(self, config, session=session, compaction_id=compaction_id,
            restart=self._compaction_restart, context_guard=lambda: identity == self._summary_identity(config, session))
        return await executor.run(transcript_lines, chunk_chars, identity)

    def _summary_identity(self, config, session):
        """身份与运行中校验共用实际语义依赖，不依靠旧指纹猜测。"""
        from ..llm.types import SystemMessage
        from ..session.compaction_policy import SUMMARY_VERSION, SUMMARY_POLICY_KEY
        directives = [to_jsonable(message) for message in session.build_messages()
                      if isinstance(message, (SystemMessage, DeveloperMessage))] if session is not None else []
        from ..llm.compaction import can_use_zen_compaction
        from ..llm.capabilities import minimum
        zen_possible = session is not None and can_use_zen_compaction(self.adapter)
        effective_cap = minimum(config.max_tokens, self.summary_max_tokens or 8192)
        return {'policy_version': SUMMARY_VERSION, 'policy': tr(SUMMARY_POLICY_KEY),
                    'language': getattr(self, 'language', None), 'language_policy': self._language_policy_enabled,
                    'directives': directives, 'summary_cap': effective_cap,
                    'request_mode': 'zen-candidate' if zen_possible else 'summary',
                    'tools': to_jsonable(tuple(self._active_tool_schemas())) if zen_possible else []}

    async def _summarize_transcript(self, transcript: str, config: LlmCallConfig, *, session=None) -> str:
        from ..llm.types import SystemMessage, UserMessage
        from ..session.compaction_policy import SUMMARY_POLICY_KEY

        self._check_cancelled()
        summary_messages = [
            SystemMessage(text=tr(SUMMARY_POLICY_KEY)),
            UserMessage(
                text=(
                    tr('python.agent.93d012db20cf', p0=f'{transcript}')
                )
            ),
        ]
        if self._language_policy_enabled:
            from ..i18n import resources
            summary_messages.append(DeveloperMessage(text=resources(self.language)['language.output_policy'],
                                                     source='summary-language-policy'))
        from ..llm.capabilities import minimum
        summary_cap = minimum(config.max_tokens, self.summary_max_tokens or 8192)
        zen_compaction = None
        # 混合路由只能在最终端点确定后切换请求；这里仅准备候选内容。
        from ..llm.compaction import ZenCompaction, can_use_zen_compaction
        if session is not None and can_use_zen_compaction(self.adapter):
            ordinary = [message for message in session.build_messages()
                        if isinstance(message, (SystemMessage, DeveloperMessage))]
            prefix = [message for message in ordinary if isinstance(message, SystemMessage)]
            runtime = [message for message in ordinary if isinstance(message, DeveloperMessage)]
            if not runtime and self._language_policy_enabled:
                runtime = [summary_messages[-1]]
            task = UserMessage(text=tr(SUMMARY_POLICY_KEY) + '\n\n' + summary_messages[1].text
                               + '\n\n' + tr('context.compaction.zenInstructions', tokens=summary_cap))
            zen_compaction = ZenCompaction(tuple([*prefix, task, *runtime]), tuple(self._active_tool_schemas()))
        if self.context_window:
            summary_cap = minimum(summary_cap, self.context_window - self._estimate_tokens(summary_messages) - max(1, self.context_window // 100))
            if summary_cap is None or summary_cap < 1:
                raise HarnessError(tr('context.compaction.protected'), code='agent/compaction-capacity')
        if self.budget is not None and self.budget.limit is not None:
            available = self.budget.limit - self.budget.spent - self._estimate_tokens(summary_messages)
            if available < 1:
                raise HarnessError(tr('python.agent.0260bcccea53'), code='agent/token-budget')
            summary_cap = minimum(summary_cap, available)
        reservation = None
        charge = None
        failure = None
        from ..llm.compaction_scope import active_compaction
        scope = active_compaction.get()
        input_tokens = self._estimate_tokens(summary_messages)
        if zen_compaction is not None:
            input_tokens = max(input_tokens, self._estimate_tokens(zen_compaction.messages) + self._estimate_tokens(zen_compaction.tools))
        cost = ResponseCost(input_tokens)
        if self.budget is not None:
            reservation = await self.budget.acquire(input_tokens, zen_compaction.max_tokens if zen_compaction else summary_cap)
            if reservation is None:
                raise _RequestBudgetExhausted(tr('python.agent.ca0494df100f'), code='agent/token-budget')
            summary_cap = minimum(summary_cap, reservation.max_tokens)
            if zen_compaction is not None:
                zen_compaction = replace(zen_compaction, max_tokens=reservation.max_tokens)
        if scope is not None:
            reserved_output = reservation.max_tokens if reservation is not None else (
                zen_compaction.max_tokens if zen_compaction else summary_cap)
            scope.reserved = input_tokens + (reserved_output or 0)
        try:
            options = {}
            if zen_compaction is not None:
                options['zen_compaction'] = zen_compaction
            # 保留原 SDK 适配器签名，同时内置实现
            # 也计量被中断的摘要输出。
            parameters = _adapter_parameters(self.adapter.complete)
            if session is not None and 'on_request' in parameters:
                from ..session.request_traces import request_observer
                trace_summary = request_observer(session, request_id='compact_' + uuid4().hex, purpose='compaction')
                async def observed_summary(data):
                    if data['phase'] == 'started':
                        cost.begin_physical(data.get('estimated_input_tokens'))
                    else:
                        cost.finish_physical(data)
                    return await trace_summary(data)
                options['on_request'] = observed_summary
            if 'on_event' in parameters:
                options['on_event'] = cost.observe
            if session is not None and 'cache_session_id' in parameters:
                options['cache_session_id'] = session.id
            self._check_cancelled()
            summary = await self.adapter.complete(summary_messages, config=config.merged(max_tokens=summary_cap), **options)
            charge = summary.usage.total_tokens if summary.usage is not None and summary.usage.total_tokens > 0 else (
                input_tokens + estimate_tokens([summary]))
            if summary.stop_reason == 'length':
                raise LlmResponseError(tr('context.compaction.truncated'), code='llm/summary-truncated')
            if summary.tool_uses:
                raise LlmResponseError(tr('context.compaction.invalidSummary'), code='llm/invalid-tool-response')
            if not summary.text.strip():
                raise LlmResponseError(tr('python.agent.e7c6221ac810'), code='llm/no-answer')
        except BaseException as exc:
            failure = exc
            raise
        finally:
            settled = cost.settlement(charge)
            if not settled and scope is not None and scope.request_started:
                settled = input_tokens
            if reservation is not None:
                reservation.settle(settled)
            self._compaction_charged += settled
            measured = cost.usage if charge is None else summary.usage
            if cost.physical_usage:
                measured = sum(cost.physical_usage.values(), TokenUsage())
            measured = measured if measured is not None and measured.total_tokens > 0 else TokenUsage(
                input_tokens=min(input_tokens, settled), output_tokens=max(0, settled - input_tokens))
            self._usage_total = self._usage_total + measured
            self._compaction_usage = self._compaction_usage + measured
            if scope is not None:
                await scope.settle(settled, cost.settlement_source(summary.usage if charge is not None else None), failure)
        self._check_cancelled()
        return summary.text

    # ==================================================================
    # 工具方法
    # ==================================================================
    async def _capture_cancel_cleanup(self) -> list[BaseException]:
        """保留子任务清理失败，先完成本轮协议收尾后再传播，不重放清理。"""
        if self.cancel_cleanup is None:
            return []
        try:
            await join_cleanup(asyncio.ensure_future(self.cancel_cleanup()))
        except BaseException as exc:
            return [exc]
        return []

    async def _end_turn_safely(self, session: Session, reason: TurnEndReason, *, final_response_seq: int | None = None) -> None:
        """结束回合；已结束则跳过，提交失败必须向调用方传播。"""
        async with self.steering_lock:
            self.accepting_steering = False
            if session.open_turn is None:
                return
            turn = session.open_turn
            try:
                await session.end_turn(reason, final_response_seq=final_response_seq)
            except Exception as exc:
                # 写入结果可能未知；不重试提交，也不能据此返回成功。
                self.status.status = 'error'
                self.status.last_error = str(exc)
                await self._emit('turn/end', {'session_id': session.id, 'turn': turn,
                    'reason': 'error', 'committed': False, 'error': str(exc),
                    **(exc.to_dict() if isinstance(exc, HarnessError) else {'code': 'run/error', 'message': str(exc)})})
                raise
            await self._emit('steer/state', {'session_id': session.id,
                'receipts': session.steering.recent(turn, active_turn=None)})

    async def _settle_unfinished_tools(self, session: Session, message: str | None = None) -> None:
        if session.persistence_error:
            return
        if message is None:
            message = tr('python.agent.7933271745ac')
        calls = {}
        resolved = set()
        entered_pipeline = set()
        current_calls = set()
        turn_start = session.latest('turn/start')
        for event in session.events:
            if event.type == "assistant/message":
                for block in event.data.get("content", []):
                    if block.get("type") == "tool_use":
                        calls[block["id"]] = block.get("name", "")
                        if turn_start is not None and event.seq > turn_start.seq:
                            current_calls.add(block['id'])
            elif event.type == "tool/result":
                resolved.update(item["tool_use_id"] for item in event.data.get("results", []))
            elif event.type == 'tool/call' and event.data.get('status') == 'running':
                entered_pipeline.add(event.data.get('call_id'))
        for call_id in calls:
            if call_id in resolved:
                continue
            # 缺少调用证据时，无法确定执行状态。
            # 只有本回合的准入流程能证明操作尚未启动。
            not_executed = (call_id in current_calls and call_id not in entered_pipeline
                            or call_id in self._unstarted_call_ids)
            content = (tr('python.agent.b2e40255c819')
                       if not_executed else message)
            payload = {"session_id": session.id, "name": calls[call_id], "call_id": call_id,
                       "content": content, "is_error": True, "display": {"interrupted": True, "not_executed": not_executed}}
            await session.append("tool/result", {**payload, "results": [{"tool_use_id": call_id, "content": content, "is_error": True}]}, surface_op=AppendOp())
            await session.append("tool/call", {"name": calls[call_id], "call_id": call_id, "status": "error", "display": payload["display"]})
            await self._emit("tool/end", payload)

    async def _emit(self, event: str, payload: dict[str, Any]) -> None:
        """广播事件（无总线时静默忽略）。"""
        if self.bus is None:
            return
        try:
            if event in {'request/start', 'assistant/delta', 'assistant/message', 'assistant/partial'}:
                payload = {**payload, **_speaker_metadata()}
            await self.bus.emit(event, payload)
        except Exception:
            logger.debug(tr('log.c8a407f0f4e3'), event, exc_info=True)


def _compaction_head(surface: Sequence[Any], keep_tail: int = 12) -> list[Any]:
    """选取连续的旧历史，排除策略并避免拆分工具调用与结果。"""
    return max(_compaction_regions(surface, keep_tail), key=len, default=[])


def _compaction_regions(surface: Sequence[Any], keep_tail: int = 12, *,
                        protect_images_after: int | None = None) -> list[list[Any]]:
    """按策略和完整图片批次分隔旧历史，保留可分别替换的连续区间。"""
    limit = max(0, len(surface) - keep_tail)
    pending = set()
    safe = [True]
    regions = []
    start = 0
    batch_start = 0
    protected_batch = False
    latest_task = next((event.seq for event in reversed(surface)
                        if event.type == 'user/message' and event.data.get('source', 'user') != 'compaction'), None)
    latest_correction = max((event.seq for event in surface if event.type == 'user/message'
                             and event.data.get('source', 'user') != 'compaction'), default=-1)
    for index, event in enumerate(surface[:limit]):
        if event.type in {"system/message", "developer/message"} or event.seq in {latest_task, latest_correction}:
            regions.append((start, index))
            start = index + 1
        if not pending:
            batch_start = index
        if event.type == "assistant/message":
            blocks = event.data.get("content", ())
            if protect_images_after is not None and event.seq > protect_images_after:
                protected_batch |= any(block.get('type') == 'tool_use' and block.get('name') in IMAGE_TOOL_NAMES
                                       for block in blocks)
            pending.update(block["id"] for block in blocks if block.get("type") == "tool_use")
        elif event.type == "tool/result":
            pending.difference_update(result["tool_use_id"] for result in event.data.get("results", ()))
        safe.append(not pending)
        if protected_batch and not pending:
            # 仅隔离含图片的完整工具批次，后续交互仍可作为独立区间压缩。
            regions.append((start, batch_start))
            start = index + 1
            protected_batch = False
    if protected_batch:
        regions.append((start, batch_start))
    else:
        regions.append((start, limit))

    candidates = []
    for start, end in regions:
        first = next((index for index in range(start, end + 1) if safe[index]), end)
        last = next((index for index in range(end, first - 1, -1) if safe[index]), first)
        # 只替换一两个节点收益很小，却要付出一次摘要请求的代价。
        if last - first >= 3:
            candidates.append(list(surface[first:last]))
    return candidates



__all__ = ["AgentLoop", "AgentStatus", "TurnResult", "MAX_STEPS_PER_TURN", "MAX_STREAM_CONTINUATIONS"]
