"""只读搜索已提交对话，独立于模型消息表面。"""
from __future__ import annotations

from harness.i18n import tr

import re
from typing import Any

from .errors import BadRequest, NotFound
from ..session.partials import partial_response_events
from ..session.events import user_message_text

CHUNK_SIZE = 16384


def _dialogue(session: Any, events: Any):
    from .conversation import steering_display_events
    for event in steering_display_events(events, evidence=session.events):
        if event.type not in {'user/message', 'assistant/message'}:
            continue
        if event.data.get('source') == 'compaction':
            continue
        message = session._event_to_message(event)
        text = user_message_text(event.data) if event.type == 'user/message' else getattr(message, 'text', '')
        if text.strip():
            yield event, text


def search_dialogue(session: Any, query: str, *, limit: int = 50,
                    through: int | None = None, before: int | None = None) -> dict[str, Any]:
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 256:
        raise BadRequest(tr('python.conversation_search.6c926b2c7396'), code='search/invalid-query')
    if type(limit) is not int or not 1 <= limit <= 50:
        raise BadRequest(tr('python.conversation_search.96f5a568c0b3'), code='search/invalid-limit')
    events = session.events
    last = events[-1].seq if events else -1
    through = last if through is None else through
    if type(through) is not int or not -1 <= through <= last:
        raise BadRequest(tr('python.conversation_search.06ad8c6bf887'), code='search/invalid-boundary')
    before = through + 1 if before is None else before
    if type(before) is not int or not 0 <= before <= through + 1:
        raise BadRequest(tr('python.conversation_search.26e8e793247e'), code='search/invalid-boundary')
    query = query.strip()
    pattern = re.compile(re.escape(query), re.IGNORECASE)
    total = eligible = 0
    results = []
    for event, text in _dialogue(session, reversed(events)):
        if event.seq > through:
            continue
        match = pattern.search(text)
        if match is None:
            continue
        total += 1
        if event.seq >= before:
            continue
        eligible += 1
        if len(results) >= limit + 4:
            continue
        start, end = match.span()
        results.append({'source_seq': event.seq, 'source_time': event.time,
                        'role': 'user' if event.type == 'user/message' else 'assistant',
                        'prefix': ('…' if start > 100 else '') + text[max(0, start - 100):start],
                        'match': text[start:end],
                        'suffix': text[end:end + 140] + ('…' if len(text) > end + 140 else ''),
                        'start_char': max(0, start - 4000)})
    if eligible - limit >= 5:
        results = results[:limit]
    more = eligible > len(results)
    return {'session_id': session.id, 'query': query, 'through': through, 'total': total, 'results': results,
            'has_more': more, 'next_before': results[-1]['source_seq'] if more else None}


def read_dialogue(session: Any, source_seq: int, *, start: int = 0) -> dict[str, Any]:
    if type(source_seq) is not int or source_seq < 0 or type(start) is not int or start < 0:
        raise BadRequest(tr('python.conversation_search.2a5c53b20927'), code='search/invalid-position')
    event = next((event for event in session.events if event.seq == source_seq), None)
    rows = list(_dialogue(session, [event])) if event is not None else []
    # 中断正文只用于展示和复制，仍不加入模型上下文或普通全文搜索。
    if not rows and event is not None and event.type == 'note' and event.data.get('kind') in {'assistant_partial', 'assistant_checkpoint'}:
        rows = [(partial, partial.data['text']) for partial in partial_response_events(session.response_evidence)
                if partial.seq == source_seq and isinstance(partial.data.get('text'), str) and partial.data['text'].strip()]
    if not rows:
        raise NotFound(tr('python.conversation_search.16b409ea4935'), code='search/message-not-found')
    event, text = rows[0]
    if start >= len(text):
        raise BadRequest(tr('python.conversation_search.d7a3652519f2'), code='search/invalid-position')
    end = min(len(text), start + CHUNK_SIZE)
    return {'session_id': session.id, 'source_seq': event.seq, 'source_time': event.time,
            'role': 'user' if event.type == 'user/message' else 'assistant',
            'text': text[start:end], 'start': start, 'end': end, 'total_chars': len(text),
            'previous_start': max(0, start - CHUNK_SIZE) if start else None,
            'next_start': end if end < len(text) else None}
