"""已结束回合的操作使用已提交边界，不使用当前实时尾部。"""

from harness.i18n import tr
import asyncio
from collections import Counter
from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any

from ...session.hook_runs import hook_summary
from ...session.partials import partial_response_events
from ...session.turn_scope import turn_events
from ...session.turn_delivery import delivery_summary
from ..errors import BadRequest, Conflict, NotFound
from ..responses import json_response
from ..router import Router


def ended_turn(session: Any, turn: int) -> tuple[Any, Any]:
    try:
        events = turn_events(session.events, turn=turn)
    except ValueError as exc:
        raise Conflict(str(exc), code='turn/changed') from exc
    start = events[0] if events else None
    end = events[-1] if events and events[-1].type == 'turn/end' else None
    if not start or not end:
        raise NotFound(tr('python.turn_completion.5fe21927fee5'), code='turn/not-completed')
    return start, end


def completion_snapshot(session: Any, turn: int, *, offset: int = 0, include_delivery: bool = True) -> dict[str, Any]:
    start, end = ended_turn(session, turn)
    # 优先复制明确关联的最终回复；未产生最终回复的中断回合使用本轮最后保留的正文。
    # 没有正文的回合仍可评价、分支和查看检查，复制按钮由界面禁用。
    events = turn_events(session.events, turn=turn)
    scope = events[1:-1]
    replies = [event for event in scope if event.type == 'assistant/message'
               and event.data.get('source') != 'compaction' and session._event_to_message(event).text.strip()]
    replies.extend(event for event in partial_response_events(scope)
                   if isinstance(event.data.get('text'), str) and event.data['text'].strip())
    final_seq = end.data.get('final_response_seq')
    final = next((event for event in replies if event.seq == final_seq and event.type == 'assistant/message'), None)
    response = final or max(replies, key=lambda event: event.seq, default=None)
    hooks = {}
    feedback = None
    for event in session.events_of_type('note'):
        data = event.data
        if not isinstance(data, Mapping) or data.get('session_id') != session.id or data.get('turn') != turn:
            continue
        if data.get('kind') == 'hook_run' and start.seq < event.seq < end.seq:
            hooks[data['id']] = hook_summary(data, event.seq, active_turn=None)
        elif data.get('kind') == 'turn_feedback' and data.get('end_seq') == end.seq and event.seq > end.seq:
            feedback = data.get('rating')
    rows = sorted(hooks.values(), key=lambda row: row['source_seq'])
    blocked = lambda row: row['status'] == 'blocked' and row['event'] in ('PreToolUse', 'Stop')
    if offset > len(rows) or offset > 0 and offset == len(rows):
        raise BadRequest(tr('python.turn_completion.8587eccb39d8'), code='turn/invalid-offset')
    return {'turn': turn, 'end_seq': end.seq, 'final_response_seq': final_seq,
            'response_seq': response.seq if response else None,
            'ended_at': end.time, 'feedback': feedback, 'delivery': delivery_summary(events, session) if include_delivery else None,
            'hooks': {'total': len(rows), 'counts': dict(Counter(row['status'] for row in rows)),
                      'runs': sum(row['status'] != 'skipped' for row in rows),
                      'blocked': sum(blocked(row) for row in rows),
                      'unsuccessful': sum(row['status'] in ('failed', 'timed_out', 'cancelled', 'cleanup_failed', 'interrupted') or row['status'] == 'blocked' and not blocked(row) for row in rows),
                      'duration_ms': sum(row.get('duration_ms', 0) for row in rows),
                      'rows': rows[offset:offset + 50], 'offset': offset,
                      'next_offset': offset + 50 if offset + 50 < len(rows) else None}}


def build_router(ctx: Any) -> Router:
    router = Router()

    def number(value: Any, label: str, minimum: int) -> int:
        if not isinstance(value, str) or not value.isascii() or not value.isdecimal() or len(value) > 16 or not minimum <= int(value) <= 2**53 - 1:
            raise BadRequest(label + tr('python.turn_completion.e7b4dfe062ec'), code='turn/invalid-position')
        return int(value)

    @router.get('/api/v1/sessions/{session_id}/turns/{turn}/completion')
    def detail(request: Any, session_id: str, turn: str) -> Any:
        identity = number(turn, tr('python.turn_completion.c5567e3f86d2'), 1)
        offset = number(request.get('offset', '0'), tr('python.turn_completion.ab0f7240c6f2'), 0)
        async def read():
            session = ctx.harness.get_session(session_id)
            events = tuple(session.events)
            generation, cwd = session.history_generation, session.header.cwd
            snapshot = SimpleNamespace(id=session.id, events=events, header=SimpleNamespace(cwd=cwd),
                events_of_type=lambda kind: (event for event in events if event.type == kind),
                _event_to_message=session._event_to_message)
            # 文件指纹在有界工作线程中读取；不可变事件快照不会跨对话归还。
            value = await asyncio.to_thread(completion_snapshot, snapshot, identity, offset=offset)
            if session.history_generation != generation or session.header.cwd != cwd or ended_turn(session, identity)[1].seq != value['end_seq']:
                raise Conflict(tr('python.turn_completion.829ba4b4d5d7'), code='turn/changed')
            return value
        return json_response(ctx.call(read()))

    @router.post('/api/v1/sessions/{session_id}/turns/{turn}/feedback')
    def feedback(request: Any, session_id: str, turn: str) -> Any:
        identity = number(turn, tr('python.turn_completion.c5567e3f86d2'), 1)
        body = request.json()
        if not isinstance(body, dict) or body.get('rating') not in ('positive', 'negative', None) or 'rating' not in body or type(body.get('end_seq')) is not int:
            raise BadRequest(tr('python.turn_completion.d28e05f6d960'), code='turn/invalid-feedback')
        async def save():
            session = ctx.harness.get_session(session_id)
            _, end = ended_turn(session, identity)
            if body['end_seq'] != end.seq:
                raise Conflict(tr('python.turn_completion.829ba4b4d5d7'), code='turn/changed')
            current = completion_snapshot(session, identity, include_delivery=False)
            if current['feedback'] != body['rating']:
                await session.append('note', {'kind': 'turn_feedback', 'session_id': session.id,
                                             'turn': identity, 'end_seq': end.seq, 'rating': body['rating']})
            return {'turn': identity, 'end_seq': end.seq, 'feedback': body['rating']}
        return json_response(ctx.call(save()))

    return router
