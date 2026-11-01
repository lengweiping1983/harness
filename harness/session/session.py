"""会话：append-only 事件日志 + 有序表面 + 消息派生。

这是整个 harness 的核心数据结构，围绕三个关键设计：

1. **append-only 日志**：只能 :meth:`Session.append`，不能修改历史。
2. **有序表面（surface）**：不是所有事件都进 LLM 上下文。表面是一张"有序的
   消息列表"，``append`` 加到尾部，``replace`` 把一段区间换成一条摘要。
3. **消息派生**：:meth:`Session.build_messages` 从表面投影出 LLM 消息，
   而不是单独存一份消息历史。重放 = 重新派生。

附加能力：
* :meth:`SessionStore.fork` 在某个序号处切分，生成子会话（继承前缀事件）。
* :attr:`Session.open_turn` 恢复未结束回合；恢复过程不重放未知副作用。
"""

from __future__ import annotations

from harness.i18n import tr

import asyncio
import time
import uuid
from collections.abc import Iterable, Mapping
from typing import Any, Callable

from ..core.errors import SessionError, SessionInvariantError
from ..core.events import EventBus
from ..core.cleanup import join_cleanup
from ..core.jsonutil import JsonValue, deep_freeze
from ..llm.types import (
    AssistantMessage,
    ContentBlock,
    DeveloperMessage,
    ImageBlock,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolResultMessage,
    ToolUseBlock,
    UserMessage,
)
from .events import (
    SESSION_FORMAT_VERSION,
    KNOWN_EVENT_TYPES,
    PLUGIN_EVENTS,
    ReplaceOp,
    SessionEvent,
    SessionHeader,
    SurfaceOp,
    TurnEndReason,
    is_surface_event,
)
from .partials import is_response_evidence


def now_ms() -> int:
    """当前 Unix 毫秒时间戳。"""
    return int(time.time() * 1000)


def new_session_id() -> str:
    """生成一个新的会话 id。"""
    return f"ses_{uuid.uuid4().hex[:24]}"


def _turn_state(event_type: str, data: Any, current: int, opened: int | None) -> tuple[int, int | None]:
    """提交和恢复共用严格轮次转换，不能强制转换损坏身份。"""
    if event_type not in ('turn/start', 'turn/end'):
        return current, opened
    turn = data.get('turn') if isinstance(data, Mapping) else None
    if type(turn) is not int or turn <= 0:
        raise SessionInvariantError(tr('session.turn.invalid_lifecycle'))
    if event_type == 'turn/start':
        if opened is not None or turn <= current:
            raise SessionInvariantError(tr('session.turn.invalid_lifecycle'))
        return turn, turn
    if opened is None or turn != opened:
        raise SessionInvariantError(tr('session.turn.invalid_lifecycle'))
    return current, None


def _replace_span(surface: list[SessionEvent], op: ReplaceOp) -> tuple[int, int]:
    """只校验并定位当前表面的替换区间，不在持久化之前修改投影。"""
    seqs = [event.seq for event in surface]
    try:
        start = seqs.index(op.start_seq)
        end = seqs.index(op.end_seq)
    except ValueError as exc:
        raise SessionInvariantError(
            tr('python.session.895010b756ff', p0=f'{op.start_seq}', p1=f'{op.end_seq}')
        ) from exc
    if start > end:
        raise SessionInvariantError(tr('python.session.372b1c2f56b0', p0=f'{op.start_seq}', p1=f'{op.end_seq}'))
    return start, end + 1


def project_surface(events: Iterable[SessionEvent]) -> list[SessionEvent]:
    """重放不可变事件，包括插入较早位置的替换事件。"""
    surface: list[SessionEvent] = []
    for event in events:
        if not is_surface_event(event.type):
            continue
        if isinstance(event.surface_op, ReplaceOp):
            start, end = _replace_span(surface, event.surface_op)
            surface[start:end] = [event]
        else:
            surface.append(event)
    return surface


class Session:
    """一个会话的内存模型。

    :param header: 会话元数据；``None`` 时自动创建。
    :param bus:    事件总线，用于把 ``session/append`` 广播给订阅者（UI 等）。
    """

    def __init__(self, header: SessionHeader | None = None, *, bus: EventBus | None = None) -> None:
        self.header = header or SessionHeader(id=new_session_id(), created_at=now_ms(), version=SESSION_FORMAT_VERSION)
        self.bus = bus or EventBus()

        # -- append-only 日志 ------------------------------------------------
        self._events: list[SessionEvent] = []
        self._history_generation = 0
        self._message_cache: tuple[Any, ...] | None = None
        # 派生的恢复索引仅供展示，持久化提交后才更新。
        self._response_evidence: list[SessionEvent] = []
        from .steering import SteeringLedger
        self.steering = SteeringLedger()
        from .agent_messages import AgentMailbox
        self.agent_messages = AgentMailbox(self)
        # -- 有序表面：存放 surface 事件（产生 LLM 消息） --------------------
        self._surface: list[SessionEvent] = []
        # -- 事件序号 -> 事件的索引，便于 O(1) 查找 --------------------------
        self._by_seq: dict[int, SessionEvent] = {}
        self._latest_by_type: dict[str, SessionEvent] = {}
        # -- 监听器：只关心"日志追加"这一件事 --------------------------------
        self._listeners: list[Callable[[SessionEvent], Any]] = []
        # -- 生命周期状态 ----------------------------------------------------
        self._open_turn: int | None = None
        self._current_turn: int = 0
        self._closed = False
        # 异步监听器返回的 awaitable，drain() 时统一等待
        self._pending_awaitables: list[Any] = []
        self._draining: asyncio.Task | None = None
        self.commit_event: Callable[[SessionEvent], None] | None = None
        self.persistence_error: str | None = None
        from .file_evidence import FileEvidenceStore
        self.file_evidence = FileEvidenceStore()
        from .request_traces import RequestTraces
        self.request_traces = RequestTraces()
        from .file_observations import FileObservations
        self.file_observations = FileObservations()
        from .tool_result_evidence import ToolResultEvidence
        self.tool_results = ToolResultEvidence()

    # ------------------------------------------------------------------
    # 基本属性
    # ------------------------------------------------------------------
    @property
    def id(self) -> str:
        return self.header.id

    @property
    def events(self) -> tuple[SessionEvent, ...]:
        """按序号返回全部事件的不可变快照。"""
        return tuple(self._events)

    @property
    def response_evidence(self) -> tuple[SessionEvent, ...]:
        """已提交的响应锚点、片段和终态证据。"""
        return tuple(self._response_evidence)

    @property
    def surface(self) -> tuple[SessionEvent, ...]:
        """返回当前有序表面的不可变快照。"""
        return tuple(self._surface)

    @property
    def event_count(self) -> int:
        return len(self._events)

    @property
    def history_generation(self) -> int:
        """原地重载的世代，供派生缓存识别同一对象中的新历史。"""
        return self._history_generation

    def request_checkpoint(self) -> dict[str, Any]:
        """传输开始时的元数据，不重建传输消息。"""
        system = next((event for event in reversed(self._surface) if event.type == 'system/message'), None)
        header = self.latest('request/header')
        compaction = self.latest('compaction/summary')
        return {'event_seq': self._events[-1].seq if self._events else -1,
                'event_count': len(self._events), 'surface_nodes': len(self._surface),
                'system_seq': system.seq if system else None,
                'request_header_seq': header.seq if header else None,
                'compaction_summary_seq': compaction.seq if compaction else None}

    @property
    def current_turn(self) -> int:
        return self._current_turn

    def event_at(self, seq: int) -> SessionEvent | None:
        """按序号取事件。"""
        return self._by_seq.get(seq)

    def latest(self, event_type: str) -> SessionEvent | None:
        """取最后一条指定类型的事件。"""
        return self._latest_by_type.get(event_type)

    def events_of_type(self, event_type: str) -> list[SessionEvent]:
        """取全部指定类型的事件。"""
        return [e for e in self._events if e.type == event_type]

    # ------------------------------------------------------------------
    # 订阅
    # ------------------------------------------------------------------
    def subscribe(self, listener: Callable[[SessionEvent], Any]) -> Callable[[], None]:
        """订阅每一条新事件。返回取消订阅的函数。

        监听器可以是同步函数或返回 awaitable 的协程函数；协程会被
        ``Session.drain()`` 收集并在 drain 时 await。
        """
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    # ------------------------------------------------------------------
    # 追加事件
    # ------------------------------------------------------------------
    async def append(
        self,
        event_type: str,
        data: JsonValue,
        *,
        surface_op: SurfaceOp | None = None,
        source_event_seqs: Iterable[int] | None = None,
        ignorable: bool = False,
        time_ms: int | None = None,
    ) -> SessionEvent:
        """向日志追加一条事件。

        校验规则：
        * 事件类型必须已知（核心类型或插件类型）；
        * surface 事件必须提供 ``surface_op``；log-only 事件必须 **不** 提供；
        * ``replace`` 的 ``[start_seq, end_seq]`` 必须落在现有表面节点上；
        * ``source_event_seqs`` 中的序号必须已存在。

        :returns: 新建的事件（已冻结）。
        """
        if self._closed:
            raise SessionError(tr('python.session.617771d3443e', p0=f'{self.id}'))
        if self.persistence_error:
            from ..core.errors import PersistenceError
            raise PersistenceError(self.persistence_error, session_id=self.id)

        known = event_type in KNOWN_EVENT_TYPES or any(name == event_type for name, _ in PLUGIN_EVENTS)
        if not known:
            raise SessionInvariantError(
                tr('python.session.1ec0b6efc551', p0=f'{event_type!r}')
            )

        surface_event = is_surface_event(event_type)
        if surface_event and surface_op is None:
            raise SessionInvariantError(tr('python.session.30058873cf11', p0=f'{event_type!r}'))
        if not surface_event and surface_op is not None:
            raise SessionInvariantError(tr('python.session.55af51a24ff3', p0=f'{event_type!r}'))

        sources = tuple(source_event_seqs or ())
        for src in sources:
            if src not in self._by_seq:
                raise SessionInvariantError(tr('python.session.b32319447e72', p0=f'{src}'))

        seq = len(self._events)
        event = SessionEvent(
            type=event_type,
            seq=seq,
            time=time_ms if time_ms is not None else now_ms(),
            data=deep_freeze(data),
            surface_op=surface_op,
            source_event_seqs=sources or None,
            ignorable=ignorable,
        )

        self.agent_messages.validate(event)
        turn_state = _turn_state(event.type, event.data, self._current_turn, self._open_turn)

        # -- 表面操作（先校验，再落日志） ---------------------------------
        # replace 不是"先删后追加"，而是**就地替换**：被替换区间消失，
        # 新节点出现在区间的起始位置。这样压缩摘要会留在它概括的那段
        # 历史的位置上，而不是被甩到表面末尾。
        replace_span = _replace_span(self._surface, surface_op) if isinstance(surface_op, ReplaceOp) else None

        # 持久化是提交边界，不是可忽略错误的 UI 监听器。
        if self.commit_event is not None:
            try:
                self.commit_event(event)
            except Exception as exc:
                from ..core.errors import PersistenceError
                self.persistence_error = tr('python.session.2daa27c516d2')
                raise PersistenceError(self.persistence_error, session_id=self.id) from exc
        # -- 落日志 --------------------------------------------------------
        self._events.append(event)
        self.steering.absorb(event)
        self.agent_messages.absorb(event)
        self.file_observations.absorb(event)
        self.tool_results.absorb(event)
        if is_response_evidence(event):
            self._response_evidence.append(event)
        self._by_seq[seq] = event
        self._latest_by_type[event.type] = event
        if surface_event:
            self._message_cache = None
            if replace_span is not None:
                start, end = replace_span
                self._surface[start:end] = [event]
            else:
                self._surface.append(event)

        self._current_turn, self._open_turn = turn_state

        # -- 广播 ----------------------------------------------------------
        for listener in list(self._listeners):
            try:
                result = listener(event)
                if hasattr(result, "__await__"):
                    # 协程监听器：登记到 drain 队列
                    self._pending_awaitables.append(result)
            except Exception:
                # 监听器错误不应影响日志写入
                import logging

                logging.getLogger(__name__).exception(tr('log.a8c870f42029'), seq)

        await self.bus.emit("session/append", self, event)
        return event

    # -- 加载 / 导入入口（供 SessionStore 使用）--------------------------
    def restore(self, events: Iterable[SessionEvent]) -> None:
        """用一批已存在的事件**原地重建**一个会话（不经过 append 校验）。

        这是持久化加载与导入的专用入口；先校验完整轮次转换，
        再重建内部索引与表面，保证"重载后的状态 == 当时的状态"。

        :param events: 已解码的事件，序号必须从 0 连续递增。
        """
        events = tuple(events)
        current, opened = 0, None
        for event in events:
            current, opened = _turn_state(event.type, event.data, current, opened)
        # 全部投影先在隔离对象中完成，任何重放错误都不改变现有对话。
        from .steering import SteeringLedger
        from .agent_messages import AgentMailbox
        from .file_observations import FileObservations
        from .tool_result_evidence import ToolResultEvidence
        steering = SteeringLedger()
        agent_messages = AgentMailbox(self)
        file_observations = FileObservations()
        tool_results = ToolResultEvidence()
        response_evidence = []
        by_seq = {}
        latest_by_type = {}
        for event in events:
            steering.absorb(event)
            agent_messages.absorb(event)
            file_observations.absorb(event)
            tool_results.absorb(event)
            if is_response_evidence(event):
                response_evidence.append(event)
            by_seq[event.seq] = event
            latest_by_type[event.type] = event
        surface = project_surface(events)
        self._history_generation += 1
        self._message_cache = None
        self._events = list(events)
        self.steering = steering
        self.agent_messages = agent_messages
        self.file_observations = file_observations
        self.tool_results = tool_results
        self._response_evidence = response_evidence
        self._by_seq = by_seq
        self._latest_by_type = latest_by_type
        self._surface = surface
        self._current_turn, self._open_turn = current, opened

    async def drain(self) -> None:
        """等待所有异步监听器完成。

        多个调用等待同一次收尾；等待方取消不撤销已经开始的监听器。
        持久化仍由提交边界负责，监听器完成不替代落盘证明。
        """
        if self._draining is None or self._draining.done():
            self._draining = asyncio.create_task(self._drain_listeners())
        await join_cleanup(self._draining)

    async def _drain_listeners(self) -> None:
        """同一次收尾拥有全部监听器，等待方取消不撤销已开始的工作。"""
        while self._pending_awaitables:
            pending, self._pending_awaitables = self._pending_awaitables, []
            for index, awaitable in enumerate(pending):
                try:
                    await awaitable
                except asyncio.CancelledError:
                    # 监听器主动取消时保留尚未执行的项，后续显式收尾接管。
                    self._pending_awaitables[0:0] = pending[index + 1:]
                    raise
                except Exception:
                    import logging
                    logging.getLogger(__name__).exception(tr('log.9ee3409376c1'))

    # ------------------------------------------------------------------
    # 生命周期便捷方法
    # ------------------------------------------------------------------
    async def start_turn(self, turn: int | None = None, *, run_id: str | None = None, language: str | None = None) -> SessionEvent:
        """开启一轮对话。"""
        if self._open_turn is not None:
            raise SessionInvariantError(tr('python.session.ab7a293495d3', p0=f'{self._open_turn}'))
        if turn is None:
            turn = self._current_turn + 1
        event = await self.append("turn/start", {"turn": turn, **({'run_id': run_id} if run_id else {}), **({'language': language} if language else {})})
        self._current_turn = turn
        self._open_turn = turn
        return event

    async def end_turn(self, reason: TurnEndReason, *, final_response_seq: int | None = None) -> SessionEvent:
        """结束当前回合。"""
        if self._open_turn is None:
            raise SessionInvariantError(tr('python.session.592faffed78a'))
        turn = self._open_turn
        data = {"turn": turn, "reason": reason.to_dict()}
        if final_response_seq is not None:
            response = self.event_at(final_response_seq)
            start = self.latest('turn/start')
            if (reason.kind != 'completed' or response is None or response.type != 'assistant/message'
                    or start is None or response.seq <= start.seq
                    or self.latest('assistant/message') is not response
                    or any(part.get('type') == 'tool_use' for part in response.data.get('content', ()))):
                raise SessionInvariantError(tr('python.session.9616e3afbc8f'))
            data['final_response_seq'] = final_response_seq
        event = await self.append("turn/end", data)
        self._open_turn = None
        return event

    @property
    def open_turn(self) -> int | None:
        """当前未结束回合的编号。"""
        return self._open_turn

    async def close(self) -> None:
        """关闭会话：若仍有未结束回合，补一个 ``interrupted`` 结束原因。"""
        if self._closed:
            await self.drain()
            return
        if self._open_turn is not None:
            await self.end_turn(TurnEndReason(kind="interrupted", message=tr('python.session.1ad3946007ba')))
        self._closed = True
        await self.bus.emit("session/close", self)
        # 关会话时把排队中的落盘协程跑完，避免 "coroutine was never awaited"
        # 警告与"关了会话却丢事件"的数据不一致。
        await self.drain()

    # ------------------------------------------------------------------
    # 消息派生
    # ------------------------------------------------------------------
    def build_messages(self, *, surface=None) -> list[Any]:
        """从有序表面派生 LLM 消息列表。

        缓存只从已提交的表面派生，表面变化后失效，不维护独立历史。
        可选表面用于整理提交前的纯投影；不写入缓存或原始事件。
        """
        cached = surface is None
        if cached and self._message_cache is not None:
            return list(self._message_cache)
        surface = self._surface if cached else surface
        messages: list[Any] = []
        skill_versions: dict = {}
        current_system = max((event for event in surface if event.type == 'system/message'), key=lambda event: event.seq, default=None)
        if current_system is not None:
            messages.append(self._event_to_message(current_system))
        for event in surface:
            # 遍历消息表面时跳过系统策略节点，保留其审计记录。
            # 当前策略已在前面加入，只使用一次。
            if event.type == 'system/message':
                continue
            message = self._event_to_message(event, skill_versions=skill_versions)
            if message is not None:
                if isinstance(message, list):
                    messages.extend(message)
                else:
                    messages.append(message)
        if current_system is not None and current_system.data.get('runtime_text'):
            messages.append(DeveloperMessage(text=str(current_system.data['runtime_text']), source='system-runtime',
                context_parts=self._context_parts(current_system, 'runtime', str(current_system.data['runtime_text']))))
        if cached:
            self._message_cache = tuple(messages)
        return messages

    @staticmethod
    def _context_parts(event, part, text):
        """只使用事件记录的来源边界，不根据提示正文猜测来源。"""
        spans = event.data.get('context_sources', {}).get(part, ())
        if not spans:
            return ()
        offset, result = 0, []
        for span in spans:
            if span['start'] != offset or not offset <= span['end'] <= len(text):
                raise SessionInvariantError(tr('context.sources.invalid'))
            result.append((span['source'], text[offset:span['end']]))
            offset = span['end']
        if offset != len(text):
            raise SessionInvariantError(tr('context.sources.invalid'))
        return tuple(result)

    def _event_to_message(self, event: SessionEvent, *, skill_versions=None) -> Any | list[Any] | None:
        """把一条 surface 事件转成 LLM 消息。"""
        data = event.data
        if event.type == "system/message":
            text = str(data.get("cache_prefix", data.get("text", "")))
            return SystemMessage(text=text, context_parts=self._context_parts(event, 'stable', text))
        if event.type == "developer/message":
            if data.get('source') == 'agent-message':
                from .agent_messages import render_agent_message
                record = self.event_at(event.source_event_seqs[0])
                return DeveloperMessage(text=render_agent_message(record, data['language']), source='agent-message')
            return DeveloperMessage(text=str(data.get("text", "")), source=str(data.get("source", "developer")))
        if event.type == "user/message" and data.get("source") == "plan-mode":
            # 模式通知属于系统控制，保留原始证据但不归类为用户输入。
            return DeveloperMessage(text=str(data.get("text", "")), source="plan-mode")
        if event.type == "user/message":
            text = str(data.get('text', ''))
            source = str(data.get('source', 'user'))
            parts = ()
            if skill_versions is not None and 'skill_loads' in data:
                from ..skills.context import project_with_parts
                text, parts = project_with_parts(text, data['skill_loads'], skill_versions, source=source)
            offset, spans = 0, []
            for name, chunk in parts:
                spans.append((name, offset, offset + len(chunk)))
                offset += len(chunk)
            return UserMessage(text=text, source=source, context_spans=tuple(spans))
        if event.type == "assistant/message":
            return self._build_assistant(data)
        if event.type == "tool/result":
            from .tool_projection import model_result_content
            results: list[ToolResultBlock] = []
            for item in data.get("results", []):
                text = model_result_content(item)
                if skill_versions is not None and 'skill_loads' in data:
                    from ..skills.context import project
                    # 元数据只绑定宿主提交的单项技能结果，不能套用到批次其它项。
                    if len(data['results']) != 1 or item.get('is_error') is not False:
                        raise SessionInvariantError(tr('skill.context.invalid'))
                    text = project(text, data['skill_loads'], skill_versions)
                results.append(
                    ToolResultBlock(
                        tool_use_id=item["tool_use_id"],
                        content=text,
                        is_error=bool(item.get("is_error", False)),
                    )
                )
            return ToolResultMessage(results=tuple(results))
        return None

    @staticmethod
    def _build_assistant(data: dict[str, Any]) -> AssistantMessage:
        """从 ``assistant/message`` 事件数据构建助手消息。

        事件体里保存的是"最终内容块 + 完整流"，这里重建内容块。
        """
        content: list[ContentBlock] = []

        for block in data.get("content", []):
            btype = block.get("type")
            if btype == "text":
                content.append(TextBlock(text=block.get("text", "")))
            elif btype == "thinking":
                content.append(ThinkingBlock(thinking=block.get("thinking", ""), signature=block.get("signature")))
            elif btype == "tool_use":
                content.append(
                    ToolUseBlock(id=block["id"], name=block["name"], input=block.get("input", {}))
                )
            elif btype == "image":
                content.append(
                    ImageBlock(url=block.get("url", ""), mime_type=block.get("mime_type", "image/png"))
                )

        usage = None
        if data.get("usage"):
            from ..llm.types import TokenUsage

            usage = TokenUsage(
                input_tokens=data["usage"].get("input_tokens", 0),
                output_tokens=data["usage"].get("output_tokens", 0),
                reasoning_tokens=data["usage"].get("reasoning_tokens", 0),
                cache_read_tokens=data["usage"].get("cache_read_tokens", 0),
                cache_reported_input_tokens=data["usage"].get("cache_reported_input_tokens", 0),
            )

        return AssistantMessage(
            content=tuple(content),
            model=data.get("model", ""),
            stop_reason=data.get("stop_reason"),
            usage=usage,
            reasoning_details=tuple(data.get("reasoning_details") or ()),
        )

    # ------------------------------------------------------------------
    # 工具函数
    # ------------------------------------------------------------------
    def __repr__(self) -> str:
        return f"<Session {self.id} events={self.event_count} surface={len(self._surface)}>"


def create_session(
    *,
    bus: EventBus | None = None,
    cwd: str | None = None,
    title: str | None = None,
    parent_session: str | None = None,
) -> Session:
    """创建一个新会话的便捷函数。"""
    header = SessionHeader(
        id=new_session_id(),
        created_at=now_ms(),
        cwd=cwd,
        title=title,
        parent_session=parent_session,
    )
    return Session(header, bus=bus)


__all__ = ["Session", "SessionHeader", "create_session", "new_session_id", "now_ms"]
