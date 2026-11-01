"""会话检索工具。

在进程内完成事件日志分析、消息表面折叠、血缘追踪、文本抽取和检索，
通过 ``session_search``、``session_event_search``、``session_trace``、
``session_event_trace`` 和 ``session_event_read`` 五个工具提供给模型。

实现遵循四条不变量：

1. **surface 语义**：日志是 append-only 的，但"当前上下文"是折叠后的结果。
   被后续 ``replace`` 节点取代的事件属于 ``shadowed``，既不在当前上下文，
   也没有丢失 —— 这正是 ``session_event_trace`` 能追溯"改写历史"的基础。
2. **工作区授权**：只能检索与调用方同一工作目录（``SessionHeader.cwd``）的会话，
   且永远排除调用方自己，避免"搜索历史"退化成"复述当前对话"。
3. **当前会话按 step 截断**：在当前会话里搜索时，本 step 之后写入的事件
   （包括本工具自己的调用轨迹）必须先裁掉，否则检索结果会被当前步骤污染。
4. **血缘完整性**：祖先链或后代树遇到缺失记录时，**不静默截断**，而是显式
   报告 ``complete=False`` 与未解析的父会话 id。

有意简化：

* 门禁改用 :class:`SessionStore` + 调用方 ``ToolRunContext.cwd`` 做等价判定，
  不依赖插件运行时。
* 检索是线性扫描 + 数量上限，对本 harness 的会话体量足够，且省掉一整套
  持久化索引。
"""

from __future__ import annotations

from harness.i18n import tr
from ...llm.types import SchemaResource

import json
import os
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from collections.abc import Mapping
from typing import Any, Iterable, Sequence

from ...core.errors import SessionNotFoundError, ToolError
from ...core.jsonutil import to_jsonable
from ...llm.types import TextBlock
from ...session.events import ReplaceOp, SessionEvent, SessionHeader, is_surface_event
from ...session.session import Session
from ...session.store import SessionStore
from ..registry import ToolDefinition, ToolRunContext, ToolResult

#: 单次检索默认返回条数。
DEFAULT_MAX_RESULTS = 100

#: 单次检索允许的最大返回条数（防止一次调用把整段历史灌进上下文）。
MAX_RESULTS_CEILING = 500

#: ``session_search`` 的 ``availability`` 取值。
AVAILABILITY_VALUES: tuple[str, ...] = ("live", "persisted")

#: 事件在"表面"上的三种归属。
SURFACE_VALUES: tuple[str, ...] = ("current", "shadowed", "log-only")

#: 邻居事件摘要里单条 snippet 的最大长度。
SNIPPET_LIMIT = 400

#: ``session_event_read`` 默认向前/后各取几条邻居。
DEFAULT_NEIGHBORS = 2

#: 血缘遍历的硬上限，避免环形数据把遍历拖死。
MAX_LINEAGE_NODES = 512


def _fail(code: str, message: str, **details: Any) -> ToolError:
    """构造带稳定错误码的 :class:`ToolError`（与 goal_tools 同一套约定）。"""
    return ToolError(message, code=code, **details)


def _iso(ms: Any) -> str:
    """把 Unix 毫秒时间戳渲染成 UTC ISO-8601；无法解析时返回 ``-``。"""
    if isinstance(ms, bool) or not isinstance(ms, (int, float)):
        return "-"
    try:
        return datetime.fromtimestamp(float(ms) / 1000.0, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return "-"


def _parse_time(value: Any, field_name: str) -> int | None:
    """把模型给的时间参数（Unix 毫秒或 ISO-8601 字符串）统一成毫秒整数。"""
    if value is None:
        return None
    if isinstance(value, bool):
        raise _fail("SESSION_QUERY_TOOL_INVALID_ARGUMENT", tr('python.session_query_tools.d372df10c958', p0=f'{field_name}'))
    if isinstance(value, (int, float)):
        try:
            return int(value)
        except (ValueError, OverflowError) as exc:
            raise _fail(
                "SESSION_QUERY_TOOL_INVALID_ARGUMENT",
                tr('python.session_query_tools.09b88448ac64', p0=field_name, p1=repr(value)),
            ) from exc
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return int(float(text))
        except (ValueError, OverflowError):
            pass
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise _fail(
                "SESSION_QUERY_TOOL_INVALID_ARGUMENT",
                tr('python.session_query_tools.09b88448ac64', p0=f'{field_name}', p1=f'{value!r}'),
            ) from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp() * 1000)
    raise _fail("SESSION_QUERY_TOOL_INVALID_ARGUMENT", tr('python.session_query_tools.f8318286b6be', p0=f'{field_name}', p1=f'{type(value).__name__}'))


def _norm_cwd(path: str | None) -> str | None:
    """归一化工作目录，用于跨平台的授权比对（Windows 大小写不敏感）。"""
    if path is None:
        return None
    try:
        return os.path.normcase(os.path.abspath(os.path.expanduser(path)))
    except (OSError, ValueError):
        return os.path.normcase(path)


def _as_str_list(value: Any, field_name: str) -> list[str] | None:
    """校验字符串数组参数；``None`` 表示不过滤。"""
    if value is None:
        return None
    if not isinstance(value, list):
        raise _fail("SESSION_QUERY_TOOL_INVALID_ARGUMENT", tr('python.session_query_tools.e5af7f28cd64', p0=f'{field_name}'))
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise _fail("SESSION_QUERY_TOOL_INVALID_ARGUMENT", tr('python.session_query_tools.79651f86bb91', p0=f'{field_name}'))
        out.append(item)
    return out


def _snippet(text: str, limit: int = SNIPPET_LIMIT) -> str:
    """把命中文本压成单行片段，超长截断。"""
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    return flat[: limit - 1] + "…"


# ---------------------------------------------------------------------------
# 事件日志分析
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class EventLog:
    """一个会话事件日志的派生索引。

    :param records: ``seq -> SessionEvent`` 全量索引（包含被折叠掉的事件）。
    :param replaced_by: 被取代的 ``seq -> 取代它的 seq``。
    :param replaced_event_seqs: 取代者 ``seq -> 它取代掉的 seq 列表``。
    :param current_seqs: 当前折叠表面上的全部 ``seq``。
    """

    records: dict[int, SessionEvent] = field(default_factory=dict)
    replaced_by: dict[int, int] = field(default_factory=dict)
    replaced_event_seqs: dict[int, list[int]] = field(default_factory=dict)
    current_seqs: set[int] = field(default_factory=set)

    def surface_of(self, seq: int) -> str:
        """返回事件在表面上的归属：``current`` / ``shadowed`` / ``log-only``。"""
        if seq in self.current_seqs:
            return "current"
        if seq in self.replaced_by:
            return "shadowed"
        return "log-only"

    def replacement_chain(self, seq: int) -> list[int]:
        """从被取代者出发，逐级向上找出所有取代者（直到不再被取代）。"""
        chain: list[int] = []
        seen: set[int] = set()
        cursor = self.replaced_by.get(seq)
        while cursor is not None and cursor not in seen:
            seen.add(cursor)
            chain.append(cursor)
            cursor = self.replaced_by.get(cursor)
        return chain


def fold_surface(
    events: Iterable[SessionEvent],
) -> tuple[list[int], list[tuple[int, list[int]]]]:
    """严格重放 surface 的 append / replace 折叠。

    与 :func:`harness.session.session.project_surface` 同规则，额外记录"谁取代了谁"。

    :returns: ``(当前表面的 seq 顺序列表, [(取代者 seq, 被取代的 seq 列表), ...])``
    """
    surface: list[int] = []
    replacements: list[tuple[int, list[int]]] = []
    for event in events:
        if not is_surface_event(event.type):
            continue
        op = event.surface_op
        if isinstance(op, ReplaceOp):
            if op.start_seq in surface and op.end_seq in surface:
                start = surface.index(op.start_seq)
                end = surface.index(op.end_seq)
                if start <= end:
                    shadowed = surface[start : end + 1]
                    del surface[start : end + 1]
                    surface[start:start] = [event.seq]
                    replacements.append((event.seq, shadowed))
                    continue
        surface.append(event.seq)
    return surface, replacements


def analyze_event_log(events: Sequence[SessionEvent]) -> EventLog:
    """建立事件日志的派生索引（surface 折叠 + 取代关系）。"""
    log = EventLog()
    for event in events:
        log.records[event.seq] = event
    surface, replacements = fold_surface(events)
    log.current_seqs = set(surface)
    for replacer, shadowed in replacements:
        for seq in shadowed:
            log.replaced_by[seq] = replacer
        log.replaced_event_seqs.setdefault(replacer, []).extend(shadowed)
    return log
# ---------------------------------------------------------------------------
# 当前事件载荷的文本抽取
# ---------------------------------------------------------------------------


def _stringify(value: Any) -> str:
    """把任意值转成可读文本：字符串原样，其余尽量 JSON 化。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=False)
    except (TypeError, ValueError):
        return str(value)


def _join_text(parts: Iterable[Any]) -> str:
    """逐段去除首尾空白，丢弃空段后以换行拼接。"""
    out: list[str] = []
    for part in parts:
        text = _stringify(part).strip()
        if text:
            out.append(text)
    return "\n".join(out)


def _block_text(block: Any) -> str:
    """抽取当前消息内容块的语义文本。

    - ``text`` 块取 ``text``
    - ``thinking`` 块不参与检索
    - ``tool_use`` 块取名称和 ``input`` 参数
    """
    if isinstance(block, str):
        return block.strip()
    if not isinstance(block, dict):
        return ""
    kind = block.get("type")
    if kind == "text":
        return _stringify(block.get("text")).strip()
    if kind == "tool_use":
        return _join_text([block.get("name"), block.get("input")])
    return ""


def _content_text(content: Any) -> str:
    """抽取一段消息内容的语义文本。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return _join_text(_block_text(block) for block in content)
    if isinstance(content, dict):
        return _block_text(content)
    return _stringify(content).strip()


def _turn_end_text(data: dict[str, Any]) -> str:
    """把 ``turn/end`` 的 reason 映射成可检索文本。"""
    reason = data.get("reason")
    if not isinstance(reason, dict):
        return ""
    kind = str(reason.get("kind") or "")
    if kind == "error":
        error = reason.get("error")
        detail = ""
        if isinstance(error, dict):
            detail = _stringify(error.get("message"))
        detail = detail or _stringify(reason.get("message"))
        return _join_text([f"error {detail}".strip()])
    if kind == "aborted":
        return "aborted"
    if kind in ("max-tokens", "interrupted"):
        return kind
    return ""


def extract_event_text(event: SessionEvent) -> str:
    """从当前 Python 事件载荷中抽取可全文检索的语义文本。"""
    data = to_jsonable(event.data) if isinstance(event.data, Mapping) else {}
    kind = event.type

    if kind == "user/message":
        return _content_text(data.get("text"))

    if kind == "assistant/message":
        return _content_text(data.get("content"))

    if kind == "tool/call":
        return _join_text([data.get("name"), data.get("input")])

    if kind == "tool/result":
        results = data.get("results")
        parts: list[str] = []
        if isinstance(results, list):
            parts.extend(_content_text(item.get("content")) for item in results
                         if isinstance(item, dict))
        error = data.get("error")
        if isinstance(error, dict):
            parts.extend([error.get("name"), error.get("code")])
        return _join_text(parts)

    if kind == "todo/write":
        todos = data.get("todos")
        if not isinstance(todos, list):
            return ""
        rows = []
        for todo in todos:
            if isinstance(todo, dict):
                rows.append(_join_text([todo.get("status"), todo.get("content")]))
            else:
                rows.append(_content_text(todo))
        return _join_text(rows)

    if kind == "turn/end":
        return _turn_end_text(data)

    return ""
# ---------------------------------------------------------------------------
# 工作区授权与可用性
# ---------------------------------------------------------------------------


def _caller_header(store: SessionStore, ctx: ToolRunContext) -> SessionHeader:
    """取调用方所在会话的头信息。"""
    session_id = getattr(ctx, "session_id", None)
    if not session_id:
        raise _fail(
            "SESSION_QUERY_TOOL_UNAUTHORIZED",
            tr('python.session_query_tools.f23db84253f3'),
        )
    try:
        session = store.get(session_id)
    except SessionNotFoundError as exc:
        raise _fail("SESSION_QUERY_TOOL_UNAUTHORIZED", tr('python.session_query_tools.712ad02ee6ab', p0=f'{session_id}')) from exc
    return session.header


def _caller_cwd(ctx: ToolRunContext) -> str | None:
    """返回调用方声明的工作区目录（``ToolRunContext.cwd``）。

    生产运行时该值与当前会话头部的 ``cwd`` 一致；单元测试与嵌入场景可以单独
    指定，因此跨会话授权统一以它为准。
    """
    return getattr(ctx, "cwd", None)


def _scoped_caller(caller: SessionHeader, caller_cwd: str | None) -> SessionHeader:
    """按调用方声明的工作区重写头部 ``cwd``，为空时保留原值。

    这样跨会话目标按 ``caller_cwd`` 判定，而自会话（``caller_cwd`` 为空）
    仍按会话头部自己的 ``cwd`` 放行。
    """
    if caller_cwd is None or _norm_cwd(caller_cwd) == _norm_cwd(caller.cwd):
        return caller
    return replace(caller, cwd=caller_cwd)


def _header_authorized(header: SessionHeader, caller: SessionHeader) -> bool:
    """判断一个会话头对调用方是否可见（等价 TS ``headerAuthorized``）。

    调用方自己的会话要求 ``cwd`` 严格相等；其它会话要求调用方已绑定
    ``cwd`` 且完全相同——没有工作区的调用方看不到任何他人会话。
    """
    if header.id == caller.id:
        return _norm_cwd(header.cwd) == _norm_cwd(caller.cwd)
    if caller.cwd is None:
        return False
    return _norm_cwd(header.cwd) == _norm_cwd(caller.cwd)


def _availability(store: SessionStore) -> tuple[set[str], set[str]]:
    """返回 ``(存活会话 id 集合, 已落盘会话 id 集合)``。"""
    live = {session.id for session in store.loaded_sessions()}
    persisted = {header.id for header in store.list_sessions()}
    return live, persisted


def _availability_text(live: bool, persisted: bool) -> str:
    """渲染可用性文案（等价 TS ``availabilityText``）。"""
    parts = [name for name, on in (("live", live), ("persisted", persisted)) if on]
    return ", ".join(parts) if parts else "unavailable"


def _header_index(store: SessionStore) -> dict[str, SessionHeader]:
    """列出全部已知会话头，按 id 建索引。"""
    return {header.id: header for header in store.list_sessions()}


def _resolve_target(
    store: SessionStore,
    ctx: ToolRunContext,
    caller: SessionHeader,
    session_id: str | None,
) -> tuple[Session, SessionHeader]:
    """解析并授权目标会话；``session_id`` 为空时即调用方当前会话。"""
    caller = _scoped_caller(caller, _caller_cwd(ctx))
    target_id = session_id or caller.id
    try:
        session = store.get(target_id)
    except SessionNotFoundError as exc:
        raise _fail("SESSION_QUERY_TOOL_UNAUTHORIZED", tr('python.session_query_tools.1da89c3b041d', p0=f'{target_id}')) from exc
    header = session.header
    if not _header_authorized(header, caller):
        raise _fail(
            "SESSION_QUERY_TOOL_UNAUTHORIZED",
            tr('python.session_query_tools.c75dbe632946', p0=f'{target_id}'),
            sessionId=target_id,
        )
    return session, header


# ---------------------------------------------------------------------------
# 谱系追溯
# ---------------------------------------------------------------------------


def _record_node(header: SessionHeader, live: set[str], persisted: set[str]) -> dict[str, Any]:
    """把会话头投影成谱系节点。"""
    return {
        "id": header.id,
        "title": header.title or tr('query.untitled'),
        "createdAt": header.created_at,
        "availability": _availability_text(
            header.id in live,
            header.id in persisted,
        ),
    }


def trace_session(
    store: SessionStore,
    target_id: str,
    caller: SessionHeader,
) -> dict[str, Any]:
    """追溯一个会话的祖先与后代谱系。

    :returns: ``{"target": 节点, "ancestors": [...], "ancestorBoundary": bool,
                "descendants": [(depth, 节点|None)], "descendantsCapped": bool, "complete": bool,
                "unresolvedParentId": str|None}``

    越权祖先会被折叠成一个边界标记（``ancestorBoundary=True``），越权后代
    投影为 ``None``，由渲染层输出 ``[outside workspace subtree]``。
    """
    index = _header_index(store)
    live, persisted = _availability(store)
    if target_id not in index:
        raise _fail("SESSION_QUERY_TOOL_NOT_FOUND", tr('python.session_query_tools.3edfca498077', p0=f'{target_id}'), sessionId=target_id)
    target = index[target_id]

    # -- 祖先：最近优先，遇环直接判为非法谱系 --------------------------------
    ancestors: list[dict[str, Any]] = []
    ancestor_boundary = False
    complete = True
    unresolved_parent: str | None = None
    seen = {target_id}
    cursor = target.parent_session
    while cursor is not None:
        if cursor in seen:
            raise _fail(
                "SESSION_QUERY_INVALID_LINEAGE",
                tr('python.session_query_tools.3860149b18ab', p0=f'{target_id}'),
                sessionId=target_id,
            )
        seen.add(cursor)
        parent = index.get(cursor)
        if parent is None:
            complete = False
            unresolved_parent = cursor
            ancestor_boundary = True
            break
        if not _header_authorized(parent, caller):
            ancestor_boundary = True
            break
        ancestors.append(_record_node(parent, live, persisted))
        cursor = parent.parent_session

    # -- 后代：按 (created_at, id) 稳定排序，深度优先递归 ----------------------
    children: dict[str, list[SessionHeader]] = {}
    for header in index.values():
        if header.parent_session is not None:
            children.setdefault(header.parent_session, []).append(header)
    for group in children.values():
        group.sort(key=lambda item: (item.created_at, item.id))

    descendants: list[tuple[int, dict[str, Any] | None]] = []
    visited: set[str] = {target_id}
    budget = MAX_LINEAGE_NODES
    descendants_capped = False

    def _walk(node_id: str, depth: int) -> None:
        nonlocal budget, complete, descendants_capped
        for child in children.get(node_id, ()):
            if budget <= 0:
                complete = False
                descendants_capped = True
                return
            budget -= 1
            if child.id in visited:
                continue
            visited.add(child.id)
            # 越权后代投影成 None，由渲染层输出 [outside workspace subtree]。
            record: dict[str, Any] | None = (
                _record_node(child, live, persisted) if _header_authorized(child, caller) else None
            )
            descendants.append((depth, record))
            _walk(child.id, depth + 1)

    _walk(target_id, 0)
    return {
        "target": _record_node(target, live, persisted),
        "ancestors": ancestors,
        "ancestorBoundary": ancestor_boundary,
        "descendants": descendants,
        "descendantsCapped": descendants_capped,
        "complete": complete,
        "unresolvedParentId": unresolved_parent,
    }


# ---------------------------------------------------------------------------
# 当前 step 边界
# ---------------------------------------------------------------------------


def current_step_bound(session: Session) -> int:
    """返回最近 step 的起始 ``seq``；没有 step 记录时报错。

    会话内检索必须排除"正在执行本次调用的那个 step"，否则模型会把自己的
    工具调用读回来，形成自我污染。
    ``step/end`` 记录模型响应完成，工具随后执行，不能据此解除边界。
    """
    bound: int | None = None
    for event in session.events:
        if event.type == "step/start":
            bound = event.seq
    if bound is None:
        raise _fail(
            "SESSION_QUERY_TOOL_NO_CURRENT_STEP",
            tr('python.session_query_tools.7614ac2117a0'),
            sessionId=session.id,
        )
    return bound
# ---------------------------------------------------------------------------
# 过滤与打分
# ---------------------------------------------------------------------------


def _normalize_query(query: Any) -> str:
    """规整检索词；空串视为非法过滤条件。"""
    if not isinstance(query, str):
        raise _fail("SESSION_QUERY_INVALID_FILTER", tr('python.session_query_tools.192e79db96ac'))
    text = query.strip()
    if not text:
        raise _fail("SESSION_QUERY_INVALID_FILTER", tr('python.session_query_tools.8cf8b91b827b'))
    return text


def _score(text: str, query: str) -> int:
    """统计小写子串出现次数作为相关度打分。"""
    if not text:
        return 0
    return text.lower().count(query.lower())


def _event_matches(
    event: SessionEvent,
    surface: str,
    *,
    seq_from: int | None,
    seq_to: int | None,
    time_from: int | None,
    time_to: int | None,
    event_types: set[str] | None,
    surfaces: set[str] | None,
    max_seq: int | None,
) -> bool:
    """判断一条事件是否落在所有事件级过滤条件内。"""
    if max_seq is not None and event.seq >= max_seq:
        return False
    if seq_from is not None and event.seq < seq_from:
        return False
    if seq_to is not None and event.seq > seq_to:
        return False
    if time_from is not None and event.time < time_from:
        return False
    if time_to is not None and event.time > time_to:
        return False
    if event_types is not None and event.type not in event_types:
        return False
    if surfaces is not None and surface not in surfaces:
        return False
    return True


def _best_hit(
    session: Any,
    log: EventLog,
    query: str,
    *,
    seq_from: int | None,
    seq_to: int | None,
    time_from: int | None,
    time_to: int | None,
    event_types: set[str] | None,
    surfaces: set[str] | None,
    max_seq: int | None,
) -> dict[str, Any] | None:
    """在单个会话里挑出最相关的一条命中事件。

    打分相同时取更小的 ``seq``（更早、更接近根因）。
    """
    best: dict[str, Any] | None = None
    best_score = 0
    for event in session.events:
        surface = log.surface_of(event.seq)
        if not _event_matches(
            event,
            surface,
            seq_from=seq_from,
            seq_to=seq_to,
            time_from=time_from,
            time_to=time_to,
            event_types=event_types,
            surfaces=surfaces,
            max_seq=max_seq,
        ):
            continue
        text = extract_event_text(event)
        score = _score(text, query)
        if score == 0:
            continue
        if best is None or score > best_score or (score == best_score and event.seq < best["seq"]):
            best = {
                "seq": event.seq,
                "type": event.type,
                "surface": surface,
                "time": event.time,
                "snippet": _snippet(text),
                "score": score,
            }
            best_score = score
    return best


def _all_hits(
    session: Any,
    log: EventLog,
    query: str,
    *,
    seq_from: int | None,
    seq_to: int | None,
    time_from: int | None,
    time_to: int | None,
    event_types: set[str] | None,
    surfaces: set[str] | None,
    max_seq: int | None,
    limit: int,
) -> tuple[list[dict[str, Any]], bool]:
    """收集单个会话内全部命中事件（按 ``seq`` 升序）。

    :returns: ``(命中列表, 是否触顶)``
    """
    hits: list[dict[str, Any]] = []
    capped = False
    for event in session.events:
        surface = log.surface_of(event.seq)
        if not _event_matches(
            event,
            surface,
            seq_from=seq_from,
            seq_to=seq_to,
            time_from=time_from,
            time_to=time_to,
            event_types=event_types,
            surfaces=surfaces,
            max_seq=max_seq,
        ):
            continue
        text = extract_event_text(event)
        if _score(text, query) == 0:
            continue
        if len(hits) == limit:
            capped = True
            break
        hits.append(
            {
                "seq": event.seq,
                "type": event.type,
                "surface": surface,
                "time": event.time,
                "snippet": _snippet(text),
            }
        )
    return hits, capped


def _availability_set(requested: Any, field_name: str) -> set[str] | None:
    """规整 ``availability`` / ``surfaces`` 枚举数组。"""
    values = _as_str_list(requested, field_name)
    if values is None:
        return None
    allowed = {"live", "persisted"} if field_name == "availability" else {"current", "shadowed", "log-only"}
    bad = [value for value in values if value not in allowed]
    if bad:
        raise _fail(
            "SESSION_QUERY_INVALID_FILTER",
            tr('python.session_query_tools.6871b527d571', p0=f'{field_name}', p1=tr('ui.common.list_separator').join(bad)),
        )
    return set(values)


def _parent_filter_ok(
    header: SessionHeader,
    parent_ids: set[str] | None,
    include_root: bool,
) -> bool:
    """判断会话是否满足父会话过滤条件。"""
    if parent_ids is None and not include_root:
        return True
    if header.parent_session is None:
        return include_root
    return header.parent_session in (parent_ids or set())
# ---------------------------------------------------------------------------
# 渲染（字符串格式与原 TS 版逐字对齐）
# ---------------------------------------------------------------------------


def _title(header: SessionHeader) -> str:
    """渲染会话标题；缺标题时回落到 ``untitled``。"""
    return header.title or tr('query.untitled')


def _seq_list(values: Sequence[int]) -> str:
    """渲染 seq 列表；空列表输出 ``none``。"""
    return ", ".join(str(value) for value in values) if values else tr('query.none')


def render_session_search(
    items: Sequence[dict[str, Any]],
    capped: bool,
    authorized_parents: set[str],
) -> str:
    """渲染跨会话检索结果。"""
    if not items:
        return tr('query.no_sessions')
    lines = [tr('query.session_results', count=len(items))]
    for index, hit in enumerate(items):
        header = hit["header"]
        parent = tr('query.root')
        if header.parent_session is not None:
            parent = (
                header.parent_session
                if header.parent_session in authorized_parents
                else tr('query.outside')
            )
        best = hit["best"]
        lines += [
            "",
            f"{index + 1}. " + tr('query.session', id=header.id, title=_title(header)),
            '   ' + tr('query.created', time=_iso(header.created_at)),
            '   ' + tr('query.parent', parent=parent),
            '   ' + tr('query.availability', value=hit['availability']),
            '   ' + tr('query.best', seq=best['seq'], type=best['type'], surface=best['surface'], time=_iso(best['time'])),
            '   ' + tr('query.snippet', text=best['snippet']),
        ]
    if capped:
        lines += ["", tr('query.capped')]
    return "\n".join(lines)


def render_event_search(session_id: str, title: str, hits: Sequence[dict[str, Any]], capped: bool) -> str:
    """渲染单会话内的事件检索结果。"""
    lines = [tr('query.session', id=session_id, title=title)]
    if not hits:
        lines += ["", tr('query.no_events')]
        return "\n".join(lines)
    lines += ["", tr('query.event_results', count=len(hits))]
    for index, hit in enumerate(hits):
        lines += [
            f"{index + 1}. seq {hit['seq']} | {hit['type']} | {hit['surface']} | {_iso(hit['time'])}",
            '   ' + tr('query.snippet', text=hit['snippet']),
        ]
    if capped:
        lines += ["", tr('query.capped')]
    return "\n".join(lines)


def render_session_trace(trace: dict[str, Any]) -> str:
    """渲染会话谱系追溯结果。"""
    target = trace["target"]
    lines = [
        tr('query.session', id=target['id'], title=target['title']),
        tr('query.created', time=_iso(target['createdAt'])),
        tr('query.availability', value=target['availability']),
        "",
        tr('query.ancestors'),
    ]
    ancestors = trace["ancestors"]
    if not ancestors and not trace["ancestorBoundary"]:
        lines.append(tr('query.root_ancestor'))
    for node in ancestors:
        lines.append(f"- {node['id']} — {node['title']} | {_iso(node['createdAt'])} | {node['availability']}")
    if trace["ancestorBoundary"]:
        lines.append('- ' + tr('query.outside_boundary'))
    lines += ["", tr('query.descendants')]
    descendants = trace["descendants"]
    if not descendants:
        lines.append('- ' + tr('query.none'))
    else:
        for depth, node in descendants:
            indent = "  " * depth
            if node is None:
                lines.append(f'{indent}- ' + tr('query.outside_subtree'))
            else:
                lines.append(
                    f"{indent}- {node['id']} — {node['title']} | {_iso(node['createdAt'])} | {node['availability']}"
                )
    if trace["descendantsCapped"]:
        lines += ["", tr('query.lineage_capped', limit=MAX_LINEAGE_NODES)]
    return "\n".join(lines)


def render_event_trace(session_id: str, title: str, trace: dict[str, Any]) -> str:
    """渲染单事件的关系追溯结果。"""
    target = trace["target"]
    return "\n".join(
        [
            tr('query.session', id=session_id, title=title),
            tr('query.target', seq=target['seq'], type=target['type'], surface=target['surface'], time=_iso(target['time'])),
            tr('query.replaced_by', value=trace['replacedBy'] if trace['replacedBy'] is not None else tr('query.none')),
            tr('query.replacement_chain', value=_seq_list(trace['replacementChain'])),
            tr('query.replaced_events', value=_seq_list(trace['replacedEventSeqs'])),
            tr('query.sources', value=_seq_list(trace['sourceEventSeqs'])),
            tr('query.derived', value=_seq_list(trace['derivedEventSeqs'])),
        ]
    )


def _format_neighbor(event: SessionEvent) -> str:
    """渲染邻接事件摘要行（无文本时显式标注）。"""
    head = f"- seq {event.seq} | {event.type} | {_iso(event.time)}"
    text = extract_event_text(event)
    if not text:
        return head + ' | ' + tr('query.no_text')
    return head + "\n  " + text.replace("\n", "\n  ")


def render_event_read(session_id: str, title: str, window: dict[str, Any]) -> str:
    """渲染单事件全文及其前后邻居。"""
    target = window["target"]
    lines = [
        tr('query.session', id=session_id, title=title),
        tr('query.event_seq', seq=target.seq),
        "```json",
        json.dumps(target.to_dict(), ensure_ascii=False, indent=2),
        "```",
    ]
    before = window["before"]
    if before:
        lines += ["", tr('query.before')]
        lines += [_format_neighbor(event) for event in before]
    after = window["after"]
    if after:
        lines += ["", tr('query.after')]
        lines += [_format_neighbor(event) for event in after]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 工具层：把算法层暴露成五个模型可调用的工具
# ---------------------------------------------------------------------------

#: 注入系统提示的会话检索引导。
SESSION_QUERY_PROMPT_KEY = 'owned.session_query_tools.aa7040015ebe'

#: ``session_event_read`` 允许的最大邻居条数。
MAX_NEIGHBORS = 20



def _int_arg(args: dict[str, Any], name: str) -> int | None:
    """读取可选整数参数；缺失返回 ``None``。"""
    value = args.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail("SESSION_QUERY_INVALID_FILTER", tr('python.session_query_tools.40a57d561df3', p0=f'{name}'))
    return value


def _max_results(args: dict[str, Any]) -> int:
    """读取 ``max_results`` 并夹到 ``[1, MAX_RESULTS_CEILING]``。"""
    value = args.get("max_results")
    if value is None:
        return DEFAULT_MAX_RESULTS
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise _fail("SESSION_QUERY_INVALID_FILTER", tr('python.session_query_tools.a09f67f4ebde'))
    return min(value, MAX_RESULTS_CEILING)


def _result(name: str, text: str, value: dict[str, Any]) -> ToolResult:
    """统一构造工具返回值：正文用渲染文本，UI 用结构化数据。

    渲染文本挂在 ``renderedText`` 上，方便 UI 直接展示。
    """
    payload = {"name": name, "renderedText": text, **value}
    return ToolResult(content=[TextBlock(text=text)], display=payload, data=payload)


def build_session_query_tools(store: SessionStore) -> list[ToolDefinition]:
    """构造五个会话检索工具。

    所有工具共用同一个 :class:`SessionStore`，并在每次调用时按调用方
    ``ToolRunContext.cwd`` 重新做一次工作区授权判定——历史会话可能是在
    另一个工作区里产生的，不能因为它已经落盘就默认可见。
    """

    # ------------------------------------------------------------------
    # session_search：跨会话检索，每个会话只回一条最强命中
    # ------------------------------------------------------------------
    def session_search(args: dict[str, Any], ctx: ToolRunContext) -> ToolResult:
        live_ids, persisted_ids = _availability(store)
        caller_cwd = _caller_cwd(ctx)
        caller = _scoped_caller(_caller_header(store, ctx), caller_cwd)
        if caller_cwd is None:
            raise _fail(
                "SESSION_QUERY_TOOL_UNAUTHORIZED",
                tr('python.session_query_tools.6a1b33fa1d16'),
            )
        query = _normalize_query(args.get("query"))
        limit = _max_results(args)

        wanted_ids = _as_str_list(args.get("session_ids"), "session_ids")
        wanted_parents = _as_str_list(args.get("parent_session_ids"), "parent_session_ids")
        include_root = bool(args.get("include_root_sessions") or False)
        availability = _availability_set(args.get("availability"), "availability")
        event_types = (
            set(_as_str_list(args.get("event_types"), "event_types") or ())
            if args.get("event_types") is not None
            else None
        )
        surfaces = _availability_set(args.get("event_surfaces"), "event_surfaces")
        seq_from = _int_arg(args, "event_seq_from")
        seq_to = _int_arg(args, "event_seq_to")
        time_from = _parse_time(args.get("event_time_from"), "event_time_from")
        time_to = _parse_time(args.get("event_time_to"), "event_time_to")
        created_from = _parse_time(args.get("created_at_from"), "created_at_from")
        created_to = _parse_time(args.get("created_at_to"), "created_at_to")

        index = _header_index(store)
        authorized = {
            header.id
            for header in index.values()
            if _norm_cwd(header.cwd) == _norm_cwd(caller_cwd) and _header_authorized(header, caller)
        }

        items: list[dict[str, Any]] = []
        for header in index.values():
            # 排除调用方自己：否则"搜历史"会退化成"复述当前对话"。
            if header.id == caller.id or _norm_cwd(header.cwd) != _norm_cwd(caller_cwd):
                continue
            if wanted_ids is not None and header.id not in wanted_ids:
                continue
            if created_from is not None and header.created_at < created_from:
                continue
            if created_to is not None and header.created_at > created_to:
                continue
            if not _parent_filter_ok(header, set(wanted_parents) if wanted_parents else None, include_root):
                continue
            if availability is not None:
                flags = {
                    name
                    for name, on in (
                        ("live", header.id in live_ids),
                        ("persisted", header.id in persisted_ids),
                    )
                    if on
                }
                if not flags & availability:
                    continue
            try:
                session = store.get(header.id)
            except SessionNotFoundError:
                continue
            log = analyze_event_log(session.events)
            best = _best_hit(
                session,
                log,
                query,
                seq_from=seq_from,
                seq_to=seq_to,
                time_from=time_from,
                time_to=time_to,
                event_types=event_types,
                surfaces=surfaces,
                max_seq=None,
            )
            if best is None:
                continue
            items.append(
                {
                    "header": header,
                    "availability": _availability_text(
                        header.id in live_ids,
                        header.id in persisted_ids,
                    ),
                    "best": best,
                }
            )

        items.sort(key=lambda item: (-item["best"]["score"], -item["header"].created_at, item["header"].id))
        capped = len(items) > limit
        items = items[:limit]
        text = render_session_search(items, capped, authorized)
        value = {
            "query": query,
            "count": len(items),
            "results": [
                {
                    "sessionId": item["header"].id,
                    "title": _title(item["header"]),
                    "createdAt": item["header"].created_at,
                    "parentSession": item["header"].parent_session if item["header"].parent_session in authorized else None,
                    "availability": item["availability"],
                    "best": item["best"],
                }
                for item in items
            ],
        }
        return _result("session_search", text, value)

    # ------------------------------------------------------------------
    # session_event_search：单会话内的事件检索
    # ------------------------------------------------------------------
    def session_event_search(args: dict[str, Any], ctx: ToolRunContext) -> ToolResult:
        caller = _caller_header(store, ctx)
        session, header = _resolve_target(store, ctx, caller, args.get("session_id"))
        query = _normalize_query(args.get("query"))
        limit = _max_results(args)
        seq_from = _int_arg(args, "seq_from")
        seq_to = _int_arg(args, "seq_to")
        time_from = _parse_time(args.get("time_from"), "time_from")
        time_to = _parse_time(args.get("time_to"), "time_to")
        event_types = (
            set(_as_str_list(args.get("event_types"), "event_types") or ())
            if args.get("event_types") is not None
            else None
        )
        surfaces = _availability_set(args.get("surfaces"), "surfaces")

        # 搜当前会话时必须先裁掉本 step：本工具自己的调用轨迹不能进结果。
        max_seq: int | None = None
        if header.id == caller.id:
            max_seq = current_step_bound(session)
            if seq_to is not None:
                seq_to = min(seq_to, max_seq - 1)

        # 表面归属也按边界前的日志折叠，不能让本步骤的替换污染历史分类。
        history = session.events
        if max_seq is not None:
            history = [event for event in history if event.seq < max_seq]
        log = analyze_event_log(history)
        hits, capped = _all_hits(
            session,
            log,
            query,
            seq_from=seq_from,
            seq_to=seq_to,
            time_from=time_from,
            time_to=time_to,
            event_types=event_types,
            surfaces=surfaces,
            max_seq=max_seq,
            limit=limit,
        )
        text = render_event_search(header.id, _title(header), hits, capped)
        return _result("session_event_search", text, {"query": query, "count": len(hits), "hits": hits})

    # ------------------------------------------------------------------
    # session_trace：会话谱系
    # ------------------------------------------------------------------
    def session_trace(args: dict[str, Any], ctx: ToolRunContext) -> ToolResult:
        caller = _scoped_caller(_caller_header(store, ctx), _caller_cwd(ctx))
        session_id = args.get("session_id")
        target_id = session_id or caller.id
        if target_id != caller.id:
            target_header = _header_index(store).get(target_id)
            if (
                target_header is None
                or caller.cwd is None
                or _norm_cwd(target_header.cwd) != _norm_cwd(caller.cwd)
            ):
                raise _fail(
                    "SESSION_QUERY_TOOL_UNAUTHORIZED",
                    tr('python.session_query_tools.c75dbe632946', p0=f'{target_id}'),
                    sessionId=target_id,
                )
        trace = trace_session(store, target_id, caller)
        text = render_session_trace(trace)
        return _result("session_trace", text, trace)

    # ------------------------------------------------------------------
    # session_event_trace：单事件的取代 / 来源 / 派生关系
    # ------------------------------------------------------------------
    def session_event_trace(args: dict[str, Any], ctx: ToolRunContext) -> ToolResult:
        caller = _caller_header(store, ctx)
        session, header = _resolve_target(store, ctx, caller, args.get("session_id"))
        seq = args.get("seq")
        if isinstance(seq, bool) or not isinstance(seq, int):
            raise _fail("SESSION_QUERY_INVALID_FILTER", tr('python.session_query_tools.6984d5ad99a5'))
        log = analyze_event_log(session.events)
        target = log.records.get(seq)
        if target is None:
            raise _fail(
                "SESSION_QUERY_TOOL_NOT_FOUND",
                tr('python.session_query_tools.5a19609de764', p0=f'{seq}'),
                sessionId=header.id,
                seq=seq,
            )
        derived = [event.seq for event in session.events if seq in (event.source_event_seqs or ())]
        value = {
            "target": {
                "seq": target.seq,
                "type": target.type,
                "surface": log.surface_of(target.seq),
                "time": target.time,
            },
            "replacedBy": log.replaced_by.get(seq),
            "replacementChain": log.replacement_chain(seq),
            "replacedEventSeqs": list(log.replaced_event_seqs.get(seq, [])),
            "sourceEventSeqs": list(target.source_event_seqs or []),
            "derivedEventSeqs": derived,
        }
        text = render_event_trace(header.id, _title(header), value)
        return _result("session_event_trace", text, value)

    # ------------------------------------------------------------------
    # session_event_read：单事件全文 + 前后邻居
    # ------------------------------------------------------------------
    def session_event_read(args: dict[str, Any], ctx: ToolRunContext) -> ToolResult:
        caller = _caller_header(store, ctx)
        session, header = _resolve_target(store, ctx, caller, args.get("session_id"))
        seq = args.get("seq")
        if isinstance(seq, bool) or not isinstance(seq, int):
            raise _fail("SESSION_QUERY_INVALID_FILTER", tr('python.session_query_tools.6984d5ad99a5'))
        target = session.event_at(seq)
        if target is None:
            raise _fail(
                "SESSION_QUERY_TOOL_NOT_FOUND",
                tr('python.session_query_tools.5a19609de764', p0=f'{seq}'),
                sessionId=header.id,
                seq=seq,
            )

        before_count = args.get("before")
        if before_count is None:
            before_count = DEFAULT_NEIGHBORS
        after_count = args.get("after")
        if after_count is None:
            after_count = DEFAULT_NEIGHBORS

        window = {
            "target": target,
            "before": [event for event in session.events if target.seq - before_count <= event.seq < target.seq],
            "after": [event for event in session.events if target.seq < event.seq <= target.seq + after_count],
        }
        text = render_event_read(header.id, _title(header), window)
        return _result(
            "session_event_read",
            text,
            {
                "seq": target.seq,
                "type": target.type,
                "before": [event.seq for event in window["before"]],
                "after": [event.seq for event in window["after"]],
            },
        )

    _TIME_PROPS = {
        "created_at_from": {"type": "string", "description": tr('python.session_query_tools.9cbd67aea882', p0=tr('python.session_query_tools.6ab5f6420476'))},
        "created_at_to": {"type": "string", "description": tr('python.session_query_tools.abd70808fba1', p0=tr('python.session_query_tools.6ab5f6420476'))},
    }

    return [
        ToolDefinition(
            schema={
                "description_key": "owned.session_query_tools.4c4f4e57781f",
                "parameter_keys": {
                    ('properties', 'query', 'description'): 'python.session_query_tools.a2e843b52fb1',
                    ('properties', 'session_ids', 'description'): 'python.session_query_tools.8e0bc88c9cf7',
                    ('properties', 'created_at_from', 'description'): ('python.session_query_tools.9cbd67aea882', {'p0': SchemaResource('python.session_query_tools.6ab5f6420476')}),
                    ('properties', 'created_at_to', 'description'): ('python.session_query_tools.abd70808fba1', {'p0': SchemaResource('python.session_query_tools.6ab5f6420476')}),
                    ('properties', 'parent_session_ids', 'description'): 'python.session_query_tools.d3da0a841304',
                    ('properties', 'include_root_sessions', 'description'): 'python.session_query_tools.e6ba52c4cfb9',
                    ('properties', 'availability', 'description'): 'python.session_query_tools.b3bb57e1eca4',
                    ('properties', 'event_seq_from', 'description'): 'python.session_query_tools.bb23d2197b63',
                    ('properties', 'event_seq_to', 'description'): 'python.session_query_tools.6c276a826f03',
                    ('properties', 'event_time_from', 'description'): ('python.session_query_tools.9b79f517d5a2', {'p0': SchemaResource('python.session_query_tools.6ab5f6420476')}),
                    ('properties', 'event_time_to', 'description'): ('python.session_query_tools.4ee85d914818', {'p0': SchemaResource('python.session_query_tools.6ab5f6420476')}),
                    ('properties', 'event_types', 'description'): 'python.session_query_tools.f5358f02014f',
                    ('properties', 'event_surfaces', 'description'): 'python.session_query_tools.0af88283b89a',
                    ('properties', 'max_results', 'description'): ('python.session_query_tools.2b97e8f9c016', {'p0': f'{DEFAULT_MAX_RESULTS}'}),
                },
                "name": "session_search",
                "description": (
                    tr('owned.session_query_tools.4c4f4e57781f')
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "minLength": 1, "description": tr('python.session_query_tools.a2e843b52fb1')},
                        "session_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": tr('python.session_query_tools.8e0bc88c9cf7'),
                        },
                        **_TIME_PROPS,
                        "parent_session_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": tr('python.session_query_tools.d3da0a841304'),
                        },
                        "include_root_sessions": {
                            "type": "boolean",
                            "description": tr('python.session_query_tools.e6ba52c4cfb9'),
                        },
                        "availability": {
                            "type": "array",
                            "items": {"type": "string", "enum": list(AVAILABILITY_VALUES)},
                            "description": tr('python.session_query_tools.b3bb57e1eca4'),
                        },
                        "event_seq_from": {"type": "integer", "minimum": 0, "description": tr('python.session_query_tools.bb23d2197b63')},
                        "event_seq_to": {"type": "integer", "minimum": 0, "description": tr('python.session_query_tools.6c276a826f03')},
                        "event_time_from": {"type": "string", "description": tr('python.session_query_tools.9b79f517d5a2', p0=tr('python.session_query_tools.6ab5f6420476'))},
                        "event_time_to": {"type": "string", "description": tr('python.session_query_tools.4ee85d914818', p0=tr('python.session_query_tools.6ab5f6420476'))},
                        "event_types": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": tr('python.session_query_tools.f5358f02014f'),
                        },
                        "event_surfaces": {
                            "type": "array",
                            "items": {"type": "string", "enum": list(SURFACE_VALUES)},
                            "description": tr('python.session_query_tools.0af88283b89a'),
                        },
                        "max_results": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": MAX_RESULTS_CEILING,
                            "description": tr('python.session_query_tools.2b97e8f9c016', p0=f'{DEFAULT_MAX_RESULTS}'),
                        },
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            },
            execute=session_search,
            concurrency_safe=False,
            timeout=30,
        ),
        ToolDefinition(
            schema={
                "description_key": 'owned.session_query_tools.ef8b86a262b8',
                "parameter_keys": {
                    ('properties', 'session_id', 'description'): 'python.session_query_tools.5d3384234a0b',
                    ('properties', 'query', 'description'): 'python.session_query_tools.a2e843b52fb1',
                    ('properties', 'seq_from', 'description'): 'python.session_query_tools.bb23d2197b63',
                    ('properties', 'seq_to', 'description'): 'python.session_query_tools.6c276a826f03',
                    ('properties', 'time_from', 'description'): ('python.session_query_tools.9b79f517d5a2', {'p0': SchemaResource('python.session_query_tools.6ab5f6420476')}),
                    ('properties', 'time_to', 'description'): ('python.session_query_tools.4ee85d914818', {'p0': SchemaResource('python.session_query_tools.6ab5f6420476')}),
                    ('properties', 'event_types', 'description'): 'python.session_query_tools.4cc2462f474b',
                    ('properties', 'surfaces', 'description'): 'python.session_query_tools.e3287e4063fa',
                    ('properties', 'max_results', 'description'): ('python.session_query_tools.e055e1d2e0d2', {'p0': f'{DEFAULT_MAX_RESULTS}'}),
                },
                "name": "session_event_search",
                "description": (
                    tr('owned.session_query_tools.ef8b86a262b8')
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string", "description": tr('python.session_query_tools.5d3384234a0b')},
                        "query": {"type": "string", "minLength": 1, "description": tr('python.session_query_tools.a2e843b52fb1')},
                        "seq_from": {"type": "integer", "minimum": 0, "description": tr('python.session_query_tools.bb23d2197b63')},
                        "seq_to": {"type": "integer", "minimum": 0, "description": tr('python.session_query_tools.6c276a826f03')},
                        "time_from": {"type": "string", "description": tr('python.session_query_tools.9b79f517d5a2', p0=tr('python.session_query_tools.6ab5f6420476'))},
                        "time_to": {"type": "string", "description": tr('python.session_query_tools.4ee85d914818', p0=tr('python.session_query_tools.6ab5f6420476'))},
                        "event_types": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": tr('python.session_query_tools.4cc2462f474b'),
                        },
                        "surfaces": {
                            "type": "array",
                            "items": {"type": "string", "enum": list(SURFACE_VALUES)},
                            "description": tr('python.session_query_tools.e3287e4063fa'),
                        },
                        "max_results": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": MAX_RESULTS_CEILING,
                            "description": tr('python.session_query_tools.e055e1d2e0d2', p0=f'{DEFAULT_MAX_RESULTS}'),
                        },
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            },
            execute=session_event_search,
            timeout=30,
        ),
        ToolDefinition(
            schema={
                "description_key": 'owned.session_query_tools.d1c1c5e00dcf',
                "parameter_keys": {
                    ('properties', 'session_id', 'description'): 'python.session_query_tools.5d3384234a0b',
                },
                "name": "session_trace",
                "description": (
                    tr('owned.session_query_tools.d1c1c5e00dcf')
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string", "description": tr('python.session_query_tools.5d3384234a0b')},
                    },
                    "additionalProperties": False,
                },
            },
            execute=session_trace,
            concurrency_safe=True,
            timeout=30,
        ),
        ToolDefinition(
            schema={
                "description_key": 'owned.session_query_tools.70bd7cc7a805',
                "parameter_keys": {
                    ('properties', 'session_id', 'description'): 'python.session_query_tools.5d3384234a0b',
                    ('properties', 'seq', 'description'): 'python.session_query_tools.bf5d1498f2c9',
                },
                "name": "session_event_trace",
                "description": (
                    tr('owned.session_query_tools.70bd7cc7a805')
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string", "description": tr('python.session_query_tools.5d3384234a0b')},
                        "seq": {"type": "integer", "minimum": 0, "description": tr('python.session_query_tools.bf5d1498f2c9')},
                    },
                    "required": ["seq"],
                    "additionalProperties": False,
                },
            },
            execute=session_event_trace,
            concurrency_safe=True,
            timeout=30,
        ),
        ToolDefinition(
            schema={
                "description_key": 'owned.session_query_tools.43718b390080',
                "parameter_keys": {
                    ('properties', 'session_id', 'description'): 'python.session_query_tools.5d3384234a0b',
                    ('properties', 'seq', 'description'): 'python.session_query_tools.bf5d1498f2c9',
                    ('properties', 'before', 'description'): ('python.session_query_tools.f8bf2fdc80a3', {'p0': f'{DEFAULT_NEIGHBORS}'}),
                    ('properties', 'after', 'description'): ('python.session_query_tools.e10974b44369', {'p0': f'{DEFAULT_NEIGHBORS}'}),
                },
                "name": "session_event_read",
                "description": (
                    tr('owned.session_query_tools.43718b390080')
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string", "description": tr('python.session_query_tools.5d3384234a0b')},
                        "seq": {"type": "integer", "minimum": 0, "description": tr('python.session_query_tools.bf5d1498f2c9')},
                        "before": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": MAX_NEIGHBORS,
                            "description": tr('python.session_query_tools.f8bf2fdc80a3', p0=f'{DEFAULT_NEIGHBORS}'),
                        },
                        "after": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": MAX_NEIGHBORS,
                            "description": tr('python.session_query_tools.e10974b44369', p0=f'{DEFAULT_NEIGHBORS}'),
                        },
                    },
                    "required": ["seq"],
                    "additionalProperties": False,
                },
            },
            execute=session_event_read,
            concurrency_safe=True,
            timeout=30,
        ),
    ]


__all__ = [
    "AVAILABILITY_VALUES",
    "DEFAULT_MAX_RESULTS",
    "DEFAULT_NEIGHBORS",
    "MAX_LINEAGE_NODES",
    "MAX_NEIGHBORS",
    "MAX_RESULTS_CEILING",
    "SESSION_QUERY_PROMPT_KEY",
    "SURFACE_VALUES",
    "EventLog",
    "analyze_event_log",
    "build_session_query_tools",
    "current_step_bound",
    "extract_event_text",
    "fold_surface",
    "render_event_read",
    "render_event_search",
    "render_event_trace",
    "render_session_search",
    "render_session_trace",
    "trace_session",
]
