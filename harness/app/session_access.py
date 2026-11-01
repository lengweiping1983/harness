"""会话访问合同：权限事实仅存在原 Session 日志，不复制聊天或创建群存储。"""
from __future__ import annotations

import asyncio
import re

from ..core.errors import HarnessError
from .collaboration_errors import collaboration_error_message
from ..core.identity import validate_subject

CAPABILITIES = ('read', 'contribute', 'control', 'manage')
_KIND = 'collaboration/access'


def _fail(code):
    return HarnessError(collaboration_error_message(code), code=code)


def _capabilities(value):
    if (not isinstance(value, (list, tuple)) or any(item not in CAPABILITIES for item in value)
            or len(set(value)) != len(value) or value and 'read' not in value):
        raise _fail('access/invalid-capabilities')
    return tuple(item for item in CAPABILITIES if item in value)


class SessionAccess:
    """受信宿主使用的权限服务；调用者身份必须由接入层验证，不能信任消息正文。"""

    def __init__(self, sessions):
        self.sessions = sessions
        self.mutation_lock = asyncio.Lock()

    def snapshot(self, session_id, *, through=None):
        """从已提交事件重建授权；不存在配置时保持未启用，而非授予访客管理权。"""
        session = self.sessions.get(session_id)
        owner, revision, members, member_revisions = None, -1, {}, {}
        invitation_grants, profiles, member_epochs = {}, {}, {}
        for event in session.events_of_type('note'):
            if through is not None and event.seq > through:
                break
            data = event.data
            if data.get('kind') != _KIND:
                continue
            if not isinstance(data.get('session_id'), str) or not data['session_id']:
                raise _fail('access/invalid-record')
            # 分叉复制的是历史证据，原会话授权不能自动开放新会话。
            if data['session_id'] != session_id:
                continue
            base = {'kind', 'session_id', 'actor', 'subject', 'capabilities', 'expected_revision', 'owner'}
            invitation = 'invitation_id' in data
            if (set(data) != (base | {'invitation_id', 'display_name'} if invitation else base)
                    or type(data.get('expected_revision')) is not int or data['expected_revision'] != revision):
                raise _fail('access/invalid-record')
            try:
                actor, subject = validate_subject(data['actor']), validate_subject(data['subject'])
                capabilities = _capabilities(data['capabilities'])
            except HarnessError as exc:
                raise _fail('access/invalid-record') from exc
            if owner is None:
                if invitation or data['owner'] != subject or actor != subject or capabilities != CAPABILITIES:
                    raise _fail('access/invalid-record')
                owner = subject
            elif (data['owner'] != owner or 'manage' not in members.get(actor, ())
                  or subject == owner and capabilities != CAPABILITIES):
                raise _fail('access/invalid-record')
            if invitation:
                identifier = data['invitation_id']
                if (not isinstance(identifier, str) or not re.fullmatch('[0-9a-f]{32}', identifier)
                        or identifier in invitation_grants or subject in members
                        or not isinstance(data['display_name'], str) or not 1 <= len(data['display_name']) <= 80):
                    raise _fail('access/invalid-record')
                invitation_grants[identifier] = {'subject': subject, 'capabilities': list(capabilities), 'member_epoch': event.seq}
                profiles[subject] = {'display_name': data['display_name']}
            if capabilities:
                if subject not in members:
                    member_epochs[subject] = event.seq
                members[subject] = capabilities
            else:
                members.pop(subject, None)
            member_revisions[subject] = event.seq
            revision = event.seq
        return {'session_id': session_id, 'enabled': owner is not None, 'owner': owner,
                'revision': revision, 'invitation_grants': invitation_grants, 'profiles': profiles, 'member_epochs': member_epochs, 'member_revisions': member_revisions, 'members': {subject: list(values) for subject, values in members.items()}}

    def require(self, session_id, subject, capability):
        """读取当前授权，不接受客户端提供的能力列表或过期快照。"""
        subject = validate_subject(subject)
        if capability not in CAPABILITIES:
            raise _fail('access/invalid-capability')
        state = self.snapshot(session_id)
        if not state['enabled'] or capability not in state['members'].get(subject, ()):
            raise _fail('access/denied')
        return state

    async def initialize(self, session_id, owner):
        """仅供受信宿主首次启用；不能从匿名 HTTP 请求自动认领现有会话。"""
        owner = validate_subject(owner)
        async with self.mutation_lock:
            state = self.snapshot(session_id)
            if state['enabled']:
                if state['owner'] == owner:
                    return state
                raise _fail('access/already-initialized')
            await self.sessions.get(session_id).append('note', {'kind': _KIND, 'session_id': session_id, 'actor': owner,
                'subject': owner, 'capabilities': list(CAPABILITIES), 'expected_revision': -1, 'owner': owner})
            return self.snapshot(session_id)

    async def set_capabilities(self, session_id, *, actor, subject, capabilities, expected_revision):
        """按授权版本修改单个成员，提交成功后才发布变更；撤销使用空能力列表。"""
        actor, subject = validate_subject(actor), validate_subject(subject)
        capabilities = _capabilities(capabilities)
        async with self.mutation_lock:
            state = self.require(session_id, actor, 'manage')
            if type(expected_revision) is not int or expected_revision != state['revision']:
                raise _fail('access/stale-revision')
            if subject == state['owner'] and capabilities != CAPABILITIES:
                raise _fail('access/owner-required')
            if tuple(state['members'].get(subject, ())) == capabilities:
                return state
            await self.sessions.get(session_id).append('note', {'kind': _KIND, 'session_id': session_id, 'actor': actor,
                'subject': subject, 'capabilities': list(capabilities),
                'expected_revision': expected_revision, 'owner': state['owner']})
            return self.snapshot(session_id)
