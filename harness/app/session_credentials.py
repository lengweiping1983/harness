"""会话限定的身份凭据；唯一持久事实仍是原会话日志，不保存明文密钥。"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time

from .access_identity import AccessIdentity
from ..core.errors import HarnessError
from .collaboration_errors import collaboration_error_message

_KIND = 'collaboration/credential'
_MAX_TTL = 7 * 24 * 3600


def _fail():
    return HarnessError(collaboration_error_message('access/invalid-credential-record'), code='access/invalid-credential-record')


class SessionCredentials:
    """受信宿主签发给既有成员；凭据只证明身份和资源，不替代当前成员授权。"""

    def __init__(self, access):
        self.access = access

    def _records(self, session_id):
        records, seen = {}, set()
        for event in self.access.sessions.get(session_id).events_of_type('note'):
            data = event.data
            if data.get('kind') != _KIND:
                continue
            if not isinstance(data.get('session_id'), str) or not data['session_id']:
                raise _fail()
            if data['session_id'] != session_id:
                continue
            base = {'kind', 'session_id', 'action', 'actor', 'digest', 'access_revision'}
            issue = data.get('action') == 'issue'
            fields = base | {'subject', 'issued_at', 'expires_at', 'member_revision'} if issue else base
            if (set(data) != fields or not isinstance(data.get('action'), str) or data['action'] not in {'issue', 'revoke'}
                    or not isinstance(data.get('digest'), str) or not re.fullmatch('[0-9a-f]{64}', data['digest'])):
                raise _fail()
            state = self.access.snapshot(session_id, through=event.seq - 1)
            if (type(data['access_revision']) is not int or data['access_revision'] != state['revision']
                    or not isinstance(data['actor'], str)):
                raise _fail()
            digest = data['digest']
            manager = 'manage' in state['members'].get(data['actor'], ())
            own = (not issue and digest in records and records[digest]['subject'] == data['actor']
                   and 'read' in state['members'].get(data['actor'], ()))
            if not manager and not own:
                raise _fail()
            if issue:
                if (digest in seen or not isinstance(data['subject'], str)
                        or 'read' not in state['members'].get(data['subject'], ())
                        or type(data['member_revision']) is not int
                        or data['member_revision'] != state['member_epochs'].get(data['subject'])
                        or type(data['issued_at']) is not int or data['issued_at'] < 0 or type(data['expires_at']) is not int
                        or not 0 < data['expires_at'] - data['issued_at'] <= _MAX_TTL):
                    raise _fail()
                seen.add(digest)
                records[digest] = dict(data)
            elif digest not in records:
                raise _fail()
            else:
                records.pop(digest)
        return records

    async def issue(self, session_id, *, actor, subject, expires_in=3600):
        """仅在成功落盘后返回一次明文；成员授权先由唯一 SessionAccess 显式建立。"""
        if type(expires_in) is not int or not 0 < expires_in <= _MAX_TTL:
            raise ValueError(collaboration_error_message('access/invalid-expiry'))
        async with self.access.mutation_lock:
            state = self.access.require(session_id, actor, 'manage')
            self.access.require(session_id, subject, 'read')
            self._records(session_id)
            token = f'hs1.{session_id}.{secrets.token_urlsafe(32)}'
            digest = hashlib.sha256(token.encode()).hexdigest()
            issued = int(time.time())
            data = {'kind': _KIND, 'session_id': session_id, 'action': 'issue', 'actor': actor,
                    'subject': subject, 'digest': digest, 'access_revision': state['revision'],
                    'member_revision': state['member_epochs'][subject], 'issued_at': issued,
                    'expires_at': issued + expires_in}
            await self.access.sessions.get(session_id).append('note', data)
            return {'token': token, 'credential_id': digest, 'subject': subject,
                    'session_id': session_id, 'expires_at': data['expires_at']}

    async def revoke(self, session_id, *, actor, credential_id):
        """撤销不删除审计事实；已经撤销或不存在的摘要不重复写入。"""
        async with self.access.mutation_lock:
            state = self.access.require(session_id, actor, 'manage')
            if not isinstance(credential_id, str) or not re.fullmatch('[0-9a-f]{64}', credential_id):
                raise ValueError(collaboration_error_message('access/invalid-credential-id'))
            if credential_id not in self._records(session_id):
                return False
            await self.access.sessions.get(session_id).append('note', {
                'kind': _KIND, 'session_id': session_id, 'action': 'revoke', 'actor': actor,
                'digest': credential_id, 'access_revision': state['revision']})
            return True

    @staticmethod
    def _presented(headers):
        """仅解析受支持的头部形式，不把资源定位符当作身份。"""
        raw = headers.get('Authorization', '')
        if not isinstance(raw, str):
            return None
        match = re.fullmatch(r'Bearer +(hs1\.([A-Za-z0-9_-]{1,128})\.[A-Za-z0-9_-]{43})', raw, re.IGNORECASE)
        if match is None:
            return None
        return match.groups()

    async def relinquish(self, headers):
        """持钥者只可撤销该密钥；退出浏览器不需要也不取得管理权。"""
        async with self.access.mutation_lock:
            presented = self._presented(headers)
            if presented is None:
                return False
            token, session_id = presented
            digest = hashlib.sha256(token.encode()).hexdigest()
            try:
                record = self._records(session_id).get(digest)
                if record is None:
                    return False
                state = self.access.require(session_id, record['subject'], 'read')
            except (HarnessError, ValueError, KeyError, OSError):
                return False
            await self.access.sessions.get(session_id).append('note', {
                'kind': _KIND, 'session_id': session_id, 'action': 'revoke',
                'actor': record['subject'], 'digest': digest, 'access_revision': state['revision']})
            return True

    def authenticate(self, headers):
        """只接受 Authorization Bearer；资源定位符不授予权限，也不扫描其他会话。"""
        presented = self._presented(headers)
        if presented is None:
            return None
        token, session_id = presented
        try:
            digest = hashlib.sha256(token.encode()).hexdigest()
            records = self._records(session_id)
            record = records.get(digest)
            if record is None or not hmac.compare_digest(record['digest'], digest):
                return None
            now = time.time()
            if not record['issued_at'] <= now < record['expires_at']:
                return None
            state = self.access.require(session_id, record['subject'], 'read')
            if state['member_epochs'].get(record['subject']) != record['member_revision']:
                return None
            return AccessIdentity(record['subject'], frozenset({session_id}))
        except (HarnessError, ValueError, KeyError, OSError):
            return None
