"""团队成员私有会话索引和共享消息游标，随父聊天日志恢复。"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any
from ..i18n import tr


class TeamContexts:
    """运行句柄可结束，成员会话持续存在；配置变化保留旧分支。"""
    def __init__(self, session: Any, team: Any, registry: Any):
        self.session, self.team, self.registry = session, team, registry
        self.records = {}
        for event in session.events_of_type('note'):
            if event.data.get('kind') == 'collaboration/member-context':
                data = event.data
                if not isinstance(data.get('key'), str) or not isinstance(data.get('context_session_id'), str) or type(data.get('cursor')) is not int or data['cursor'] < -1:
                    from ..core.errors import HarnessError
                    raise HarnessError(tr('collaboration.context_invalid'), code='team/context-invalid')
                self.records[data['key']] = dict(data)

    def prepare(self, member: Any, prompt: str, workspace: str | None, model: str | None, read_only: bool) -> dict[str, Any]:
        """以真实配置和工作区隔离私有历史，只注入尚未读取的共享消息。"""
        referenced = self.registry.get(member.agent) if member.agent else None
        signature = {'owner': self.session.id, 'team': self.team.name, 'member': {key: value for key, value in member.to_dict().items() if key != 'display_name'},
            'agent': {'name': referenced.name, 'description': referenced.description, 'prompt': referenced.system_prompt, 'model': referenced.model,
                **({'skills': referenced.skills} if referenced.skills is not None else {}),
                'metadata': {key: value for key, value in referenced.metadata.items() if key not in ('display_name', 'profession', 'avatar')}} if referenced else None,
            'team_prompt': self.team.system_prompt, 'team_model': self.team.model,
            **({'team_skills': self.team.skills} if self.team.skills is not None else {}),
            'team_metadata': {key: value for key, value in self.team.metadata.items() if key not in ('display_name', 'profession', 'member_timeout_seconds')},
            'workspace': workspace or self.session.header.cwd, 'model': model, 'read_only': read_only}
        key = hashlib.sha256(json.dumps(signature, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()
        old = self.records.get(key)
        after = old['cursor'] if old else -1
        through = self.session.event_count - 1
        rows = []
        from .history import message_text, message_author
        # 增量引用有界；私有会话保留完整历史，过大时仍由原压缩机制处理。
        for event in self.session.surface:
            if not after < event.seq <= through or event.type not in ('user/message', 'assistant/message'):
                continue
            author = event.data.get('author')
            if old and event.data.get('member_context_session_id') == old['context_session_id'] and isinstance(author, Mapping) and author.get('team') == self.team.name and author.get('member') == member.name:
                continue
            text = message_text(event)
            rows.append({'seq': event.seq, 'role': 'user' if event.type == 'user/message' else 'assistant',
                'author': message_author(event), 'text': text[:2000], 'truncated': len(text) > 2000})
        clipped = len(rows) > 12
        rows = rows[-12:]
        while rows and len(json.dumps(rows, ensure_ascii=False)) > 12000:
            rows.pop(0)
            clipped = True
        ended = self.session.latest('turn/end')
        reason = ended.data.get('reason', {}) if ended else {}
        outcome = reason.get('kind') if isinstance(reason, Mapping) else reason
        history = json.dumps({'last_outcome': outcome, 'context_outcome': old.get('outcome') if old else None, 'session_id': self.session.id, 'after': after, 'through': through, 'truncated': clipped, 'history_tool': 'team_history', 'messages': rows}, ensure_ascii=False)
        return {'prompt': tr('agent.team.session_context', history=history, prompt=prompt),
            'context_session_id': old['context_session_id'] if old else None,
            'record': {'kind': 'collaboration/member-context', 'key': key, 'team': self.team.name,
                'member': member.name, 'cursor': through, 'previous_cursor': after}}

    async def started(self, state: Any) -> None:
        """运行开始先记录上下文身份，失败或中断后也不会静默丢弃私有会话。"""
        record = getattr(state, '_member_context_record', None)
        if record and state.session_id:
            value = {**record, 'context_session_id': state.session_id, 'outcome': 'pending'}
            await self.session.append('note', value, ignorable=True)
            self.records[value['key']] = value

    async def checkpoint(self, state: Any) -> None:
        """只在会话已闭合并落盘后推进索引，不把运行中的上下文交给下一任务。"""
        record = getattr(state, '_member_context_record', None)
        if not record or not state.session or state.session.open_turn is not None or state.session.persistence_error:
            return
        value = {**record, 'context_session_id': state.session_id, 'outcome': state.status}
        if self.records.get(value['key']) == value:
            return
        await self.session.append('note', value, ignorable=True)
        self.records[value['key']] = value
