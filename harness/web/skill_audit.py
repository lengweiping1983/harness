"""技能证据来自已提交工具调用和结果，不读取当前注册表作为历史证据。"""

from harness.i18n import tr
from collections.abc import Mapping

from ..session.session import project_surface
from .errors import BadRequest, NotFound

CHUNK_CHARS = 24000


def bounded_events(events, through):
    last = events[-1].seq if events else -1
    through = last if through is None else through
    if through < -1 or through > last:
        raise BadRequest(tr('python.skill_audit.5cbad00b1421'))
    return tuple(e for e in events if e.seq <= through), through


def skill_audit(events, *, through=None, before=None, limit=20):
    events, through = bounded_events(events, through)
    if not 1 <= limit <= 100 or (before is not None and not 0 <= before <= through + 1):
        raise BadRequest(tr('python.skill_audit.5fcf9d6170a4'))
    calls = []
    active_calls = {}
    results = {}
    origins = {}
    for event in events:
        data = event.data
        if event.type == 'tool/call' and data.get('status') == 'running':
            if data.get('name') == 'skill':
                calls.append(event)
            active_calls[data.get('call_id')] = event
        elif event.type == 'tool/result':
            if data.get('pruned'):
                origins[event.seq] = set().union(*(origins.get(seq, set()) for seq in event.source_event_seqs or ()))
                continue
            call_id = data.get('call_id')
            call = active_calls.get(call_id)
            if call is not None and call.data.get('name') == 'skill' and call.seq not in results:
                results[call.seq] = event
                origins[event.seq] = {event.seq}
    surface = project_surface(events)
    retained = {e.seq for e in surface}
    pruned = set().union(*(origins.get(e.seq, set()) for e in surface
                          if e.type == 'tool/result' and e.data.get('pruned')))
    eligible = [e for e in calls if before is None or e.seq < before]
    selected = eligible if len(eligible) - limit < 5 else eligible[-limit:]
    rows = []
    for call in selected:
        result = results.get(call.seq)
        data = result.data if result else {}
        display = data.get('display') or {}
        if not isinstance(display, Mapping):
            display = {}
        payload = data.get('results') or ()
        failed = any(part.get('is_error') for part in payload)
        args = call.data.get('input') or {}
        name = args.get('name', '') if isinstance(args, Mapping) else ''
        rows.append({'id': call.seq, 'time': call.time, 'name': str(name)[:500],
                     'status': 'failed' if failed else 'loaded' if result else 'unconfirmed',
                     'context': ('full' if result and result.seq in retained else
                                 'pruned' if result and result.seq in pruned else 'absent'),
                     'result_seq': result.seq if result else None,
                     'description': str(display.get('description', ''))[:500],
                     'error': str(display.get('error', ''))[:500] if failed else ''})
    return {'through': through, 'skills': rows, 'has_more': len(eligible) > len(selected),
            'next_before': selected[0].seq if len(eligible) > len(selected) else None}


def read_skill(events, seq, *, through, offset=0):
    events, _ = bounded_events(events, through)
    event = next((e for e in events if e.seq == seq), None)
    if not event or event.type != 'tool/result' or event.data.get('pruned'):
        raise NotFound(tr('python.skill_audit.30cad2032603'))
    call_id = event.data.get('call_id')
    call = next((e for e in reversed(events) if e.type == 'tool/call'
                 and e.data.get('status') == 'running' and e.data.get('call_id') == call_id
                 and e.seq < seq), None)
    if call is None or call.data.get('name') != 'skill':
        raise NotFound(tr('python.skill_audit.375ec23d4356'))
    text = '\n'.join(str(p.get('content', '')) for p in event.data.get('results', ()))
    if offset < 0 or offset > len(text):
        raise BadRequest(tr('python.skill_audit.a9d4333ff33a'))
    return {'text': text[offset:offset + CHUNK_CHARS], 'offset': offset, 'total_chars': len(text),
            'next_offset': offset + CHUNK_CHARS if offset + CHUNK_CHARS < len(text) else None}
