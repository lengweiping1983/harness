"""冻结调用者可见对话背景，不复制权限、目标、工具执行或消息收件箱。"""
from __future__ import annotations

import json
from .history import message_text, message_author

MAX_CONTEXT_BYTES = 1024 * 1024


def inherited_context(session, fork_turns):
    """all 复制全部可见正文，正整数复制最近若干轮；超过上限明确拒绝。"""
    if fork_turns == 'none':
        return None
    if fork_turns != 'all' and (type(fork_turns) is not int or fork_turns <= 0):
        raise ValueError('agent/invalid-fork-turns')
    if session is None:
        raise ValueError('agent/context-unavailable')
    through = session.event_count - 1
    starts = [event.seq for event in session.events_of_type('turn/start') if event.seq <= through]
    after = starts[-fork_turns] - 1 if type(fork_turns) is int and len(starts) >= fork_turns else -1
    rows = []
    used = 0
    for event in session.surface:
        if not after < event.seq <= through or event.type not in ('user/message', 'assistant/message'):
            continue
        text = message_text(event)
        if text:
            if len(text) > MAX_CONTEXT_BYTES:
                raise ValueError('agent/inherited-context-too-large')
            row = {'seq': event.seq, 'role': 'user' if event.type == 'user/message' else 'assistant',
                   'author': message_author(event), 'text': text}
            used += len(json.dumps(row, ensure_ascii=False).encode('utf-8')) + 2
            if used > MAX_CONTEXT_BYTES:
                raise ValueError('agent/inherited-context-too-large')
            rows.append(row)
    payload = {'source_session_id': session.id, 'after': after, 'through': through,
               'fork_turns': fork_turns, 'messages': rows}
    text = json.dumps(payload, ensure_ascii=False)
    if len(text.encode('utf-8')) > MAX_CONTEXT_BYTES:
        raise ValueError('agent/inherited-context-too-large')
    return payload
