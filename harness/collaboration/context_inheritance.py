"""冻结调用者可见对话背景，不复制权限、目标、工具执行或消息收件箱。

继承副本会跨越代理边界：子代理可能使用不同模型、不同执行器，甚至由外部
runner（codex/claude/opencode）在独立进程里运行。因此父对话正文在进入子代理
上下文之前必须先脱敏，且脱敏必须可核验——载荷同时给出消息计数与命中次数，
让子代理和审计都能看到"继承了多少、改写了多少"，而不是从被静默改写的正文里
自行猜测。超出体积上限明确拒绝，不做静默截断。
"""
from __future__ import annotations

import json

from ..core.redaction import redact_text
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
    redacted_messages = 0
    redacted_hits = 0
    for event in session.surface:
        if not after < event.seq <= through or event.type not in ('user/message', 'assistant/message'):
            continue
        text = message_text(event)
        if text:
            if len(text) > MAX_CONTEXT_BYTES:
                raise ValueError('agent/inherited-context-too-large')
            # 脱敏只作用于继承副本；父会话事件与落盘原件保持原样。
            text, hits = redact_text(text)
            if hits:
                redacted_messages += 1
                redacted_hits += hits
            row = {'seq': event.seq, 'role': 'user' if event.type == 'user/message' else 'assistant',
                   'author': message_author(event), 'text': text}
            used += len(json.dumps(row, ensure_ascii=False).encode('utf-8')) + 2
            if used > MAX_CONTEXT_BYTES:
                raise ValueError('agent/inherited-context-too-large')
            rows.append(row)
    payload = {'source_session_id': session.id, 'after': after, 'through': through,
               'fork_turns': fork_turns, 'messages': rows,
               'message_count': len(rows),
               'redacted_messages': redacted_messages,
               'redacted_hits': redacted_hits}
    text = json.dumps(payload, ensure_ascii=False)
    if len(text.encode('utf-8')) > MAX_CONTEXT_BYTES:
        raise ValueError('agent/inherited-context-too-large')
    return payload
