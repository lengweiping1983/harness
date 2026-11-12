"""成员交付统一发布：正式回复与中断片段先提交会话，再广播。"""
from __future__ import annotations

from typing import Any

from ..session.events import AppendOp
from ..agents.presentation import agent_presentation, team_member_identity
from ..i18n import tr


def freeze_member_authors(team: Any, registry: Any) -> dict[str, dict[str, Any]]:
    """冻结实际配置的作者，不由之后的目录选择、改名或模型正文推断。"""
    authors = {}
    for member in team.members:
        referenced = registry.get(member.agent) if member.agent else None
        authors[member.name] = {'team': team.name, 'member': member.name,
            'display_name': member.display_name or (agent_presentation(referenced)['display_name']
                if referenced else tr('agent.team.lead_display') if member.role == 'lead' else member.name),
            'role': member.role, 'identity': team_member_identity(team.name, member, referenced),
            'stage': 'member', **({'agent': member.agent} if member.agent else {})}
    return authors


async def publish_member_reply(session: Any, bus: Any, *, author: dict[str, Any],
                               text: str, ok: bool, state: dict[str, Any], turn: int):
    """调度者提供冻结作者与证据；片段不进入模型表面或冒充完整交付。"""
    if not text.strip():
        return None
    if state.get('partial') in {'stopped', 'failed', 'interrupted'}:
        request_id = state['partial_request_id']
        if any(event.data.get('request_id') == request_id for event in session.response_evidence):
            return None
        data = {'kind': 'assistant_partial', 'status': state['partial'], 'text': text,
            'thinking': '', 'request_id': request_id, 'turn': turn, 'author': author,
            'member_context_session_id': state.get('context_session_id'),
            'member_response_seq': state.get('member_response_seq'),
            'checkpoint_gap': state.get('checkpoint_gap', False)}
        event = await session.append('note', data, ignorable=True)
        await bus.emit('assistant/partial', {'session_id': session.id, **data,
            'source_seq': event.seq, 'source_time': event.time})
        return event
    data = {'role': 'assistant', 'content': [{'type': 'text', 'text': text}],
        'source': 'agent-team', 'turn': turn, 'author': author,
        'team_members': [author['member']], 'error': not ok,
        **({'member_context_session_id': state['context_session_id']} if state.get('context_session_id') else {}),
        **({'acceptance': state['acceptance']} if state.get('acceptance') else {})}
    event = await session.append('assistant/message', data, surface_op=AppendOp())
    await bus.emit('assistant/message', {'session_id': session.id, **data, 'text': text,
        'source_seq': event.seq, 'source_time': event.time})
    return event
