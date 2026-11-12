"""按需读取共享聊天原始消息，截取窗口不是唯一可用的背景来源。"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any
from ..i18n import tr
from ..tools.registry import ToolDefinition, ToolResult
from ..llm.types import TextBlock
from ..core.errors import ToolArgsError, ToolDeniedError


def message_text(event: Any) -> str:
    """兼容文字及分块消息，思考和工具私有过程不混入聊天正文。"""
    text = event.data.get('text')
    if isinstance(text, str):
        return text
    content = event.data.get('content', ())
    if isinstance(content, str):
        return content
    return '\n'.join(part['text'] for part in content if isinstance(part, Mapping) and isinstance(part.get('text'), str) and part.get('type') == 'text') if isinstance(content, (tuple, list)) else ''


def message_author(event: Any) -> dict[str, Any]:
    """从宿主冻结的团队作者或单 Agent 展示身份投影，不从正文猜身份或授予权限。"""
    value = event.data.get('author')
    if not isinstance(value, Mapping):
        value = event.data.get('speaker')
    return {key: text[:256] for key, text in value.items() if key in ('team', 'member', 'identity', 'display_name', 'profession', 'avatar', 'role', 'stage', 'agent') and isinstance(text, str)} if isinstance(value, Mapping) else {}


def history_page(session: Any, *, after: int = -1, through: int | None = None, query: str = '', limit: int = 10, message_seq: int | None = None, offset: int = 0) -> dict[str, Any]:
    """固定上界的序号分页和全文分块，不静默遗失长消息或已压缩的原文。"""
    high = session.event_count - 1 if through is None else through
    if type(after) is not int or type(high) is not int or after < -1 or not -1 <= high < session.event_count or after > high or type(limit) is not int or not 1 <= limit <= 20 or not isinstance(query, str) or len(query) > 200 or type(offset) is not int or offset < 0:
        raise ToolArgsError(tr('collaboration.history.invalid'))
    if message_seq is not None:
        if type(message_seq) is not int or not 0 <= message_seq <= high:
            raise ToolArgsError(tr('collaboration.history.invalid'))
        event = session.event_at(message_seq)
        if event is None or event.type not in ('user/message', 'assistant/message'):
            raise ToolArgsError(tr('collaboration.history.invalid'))
        text = message_text(event)
        if offset > len(text):
            raise ToolArgsError(tr('collaboration.history.invalid'))
        end = min(len(text), offset + 4000)
        return {'session_id': session.id, 'through': high, 'message_seq': event.seq,
            'role': 'user' if event.type == 'user/message' else 'assistant', 'author': message_author(event),
            'offset': offset, 'end': end, 'total': len(text), 'text': text[offset:end], 'next_offset': end if end < len(text) else None}
    rows = []
    row_chars = 0
    budget_more = False
    pattern = re.compile(re.escape(query), re.IGNORECASE) if query else None
    for seq in range(after + 1, high + 1):
        event = session.event_at(seq)
        if event is None or event.type not in ('user/message', 'assistant/message') or event.data.get('source') == 'compaction':
            continue
        text = message_text(event)
        found = pattern.search(text) if pattern else None
        if not text or pattern and found is None:
            continue
        start = max(0, (found.start() if found else 0) - 200)
        snippet = text[start:start + 1600]
        row = {'seq': event.seq, 'time': event.time, 'role': 'user' if event.type == 'user/message' else 'assistant',
            'author': message_author(event), 'text': snippet, 'offset': start, 'total': len(text),
            'truncated': start > 0 or len(snippet) < len(text)}
        size = len(json.dumps(row, ensure_ascii=False))
        if rows and row_chars + size > 9000:
            budget_more = True
            break
        rows.append(row)
        row_chars += size
        if len(rows) > limit:
            break
    more = budget_more or len(rows) > limit
    rows = rows[:limit]
    return {'session_id': session.id, 'through': high, 'messages': rows, 'has_more': more,
        'next_after': rows[-1]['seq'] if more else None}


def build_team_history_tool(harness: Any) -> ToolDefinition:
    """聊天归属由宿主提供，模型参数不能指定其他会话或同伴的私有会话。"""
    def execute(args, context):
        state = harness.subagents.state_for_session(context.session_id)
        candidate, seen = state, set()
        while candidate is not None and not getattr(candidate, '_team_history_scope', None) and candidate.parent_session_id not in seen:
            seen.add(candidate.parent_session_id)
            candidate = harness.subagents.state_for_session(candidate.parent_session_id)
        scope = getattr(candidate, '_team_history_scope', None)
        source = getattr(state, '_team_steering_source', None) or getattr(getattr(state, 'agent', None), 'steering_source', None)
        if scope is not None:
            if scope not in harness._active_team_runs:
                raise ToolDeniedError(tr('collaboration.history.unavailable'))
            session = harness._agent_session(scope[0])
            if session is None:
                raise ToolDeniedError(tr('collaboration.history.unavailable'))
        elif source is not None:
            controller = source.controller
            if controller.closed or controller.session.open_turn != controller.turn:
                raise ToolDeniedError(tr('collaboration.history.unavailable'))
            session = controller.session
        else:
            session = harness._agent_session(context.session_id)
            if session is None:
                raise ToolDeniedError(tr('collaboration.history.unavailable'))
        data = history_page(session, **args)
        return ToolResult([TextBlock(json.dumps(data, ensure_ascii=False))], data=data,
            display={'kind': 'team-history', 'session_id': session.id, 'query': args.get('query', ''), 'messages': len(data.get('messages', []))})
    return ToolDefinition({'name': 'team_history', 'description': tr('collaboration.history.description'), 'description_key': 'collaboration.history.description',
        'parameters': {'type': 'object', 'additionalProperties': False, 'properties': {
            'query': {'type': 'string', 'maxLength': 200}, 'after': {'type': 'integer', 'minimum': -1},
            'through': {'type': 'integer', 'minimum': -1}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 20},
            'message_seq': {'type': 'integer', 'minimum': 0}, 'offset': {'type': 'integer', 'minimum': 0},
        }}}, execute, effect='read', category='session')
