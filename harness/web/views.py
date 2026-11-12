"""会话 → 前端视图模型的投影。

后端只存"事件流"，前端要的是"一屏能直接画出来的结构"。这里负责把
事件 / 派生消息翻译成前端要的形状，并顺手算好统计：

* :func:`session_detail` —— 会话头 + 派生消息 + 统计 + 待办 + 待答问题；
* :func:`session_listing` —— 会话列表（带摘要、消息数、分组时间戳）；
* :func:`usage_summary` —— token 用量汇总（输入/输出/缓存/推理 + 上下文占用）。

**所有返回值必须能直接 ``json.dumps``**：会话事件里的 ``data`` 是
:func:`~harness.core.jsonutil.deep_freeze` 冻结后的 ``MappingProxyType``，
直接序列化会抛异常，所以这里统一走 ``to_jsonable``。
"""

from __future__ import annotations

import json
import threading
from collections import OrderedDict
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Iterable

from ..core.errors import SessionInvariantError
from ..i18n import tr
from ..core.jsonutil import to_jsonable
from ..session.tool_activity import tool_invocations
from ..session.events import user_message_text
from ..tools.categories import resolve as resolve_category

#: 触发"上下文即将满"的提示阈值（前端画进度条用）。
CONTEXT_WARN_RATIO = 0.75

#: 单个工具结果在列表摘要里保留的最大字符数（完整结果走详情接口）。
SUMMARY_CHAR_LIMIT = 220


def session_header_view(header: Any) -> dict[str, Any]:
    """公开文件系统标识，不改写所记录的执行工作目录。"""
    item = dict(to_jsonable(header)) if isinstance(header, Mapping) else header.to_dict()
    raw = item.get("cwd")
    item["resolved_cwd"] = None
    if isinstance(raw, str) and raw.strip():
        try:
            item["resolved_cwd"] = str(Path(raw).expanduser().resolve())
        except (OSError, RuntimeError, ValueError):
            # 路径删除或不可访问时，不能让会话列表消失。
            pass
    return item


def _text_of(value: Any, limit: int = SUMMARY_CHAR_LIMIT) -> str:
    """把任意负载压成一行摘要。"""
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    else:
        import json

        try:
            text = json.dumps(to_jsonable(value), ensure_ascii=False)
        except (TypeError, ValueError):
            text = str(value)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


# ---------------------------------------------------------------------------
# 消息投影
# ---------------------------------------------------------------------------


def _project_messages(session: Any, *, include_partials: bool = True, active_turn: int | None = None) -> list[dict[str, Any]]:
    """把派生消息转成前端可直接渲染的结构。"""

    messages: list[dict[str, Any]] = []
    for message in session.build_messages():
        kind = type(message).__name__
        if kind == "UserMessage":
            messages.append(
                {
                    "kind": kind,
                    "role": "user",
                    "text": message.text,
                    "source": message.source,
                }
            )
        elif kind in ("SystemMessage", "DeveloperMessage"):
            # 系统/开发者提示词**不是对话内容**。它们是配置，被拼进每一轮请求
            # 的最前面；当成一条消息投影出去，前端会把它画成会话里的第一条
            # "你" 气泡 —— 一个正常会话的第一屏就是几百行 <shell_rules>。
            # 这里直接跳过；要看它请走 :func:`session_prompts`。
            continue
        elif kind == "AssistantMessage":
            messages.append(
                {
                    "kind": "AssistantMessage",
                    "role": "assistant",
                    "text": message.text,
                    "thinking": message.thinking,
                    "tool_uses": [tool.to_dict() for tool in message.tool_uses],
                    "usage": message.usage.to_dict() if message.usage else None,
                    "model": getattr(message, "model", None),
                    "stop_reason": getattr(message, "stop_reason", None),
                }
            )
        elif kind == "ToolResultMessage":
            results = []
            for result in message.results:
                payload = result.to_dict()
                results.append(payload)
            messages.append({"kind": "ToolResultMessage", "role": "tool", "results": results})
        else:  # pragma: no cover - 未来新增消息类型时的兜底
            messages.append({"kind": kind, "role": "unknown", "text": str(message)})
    # 将展示消息关联到已提交消息表面，不改变模型消息，
    # 也不按追加序号重新排序已压缩的消息表面。
    # 遇到未知的未来投影时省略关联，不通过文本匹配猜测。
    roles = {'user/message': 'user', 'assistant/message': 'assistant', 'tool/result': 'tool'}
    sources = [event for event in session.surface if event.type in roles
               and not (event.type == 'user/message' and event.data.get('source') == 'plan-mode')]
    if len(sources) == len(messages) and all(roles[event.type] == message['role']
                                            for event, message in zip(sources, messages)):
        for event, message in zip(sources, messages):
            message.update(source_seq=event.seq, source_time=event.time)
            if event.type == 'tool/result':
                originals = event.data.get('results', ())
                for index, result in enumerate(message.get('results', ())):
                    if index >= len(originals) or originals[index].get('tool_use_id') != result.get('tool_use_id'):
                        continue
                    # 原件与元数据靠事件位置关联，不能用可能重复的调用标识覆盖旧结果。
                    if isinstance(originals[index].get('content'), str):
                        result['content'] = originals[index]['content']
                    for key in ('name', 'display', 'duration_ms'):
                        result.pop(key, None)
                        if event.data.get(key) is not None:
                            result[key] = to_jsonable(event.data[key])
            if event.type == 'assistant/message':
                for key in ('author', 'speaker', 'error', 'acceptance', 'team_members'):
                    if key in event.data:
                        message[key] = event.data[key]
            if event.type == 'user/message':
                message['text'] = user_message_text(event.data)
                for key in ('requester', 'run_id', 'attachments', 'goal_id', 'team_recipients', 'request_id', 'steering_status', 'steering_reason', 'steering_team_delivered', 'steering_delivery_seq'):
                    if key in event.data:
                        message[key] = event.data[key]
    header = getattr(session, 'header', None)
    links = header.metadata.get('goal_messages', {}) if header else {}
    for message in messages:
        linked = links.get(str(message.get('source_seq')))
        if message.get('role') == 'user' and isinstance(linked, str):
            message['goal_id'] = linked
    if include_partials:
        from ..session.partials import partial_response_events, project_partial_response
        evidence = session.response_evidence
        partials = [project_partial_response(event) for event in
                    partial_response_events(evidence, active_turn=active_turn)]
        # 按已提交序号合并，保留有序模型消息表面的压缩位置，
        # 不对全部投影消息重新排序。
        if partials:
            merged = []
            cursor = 0
            for message in messages:
                while cursor < len(partials) and partials[cursor]['source_seq'] < message.get('source_seq', float('inf')):
                    merged.append(partials[cursor])
                    cursor += 1
                merged.append(message)
            messages = merged + partials[cursor:]
    return messages


# ---------------------------------------------------------------------------
# 统计
# ---------------------------------------------------------------------------


def usage_summary(messages: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """汇总 token 用量。

    优先累加 ``assistant/message`` 上的 usage；若会话里没有 usage
    （例如刚创建），返回全 0，前端据此显示占位而不是隐藏进度条。
    """
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_reported_input_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 0,
        "requests": 0,
    }
    for message in messages:
        usage = message.get("usage")
        if usage is None:
            continue
        if not isinstance(usage, Mapping):
            raise SessionInvariantError(tr('web.usage.invalid'))
        totals["requests"] += 1
        for key in (
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_reported_input_tokens",
            "reasoning_tokens",
        ):
            value = usage.get(key)
            if value is not None:
                if type(value) is not int or value < 0:
                    raise SessionInvariantError(tr('web.usage.invalid'))
                totals[key] += value
        totals["total_tokens"] = (
            totals["input_tokens"]
            + totals["output_tokens"]
        )
    return totals


def _event_stats(events: list[Any]) -> dict[str, Any]:
    """从事件流算时间跨度与工具调用直方图。"""
    tools: dict[str, dict[str, int]] = {}
    errors = 0
    compactions = 0
    first_time = 0
    last_time = 0
    for event in events:
        ts = int(getattr(event, "time", 0) or 0)
        if ts:
            if not first_time or ts < first_time:
                first_time = ts
            last_time = max(last_time, ts)
        kind = getattr(event, "type", "")
        if kind == "compaction/start":
            compactions += 1
    for invocation in tool_invocations(events):
        if not invocation['counted']:
            continue
        name = str(invocation['name'])
        entry = tools.setdefault(name, {"count": 0, "errors": 0})
        entry['count'] += 1
        display = invocation.get('display')
        # 引导在安全边界跳过旧操作，错误标志用于模型恢复，不代表执行失败。
        steering_skipped = isinstance(display, Mapping) and display.get('steering_deferred') is True
        if invocation['is_error'] and not steering_skipped:
            entry['errors'] += 1
            errors += 1
    return {
        "events": len(events),
        "duration_ms": max(0, last_time - first_time),
        "tools": [
            {"name": name, **counts} for name, counts in sorted(tools.items(), key=lambda kv: -kv[1]["count"])
        ],
        "tool_calls": sum(item["count"] for item in tools.values()),
        "tool_errors": errors,
        "compactions": compactions,
        "started_at": first_time or None,
        "updated_at": last_time or None,
    }


def _latest_todos(events: list[Any]) -> list[dict[str, Any]]:
    """取最后一次 todo/write 的快照。"""
    for event in reversed(events):
        if getattr(event, "type", "") != "todo/write":
            continue
        data = getattr(event, "data", None)
        if isinstance(data, Mapping):
            todos = data.get("todos")
            if isinstance(todos, (list, tuple)):
                return list(todos)
    return []


def _tool_timeline(events: list[Any]) -> list[dict[str, Any]]:
    """合并调用的开始、结果和结束记录，每次调用保留一个时间线条目。"""
    return [{
        'seq': row['seq'], 'time': row['time'], 'call_id': row['call_id'], 'turn': row['turn'],
        'name': row['name'], 'status': row['status'], 'is_error': row['is_error'],
        'duration_ms': row['duration_ms'], 'input': _text_of(row['input'], 400),
        'display': _text_of(row['display']), 'counted': row['counted'],
    } for row in tool_invocations(events)]


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------


def session_prompts(session: Any) -> list[dict[str, str]]:
    """抽出本次会话实际生效的系统/开发者提示词。

    :func:`_project_messages` 把它们排除在对话之外，但"我的 harness 现在到底
    给模型灌了哪些指令"是个正当且高频的问题（排幻觉、调工具策略、核对技能
    注入），所以留一个只读出口，而不是让它们污染时间线。
    """
    prompts: list[dict[str, str]] = []
    for message in session.build_messages():
        kind = type(message).__name__
        if kind not in ("SystemMessage", "DeveloperMessage"):
            continue
        text = message.text
        if not text.strip():
            continue
        prompts.append({"role": kind.replace("Message", "").lower(), "text": text})
    return prompts


def _conversation_event(event: Any) -> bool:
    """仅包含恢复对话块所需的持久化事件。

    消息、汇总统计、待办和检查时间线根据完整历史分别构建。
    此投影不改变存储。
    """
    if event.type in {"turn/start", "turn/end", "tool/output", "agent/team"}:
        return True
    data = event.data
    if not isinstance(data, Mapping):
        return False
    return (
        event.type == "tool/call" and data.get("status") == "running"
        or event.type == "note" and data.get("kind") in {"permission_decision", "hook_run"}
    )


def session_detail(harness: Any, session_id: str, *, view: str = "full", limit: int = 100, cursor: str | None = None, session: Any = None) -> dict[str, Any]:
    """构造会话视图；默认完整历史，界面可协商 conversation 事件投影。"""
    member_context = session is not None
    session = session if member_context else harness.get_session(session_id)
    events = list(session.events)
    active_turn = session.open_turn if member_context or session_id in getattr(getattr(harness, 'runtime', None), 'active', {}) else None
    # 读取较早页面仅恢复展示，不刷新控制状态、
    # 重新计算模型上下文或序列化完整检查时间线。
    if view == "conversation-window" and cursor:
        from .conversation import project_page, turn_boundaries
        messages, visible_events, history = project_page(session, events, limit=limit, cursor=cursor, active_turn=active_turn, active_team_runs=harness._active_team_runs)
        return to_jsonable({"header": session_header_view(session.header), "messages": messages, "events": visible_events,
                           "turn_events": turn_boundaries(events, messages, visible_events, through=history['through']),
                           "events_view": "conversation-window", "history": history,
                           "usage": {}, "todos": [], "stats": {}, "timeline": []})
    messages = _project_messages(session, active_turn=active_turn)
    usage = usage_summary(messages)
    todos = _latest_todos(events)
    history = None
    if view == "conversation-window":
        from .conversation import project_page
        messages, visible_events, history = project_page(session, events, limit=limit, cursor=cursor, active_turn=active_turn, active_team_runs=harness._active_team_runs)
    else:
        from .conversation import project_steering_messages
        messages = project_steering_messages(messages, events, active_turn=active_turn)
        from .team_history import latest_team_events, team_event_view
        team_latest = {event.seq for event in latest_team_events(events)}
        visible_events = [team_event_view(event, session_id=session_id, active_runs=harness._active_team_runs) if event.seq in team_latest else event.to_dict() for event in events if view != "conversation" or _conversation_event(event)]
        from ..session.hook_runs import hook_results, hook_summary
        evidence = hook_results(events, active_turn=active_turn)
        for message in messages:
            for result in message.get('results', []):
                result['hooks'] = evidence.get(message.get('source_seq'), {}).get(str(result.get('tool_use_id')), [])
        if view == 'conversation':
            for event in visible_events:
                if event['type'] == 'note' and event['data'].get('kind') == 'hook_run':
                    event['data'] = {'kind': 'hook_run', **hook_summary(event['data'], event['seq'], active_turn=active_turn)}
    detail: dict[str, Any] = {
        "header": session_header_view(session.header),
        "storage_error": getattr(session, "persistence_error", None),
        "interrupted": getattr(session, "open_turn", None) is not None and session_id not in getattr(getattr(harness, "runtime", None), "active", {}),
        "messages": messages,
        "events": visible_events,
        "usage": usage,
        "todos": todos,
        "stats": _event_stats(events),
        # 窗口客户端从页面局部证据恢复工具，原有接口仍导出完整时间线。
        # 避免发送每条历史输入。
        "timeline": [] if history is not None else _tool_timeline(events),
        # 只读的系统提示词出口：不在对话里，但随时可查。
        "prompts": session_prompts(session),
    }
    from .conversation import turn_boundaries
    detail['turn_events'] = turn_boundaries(events, messages, visible_events,
        through=history['through'] if history else None, active_turn=active_turn)
    if member_context:
        # 成员仅投影对话，不访问主会话仓库里的目标、权限和计划控制状态。
        detail['interrupted'] = False
        detail['turn'] = session.current_turn
        if history is not None:
            detail.update(events_view="conversation-window", history=history)
        return to_jsonable(detail)
    if getattr(harness, "goals", None) is not None:
        detail["goal_state"] = harness.goals.snapshot(session_id)
    if hasattr(session, 'current_turn'):
        detail['turn'] = session.current_turn
    if getattr(session, 'steering', None) is not None:
        detail['steering'] = session.steering.recent(session.current_turn,
            active_turn=session.open_turn if session_id in getattr(getattr(harness, 'runtime', None), 'active', {}) else None)
    if view == "conversation":
        detail["events_view"] = "conversation"
    elif history is not None:
        detail.update(events_view="conversation-window", history=history)
    detail["plan_mode"] = harness.plan_mode_state(session_id)
    detail["questions"] = harness.questions.pending(session_id)
    if getattr(harness, "permissions", None) is not None:
        detail["permissions"] = harness.permissions.snapshot(session_id)
    if callable(getattr(harness, "agent_mount_state", None)):
        detail["agent_mount"] = harness.agent_mount_state(session)
    return to_jsonable(detail)


# ---------------------------------------------------------------------------
# 会话列表快速路径
# ---------------------------------------------------------------------------

#: 参与"消息条数"统计的事件类型（与 Session._event_to_message 的派生口径一致）。
_COUNTED_EVENTS = frozenset({"user/message", "assistant/message"})

#: 会话列表快速扫描结果的记忆化缓存条目上限。
_LISTING_CACHE_MAX = 512

#: ``(文件路径, mtime_ns, 大小) -> (消息数, 最后活动时间, 最后一条人工输入)``。
#: mtime/size变化时使用新键；这不替代日志读取及文件身份校验。
_LISTING_CACHE: "OrderedDict[tuple[str, int, int], tuple[int, int, str | None]]" = OrderedDict()
_LISTING_CACHE_LOCK = threading.Lock()


def _events_path_of(harness: Any, session_id: str) -> Path | None:
    """获取原始事件路径；未提供磁盘入口时回退，入口失败保留原异常。"""
    store = getattr(harness, "sessions", None)
    getter = getattr(store, "events_path", None)
    if getter is None:
        return None
    path = getter(session_id)
    return path if isinstance(path, Path) else None


def _listing_control_key(data: Mapping) -> tuple[str, int] | None:
    """以回执标识与回合关联控制引导，不按正文或请求名称猜测。"""
    request_id, turn = data.get('request_id'), data.get('turn')
    return (request_id, turn) if isinstance(request_id, str) and type(turn) is int else None


def _listing_goal_control(data: Mapping) -> tuple[str, int] | None:
    """只有持久受理记录中明确声明的目标控制才属于内部控制。"""
    if data.get('kind') == 'steering/accepted' and data.get('goal_action') in ('pause', 'resume', 'clear', 'edit'):
        return _listing_control_key(data)
    return None


def _listing_user_text(data: Mapping, controls: set[tuple[str, int]]) -> str | None:
    """自动续跑和目标控制不命名任务；普通人工引导仍参与摘要。"""
    source = data.get('source')
    if source in ('goal', 'goal-control', 'plan-mode') or source == 'steer' and _listing_control_key(data) in controls:
        return None
    text = user_message_text(data)
    return text if text.strip() else None


def _scan_events_for_listing(path: Path) -> tuple[int, int, str | None] | None:
    """流式扫描事件日志，只取会话列表需要的三个字段。

    返回 ``(消息数, 最后活动时间, 最后一条非空人工输入)``；返回 ``None`` 表示
    日志需要精确派生或由存储层确认完整性。替换操作不能使用朴素累计计数；
    损坏记录也不能由扫描器自行确认为有效前缀。读取失败保留原异常。

    """
    count = 0
    last_time = 0
    last_user: str | None = None
    controls: set[tuple[str, int]] = set()
    event_count = 0
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except ValueError:
                # 由存储层确认可读前缀及写入保护，扫描不自行判定损坏日志有效。
                return None
            if not isinstance(raw, dict):
                return None
            if type(raw.get('seq')) is not int or raw['seq'] != event_count:
                return None
            event_count += 1
            operation = raw.get("surface_op")
            if isinstance(operation, dict) and operation.get("op") != "append":
                return None
            stamp = raw.get("time")
            if type(stamp) is not int or stamp < 0:
                return None
            if stamp > last_time:
                last_time = stamp
            etype = raw.get("type")
            data = raw.get('data')
            if etype == 'note' and isinstance(data, dict):
                control = _listing_goal_control(data)
                if control is not None:
                    controls.add(control)
            if etype == 'user/message' and isinstance(raw.get('data'), dict) and raw['data'].get('source') == 'plan-mode':
                continue
            if etype in _COUNTED_EVENTS:
                count += 1
                if etype == "user/message":
                    data = raw.get("data")
                    text = _listing_user_text(data, controls) if isinstance(data, dict) else None
                    if isinstance(text, str) and text.strip():
                        last_user = text
    return count, last_time, last_user


def _list_stats_for_disk_session(path: Path) -> tuple[int, int, str | None] | None:
    """带记忆化缓存的扫描；缓存键含mtime/size，日志一追加即失效。"""
    try:
        stat = path.stat()
    except FileNotFoundError:
        # 新建的空对话可能尚无事件文件，交给存储层确认。
        return None
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    with _LISTING_CACHE_LOCK:
        cached = _LISTING_CACHE.get(key)
        if cached is not None:
            _LISTING_CACHE.move_to_end(key)
            return cached
    scanned = _scan_events_for_listing(path)
    if scanned is None:
        # 不缓存"需要精确回退"这个结论：文件一变就要重新判断。
        return None
    with _LISTING_CACHE_LOCK:
        _LISTING_CACHE[key] = scanned
        if len(_LISTING_CACHE) > _LISTING_CACHE_MAX:
            _LISTING_CACHE.popitem(last=False)
    return scanned


def clear_listing_cache() -> None:
    """清空会话列表快速路径的缓存（会话被删除或导入后调用）。"""
    with _LISTING_CACHE_LOCK:
        _LISTING_CACHE.clear()


def _exact_list_stats(harness: Any, session_id: str) -> tuple[int, int, str | None]:
    """精确回退路径：完整派生消息，只用于含 replace 操作的会话。"""
    session = harness.sessions.get(session_id)
    messages = _project_messages(session)
    count = sum(1 for message in messages if message.get("role") in {"user", "assistant"})
    last_time = 0
    last_user: str | None = None
    controls = {control for event in session.events if event.type == 'note'
                if (control := _listing_goal_control(event.data)) is not None}
    for event in session.surface:
        if event.type == 'user/message':
            text = _listing_user_text(event.data, controls)
            if text is not None:
                last_user = text
    for event in session.events:
        stamp = getattr(event, "time", 0) or 0
        if isinstance(stamp, (int, float)) and int(stamp) > last_time:
            last_time = int(stamp)
    return count, last_time, last_user


def session_listing(harness: Any, *, visible_session_ids: frozenset[str] | None = None) -> list[dict[str, Any]]:
    """会话列表：补上摘要与规模信息，交给前端直接分组渲染。"""
    listing: list[dict[str, Any]] = []
    for header in harness.list_sessions():
        if visible_session_ids is not None and header['id'] not in visible_session_ids:
            continue
        item = session_header_view(header)
        session_id = item.get("id", "")
        item.setdefault("title", None)
        item.setdefault("created_at", 0)
        # 会话级模型从 metadata 提到顶层：前端侧栏要画模型徽标，
        # 不该为了一个字段去理解 metadata 的内部形状。
        metadata = item.get("metadata")
        if isinstance(metadata, dict):
            item["model"] = metadata.get("model") or None
            item["reasoning_effort"] = metadata.get("reasoning_effort") or None
        item["summary"] = None
        item["message_count"] = 0
        item["last_activity"] = item.get("created_at") or 0
        events_path = _events_path_of(harness, session_id)
        stats = _list_stats_for_disk_session(events_path) if events_path else None
        if stats is None:
            # 替换、损坏记录或缺少磁盘入口时，由存储层确认并精确派生。
            stats = _exact_list_stats(harness, session_id)
        count, last_time, last_user = stats
        item["message_count"] = count
        if last_time:
            item["last_activity"] = last_time
        if last_user:
            item["summary"] = _text_of(last_user, 140)
        elif isinstance(metadata, dict):
            # 由面板直接创建的目标可能只有自动输入；摘要使用目标正文，
            # 不让内部续跑指令覆盖用户可辨认的任务名称。
            goal = metadata.get("goal")
            objective = goal.get("objective") if isinstance(goal, dict) else None
            if isinstance(objective, str) and objective.strip():
                item["summary"] = _text_of(objective, 140)
        listing.append(item)
    listing.sort(key=lambda row: int(row.get("last_activity") or 0), reverse=True)
    return to_jsonable(listing)


def catalog_status(harness: Any) -> dict[str, Any]:
    """状态总览：配置 + 工具 + 技能 + 各开关，前端设置面板直接消费。"""
    status = harness.status()
    status["context_warning_ratio"] = CONTEXT_WARN_RATIO
    try:
        status["mcp_connected"] = [
            server
            for server in getattr(harness.mcp, "servers", {})
            .values()
            if getattr(server, "connected", False)
        ]
    except Exception:
        status["mcp_connected"] = []
    return to_jsonable(status)


def tool_catalog(harness: Any) -> list[dict[str, Any]]:
    """工具目录 + 分类，供工具浏览器与命令面板使用。

    这里刻意在 ``schema.to_dict()`` 之外补上 ``category`` / ``title`` /
    ``dangerous``：这三项是**执行侧元数据**，ToolSchema 里没有（也不该有，
    否则每次请求都要多带几百 token 给模型看纯 UI 信息）。从前端的角度，
    拿到一个不带分类的 47 条平铺列表等于没有目录 —— 全部落进"其他"分组，
    比不给列表还糟。
    """
    from ..core.language import current_language
    catalog: list[dict[str, Any]] = []
    for name in harness.tools.names():
        definition = harness.tools.get(name)
        category = resolve_category(getattr(definition, "category", None), name)
        schema = definition.schema.resolve_description(current_language.get())
        entry = schema.to_dict()
        entry["category"] = category.id
        entry["category_label"] = category.label
        entry["title"] = getattr(definition, "title", name)
        entry["dangerous"] = bool(getattr(definition, "dangerous", False))
        catalog.append(entry)
    return to_jsonable(catalog)


def skill_catalog(harness: Any, session: Any = None) -> list[dict[str, Any]]:
    """技能目录。"""
    registry = harness.skills_for(session) if session is not None else harness.skills
    skills = registry.list() if registry is not None else []
    return to_jsonable([skill.to_dict() for skill in skills])


__all__ = [
    "CONTEXT_WARN_RATIO",
    "session_prompts",
    "usage_summary",
    "session_detail",
    "session_listing",
    "clear_listing_cache",
    "catalog_status",
    "tool_catalog",
    "skill_catalog",
]
