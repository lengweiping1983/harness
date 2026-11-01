"""已提交对话历史的只读分页，独立于模型消息表面。"""
from __future__ import annotations

from harness.i18n import tr

import base64
import json
from dataclasses import replace
from collections.abc import Mapping
from typing import Any

from .errors import BadRequest, Conflict
from ..session.partials import is_partial_response, partial_response_events, project_partial_response, resolved_response_ids
from ..session.hook_runs import hook_summary
from ..session.events import AppendOp
from ..session.steering import SteeringLedger

MESSAGE_TYPES = frozenset({'user/message', 'assistant/message', 'tool/result'})
MAX_OUTPUT = 32768
MAX_INTEGER = 2**53 - 1


def _terminal_outcome(event: Any) -> bool:
    if event.type != 'turn/end' or not isinstance(event.data, Mapping):
        return False
    reason = event.data.get('reason')
    kind = reason.get('kind') if isinstance(reason, Mapping) else reason
    return isinstance(kind, str) and kind in {'error', 'aborted', 'cancelled', 'interrupted',
        'blocked', 'max-tokens', 'max-steps', 'budget'}


def _cursor(session: str, through: int, before: int) -> str:
    data = json.dumps({'session': session, 'through': through, 'before': before}, separators=(',', ':')).encode()
    return base64.urlsafe_b64encode(data).decode().rstrip('=')


def _decode(token: str, session: str) -> tuple[int, int]:
    try:
        if not isinstance(token, str) or not 1 <= len(token) <= 2048:
            raise ValueError(tr('runtime.validation.d3739844c775'))
        data = json.loads(base64.b64decode(token + '=' * (-len(token) % 4), altchars=b'-_', validate=True))
        if not isinstance(data, dict) or set(data) != {'session', 'through', 'before'} or data['session'] != session:
            raise ValueError(tr('runtime.validation.4c3eaa59c562'))
        if any(type(data[key]) is not int or not 0 <= data[key] <= MAX_INTEGER for key in ('through', 'before')):
            raise ValueError(tr('runtime.validation.fae9fad62463'))
        if data['before'] > data['through']:
            raise ValueError(tr('runtime.validation.74d229e39a71'))
        return data['through'], data['before']
    except (ValueError, UnicodeError, TypeError) as exc:
        raise BadRequest(tr('python.conversation.2ede74cc56fa'), code='conversation/invalid-cursor') from exc


def _progress(previous: dict[str, Any] | None, raw: Mapping[str, Any]) -> dict[str, Any] | None:
    """与前端 mergeToolOutput 使用相同的 Unicode 码点尾部及重叠语义。"""
    sequence = raw.get('output_seq')
    if type(sequence) is not int or not 1 <= sequence <= MAX_INTEGER or sequence <= (previous or {}).get('seq', 0):
        return previous
    result: dict[str, Any] = {'seq': sequence}
    for name in ('stdout', 'stderr'):
        offset, delta = raw.get(name + '_offset'), raw.get(name)
        if type(offset) is not int or not 0 <= offset <= MAX_INTEGER or not isinstance(delta, str) or offset + len(delta) > MAX_INTEGER:
            return previous
        end_name = name + 'End'
        old_end = (previous or {}).get(end_name, 0)
        old = '' if offset > old_end else (previous or {}).get(name, '')
        tail = (old + delta[max(0, old_end - offset):])[-MAX_OUTPUT:]
        end = max(old_end, offset + len(delta))
        result.update({name: tail, end_name: end, name + 'Dropped': end - len(tail)})
    if raw.get('encoding_errors'):
        result['encodingErrors'] = raw['encoding_errors']
    return result


def steering_display_events(events, *, evidence, active_turn=None):
    """按接受位置展示引导，应用记录仅改变状态，不修改模型表面。"""
    ledger = SteeringLedger()
    for event in evidence:
        ledger.absorb(event)
    accepted = {record['accepted_seq']: record for record in ledger.records.values()}
    for event in events:
        if event.type == 'user/message' and event.data.get('source') == 'plan-mode':
            continue
        if event.seq in accepted:
            receipt = ledger.receipt(accepted[event.seq]['request_id'], active_turn=active_turn)
            yield replace(event, type='user/message', surface_op=AppendOp(), data={
                **({'attachments': receipt['attachments']} if receipt.get('attachments') else {}),
                'text': receipt['text'], 'source': 'steer', 'request_id': receipt['request_id'],
                'turn': receipt['turn'], 'steering_status': receipt['status'], 'steering_reason': receipt['reason'],
                'steering_team_delivered': receipt['team_delivered'],
                'steering_delivery_seq': receipt['delivery_seq']})
        elif event.type == 'user/message' and event.data.get('source') == 'steer' and event.data.get('request_id') in ledger.records:
            continue
        else:
            yield event


def project_steering_messages(messages, events, *, active_turn=None):
    """完整视图也展示未应用的引导，保留已有压缩消息的位置。"""
    synthetic = [event for event in steering_display_events(events, evidence=events, active_turn=active_turn)
                 if event.type == 'user/message' and 'steering_status' in event.data]
    request_ids = {event.data['request_id'] for event in synthetic}
    messages = [message for message in messages if message.get('request_id') not in request_ids]
    for event in synthetic:
        message = {'kind': 'UserMessage', 'role': 'user', **event.data,
                   'source_seq': event.seq, 'source_time': event.time}
        after = next((index for index, item in enumerate(messages) if item.get('source_seq', -1) > event.seq), len(messages))
        messages.insert(after, message)
    return messages


class _HistorySource:
    def __init__(self, session: Any, events: list[Any]):
        self.session = session
        self.surface = events
        self.header = session.header
        self.events = session.events

    def build_messages(self):
        # 此适配器投影不可变证据，不修改模型消息表面。
        return [self.session._event_to_message(event) for event in self.surface]


def project_page(session: Any, events: list[Any], *, limit: int = 100, cursor: str | None = None,
                 active_turn: int | None = None, active_team_runs=()) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    from .views import _project_messages

    if type(limit) is not int or not 1 <= limit <= 200:
        raise BadRequest(tr('python.conversation.06323b716459'), code='conversation/invalid-limit')
    through = events[-1].seq if events else -1
    before = through + 1
    if cursor:
        through, before = _decode(cursor, session.id)
        known = {event.seq for event in events}
        if through not in known or before not in known:
            raise Conflict(tr('python.conversation.60e2d5d89465'), code='conversation/history-changed')
    pinned = [event for event in events if event.seq <= through]
    # 标识来自已提交的回合边界，包括本页之外的开始记录。
    # 引导用户消息仍属于同一次执行。
    turns: dict[int, int] = {}
    current_turn = None
    for event in pinned:
        if event.type == 'turn/start':
            value = event.data.get('turn')
            current_turn = value if type(value) is int and 1 <= value <= MAX_INTEGER else None
        if current_turn is not None:
            turns[event.seq] = current_turn
        if event.type == 'turn/end':
            current_turn = None
    evidence = session.response_evidence
    resolved = resolved_response_ids(evidence) if cursor else None
    partials = partial_response_events([event for event in evidence if event.seq <= through],
                                       active_turn=active_turn, resolved=resolved)
    stop_checks = {}
    for event in pinned:
        if event.type == 'note' and event.data.get('kind') == 'hook_run' and event.data.get('event') == 'Stop':
            stop_checks[str(event.data['id'])] = event
    # 每次检查占一个历史单元，最新已提交修订替换其开始记录，
    # 避免分页重复展示长期运行的检查。
    display_events = steering_display_events(pinned, evidence=events, active_turn=active_turn)
    units = [event for event in display_events if event.type in MESSAGE_TYPES or _terminal_outcome(event)]
    units.extend(stop_checks.values())
    from .team_history import latest_team_events, team_event_view
    team_events = latest_team_events(pinned)
    units.extend(team_events)
    units.sort(key=lambda event: event.seq)
    if partials:
        units.extend(partials)
        units.sort(key=lambda event: event.seq)
    if cursor and not any(event.seq == before for event in units):
        raise BadRequest(tr('python.conversation.6c69799100f5'), code='conversation/invalid-cursor')
    candidates = [event for event in units if event.seq < before]
    selected = candidates if len(candidates) - limit < 5 else candidates[-limit:]
    start, end = len(candidates) - len(selected), len(candidates)
    message_events = [event for event in selected if event.type in MESSAGE_TYPES]
    messages = _project_messages(_HistorySource(session, message_events), include_partials=False)
    messages.extend(project_partial_response(event) for event in selected if is_partial_response(event))
    for message in messages:
        if message['source_seq'] in turns:
            message['turn'] = turns[message['source_seq']]
    messages.sort(key=lambda message: message['source_seq'])
    selected_by_seq = {message['source_seq']: message for message in messages}

    def call_key(event, call_id):
        value = event.data.get('turn')
        return (value if type(value) is int else turns.get(event.seq), call_id)

    needed = {(result.get('turn', message.get('turn')), str(result.get('tool_use_id') or ''))
              for message in messages for result in message.get('results', [])}
    latest: dict[tuple[int | None, str], Any] = {}
    completed: dict[tuple[int | None, str], int] = {}
    for event in pinned:
        if event.type == 'tool/call' and isinstance(event.data, Mapping) and event.data.get('call_id'):
            latest[call_key(event, str(event.data['call_id']))] = event
        elif event.type == 'tool/result' and isinstance(event.data, Mapping):
            for result in event.data.get('results', ()):
                if isinstance(result, Mapping) and result.get('tool_use_id'):
                    completed[call_key(event, str(result['tool_use_id']))] = event.seq
    if not cursor:
        needed.update(call_id for call_id, event in latest.items() if event.data.get('status') == 'running' and completed.get(call_id, -1) < event.seq)
    calls: dict[tuple[int | None, str], dict[str, Any]] = {}
    relevant = [event.to_dict() for event in selected if _terminal_outcome(event)]
    for event in selected:
        if event.type == 'note' and event.data.get('kind') == 'hook_run' and event.data.get('event') == 'Stop':
            relevant.append({'type': 'note', 'seq': event.seq, 'time': event.time,
                'data': {'kind': 'hook_run', **hook_summary(event.data, event.seq, active_turn=active_turn)}})
    for event in pinned:
        if event.type not in {'tool/call', 'tool/output', 'note', 'tool/result'}:
            continue
        data = event.data
        if not isinstance(data, Mapping):
            continue
        call_id = call_key(event, str(data.get('call_id') or ''))
        if event.type == 'tool/call' and call_id in needed:
            if data.get('status') == 'running':
                calls[call_id] = {'event': event, 'progress': None, 'approval': None, 'hooks': {}}
            state = calls.setdefault(call_id, {'event': event, 'progress': None, 'approval': None, 'hooks': {}})
            state['last'] = event
        elif event.type == 'tool/output' and call_id in calls:
            state = calls[call_id]
            progress = _progress(state['progress'], data)
            if progress is not state['progress']:
                state['output_event'] = event
                state['progress'] = progress
        elif event.type == 'note' and data.get('kind') == 'hook_run' and data.get('event') != 'Stop' and call_id in calls:
            calls[call_id]['hooks'][str(data['id'])] = (event, hook_summary(data, event.seq, active_turn=active_turn))
        elif event.type == 'note' and data.get('kind') == 'permission_decision' and call_id in calls:
            calls[call_id]['approval'] = data.get('decision')
            calls[call_id]['approval_reason'] = data.get('reason')
            calls[call_id]['approval_reviewer'] = data.get('reviewer')
        elif event.type == 'tool/result' and event.seq in selected_by_seq:
            message = selected_by_seq[event.seq]
            for result in message.get('results', []):
                result_id = call_key(event, str(result.get('tool_use_id') or ''))
                state = calls.get(result_id)
                # 事件自身的元数据优先于后续同名调用。
                for key in ('name', 'display', 'duration_ms'):
                    result.pop(key, None)
                    if data.get(key) is not None:
                        result[key] = data[key]
                if state:
                    source = state['event'].data
                    result['hooks'] = [dict(summary) for _, summary in state['hooks'].values()]
                    result['input'] = source.get('input')
                    result.setdefault('name', source.get('name'))
                    result['call_source_seq'] = state['event'].seq
                    if state['event'].seq in turns:
                        result['turn'] = turns[state['event'].seq]
                    if state['progress'] is not None:
                        result['progress'] = dict(state['progress'])
                    if isinstance(state['approval'], (list, tuple)):
                        result['approval_decision'] = list(state['approval'])
                        if state.get('approval_reason'):
                            result['approval_reason'] = state['approval_reason']
                        if state.get('approval_reviewer'):
                            result['approval_reviewer'] = state['approval_reviewer']
    # 即使页面从操作开始之后截取，当前未完成操作仍保持可见。
    if not cursor:
        for call_id, state in calls.items():
            last = state.get('last', state['event'])
            if last.data.get('status') != 'running' or completed.get(call_id, -1) > state['event'].seq:
                continue
            projected = state['event'].to_dict()
            if state['event'].seq in turns:
                projected['data'] = {**projected['data'], 'turn': turns[state['event'].seq]}
            relevant.append(projected)
            for hook_event, summary in state['hooks'].values():
                relevant.append({'type': 'note', 'seq': hook_event.seq, 'time': hook_event.time,
                                 'data': {'kind': 'hook_run', **summary}})
            progress = state['progress']
            if progress:
                output = state['output_event']
                relevant.append({'type': 'tool/output', 'seq': output.seq, 'time': output.time,
                                 'projection': 'output-tail', 'data': {
                                     'call_id': call_id[1], 'output_seq': progress['seq'],
                                     **({'turn': call_id[0]} if call_id[0] is not None else {}),
                                     'stdout': progress['stdout'], 'stderr': progress['stderr'],
                                     'stdout_offset': progress['stdoutDropped'], 'stderr_offset': progress['stderrDropped'],
                                     **({'encoding_errors': progress['encodingErrors']} if progress.get('encodingErrors') else {}),
                                 }})
    relevant.extend(team_event_view(event, session_id=session.id, active_runs=active_team_runs)
                    for event in selected if event.type == 'agent/team')
    # 导航索引覆盖固定快照的全部用户输入，不受正文分页窗口限制。
    user_messages = [{'source_seq': event.seq, 'text': event.data.get('text', '')[:240]}
                     for event in units if event.type == 'user/message'
                     and event.data.get('source') not in {'goal', 'compaction'}
                     and isinstance(event.data.get('text'), str) and event.data['text'].strip()]
    history = {'user_messages': user_messages, 'through': through, 'start': start, 'end': end, 'total': len(units), 'has_more': start > 0,
               'next_cursor': _cursor(session.id, through, selected[0].seq) if start > 0 else None}
    from .tool_results import project_result
    for message in messages:
        if message.get('role') == 'tool':
            message['results'] = [project_result(result, event_seq=message['source_seq'], index=index, name=result.get('name'))
                                  for index, result in enumerate(message.get('results', []))]
    return messages, sorted(relevant, key=lambda event: event['seq']), history


def turn_boundaries(events, messages, relevant, *, through=None, active_turn=None):
    """页面级执行元数据，与分页消息单元分开处理。"""
    selected = {m.get('source_seq') for m in messages}
    selected.update(e.get('seq') for e in relevant)
    needed = {active_turn} if active_turn is not None else set()
    boundaries = []
    current = None
    for event in events:
        if through is not None and event.seq > through:
            break
        if event.type == 'turn/start':
            current = event.data.get('turn')
        if event.seq in selected and current is not None:
            needed.add(current)
        if event.type in {'turn/start', 'turn/end'}:
            boundaries.append(event)
        if event.type == 'turn/end':
            current = None
    return [e.to_dict() for e in boundaries if e.data.get('turn') in needed]
