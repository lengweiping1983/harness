"""原会话日志中的单人邀请与申请；授权事实仍由 SessionAccess 唯一维护。"""
from __future__ import annotations

import hashlib
import re
import secrets
import time

from ..core.errors import HarnessError
from .collaboration_errors import collaboration_error_message

ROLES = {'observer': ('read',), 'collaborator': ('read', 'contribute'),
         'administrator': ('read', 'contribute', 'control', 'manage')}
KIND = 'collaboration/invitation'


def fail(code):
    raise HarnessError(collaboration_error_message(code), code=code)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class SessionInvitations:
    """领取只产生待批准申请，持有邀请秘密本身不获得会话读取权。"""

    def __init__(self, access):
        self.access = access

    def records(self, session_id):
        rows = {}
        for event in self.access.sessions.get(session_id).events_of_type('note'):
            data = event.data
            if data.get('kind') != KIND or data.get('session_id') != session_id:
                continue
            action, identifier = data.get('action'), data.get('id')
            if not isinstance(identifier, str) or not re.fullmatch('[0-9a-f]{32}', identifier):
                fail('invitation/invalid-record')
            common = {'kind', 'session_id', 'action', 'id'}
            if action == 'create':
                if (set(data) != common | {'actor', 'role', 'digest', 'created_at', 'expires_at'}
                        or identifier in rows or not isinstance(data['role'], str) or data['role'] not in ROLES
                        or not isinstance(data['digest'], str) or not re.fullmatch('[0-9a-f]{64}', data['digest'])
                        or type(data['created_at']) is not int or type(data['expires_at']) is not int
                        or not 0 < data['expires_at'] - data['created_at'] <= 604800):
                    fail('invitation/invalid-record')
                state = self.access.snapshot(session_id, through=event.seq - 1)
                if 'manage' not in state['members'].get(data['actor'], ()):
                    fail('invitation/invalid-record')
                rows[identifier] = {**data, 'status': 'open'}
            elif action == 'claim':
                if (set(data) != common | {'claim_digest', 'display_name', 'subject', 'claimed_at'}
                        or identifier not in rows or rows[identifier]['status'] != 'open'
                        or type(data['claimed_at']) is not int
                        or not rows[identifier]['created_at'] <= data['claimed_at'] < rows[identifier]['expires_at']
                        or not isinstance(data['claim_digest'], str) or not re.fullmatch('[0-9a-f]{64}', data['claim_digest'])
                        or not isinstance(data['display_name'], str) or not 1 <= len(data['display_name']) <= 80
                        or not re.fullmatch('person_[0-9a-f]{32}', str(data['subject']))):
                    fail('invitation/invalid-record')
                rows[identifier].update(data, status='pending')
            elif action == 'revoke':
                if set(data) != common | {'actor'} or identifier not in rows:
                    fail('invitation/invalid-record')
                state = self.access.snapshot(session_id, through=event.seq - 1)
                if 'manage' not in state['members'].get(data['actor'], ()):
                    fail('invitation/invalid-record')
                rows[identifier]['status'] = 'revoked'
            else:
                fail('invitation/invalid-record')
        grants = self.access.snapshot(session_id)['invitation_grants']
        for identifier, grant in grants.items():
            row = rows.get(identifier)
            if row is None or row.get('subject') != grant['subject'] or tuple(grant['capabilities']) != ROLES[row['role']]:
                fail('invitation/invalid-record')
            row['status'] = 'approved'
        return rows

    async def create(self, session_id, *, actor, role='collaborator', expires_in=86400):
        """落盘成功后只返回一次邀请秘密；不存储原始秘密。"""
        if not isinstance(role, str) or role not in ROLES or type(expires_in) is not int or not 0 < expires_in <= 604800:
            fail('invitation/invalid-options')
        async with self.access.mutation_lock:
            self.access.require(session_id, actor, 'manage')
            self.records(session_id)
            identifier = secrets.token_hex(16)
            token = f'hi1.{session_id}.{identifier}.{secrets.token_urlsafe(32)}'
            now = int(time.time())
            await self.access.sessions.get(session_id).append('note', {'kind': KIND, 'session_id': session_id,
                'action': 'create', 'id': identifier, 'actor': actor, 'role': role, 'digest': digest(token),
                'created_at': now, 'expires_at': now + expires_in})
            return {'id': identifier, 'token': token, 'role': role, 'expires_at': now + expires_in}

    @staticmethod
    def parse(token, prefix):
        match = re.fullmatch(prefix + r'\.([A-Za-z0-9_-]{1,128})\.([0-9a-f]{32})\.([A-Za-z0-9_-]{43})', token if isinstance(token, str) else '')
        if match is None:
            fail('invitation/invalid-token')
        return match.group(1), match.group(2)

    async def claim(self, token, *, display_name, claim_secret):
        """客户端保留随机领取秘密，超时可查回同一申请，不能重复领取他人申请。"""
        session_id, identifier = self.parse(token, 'hi1')
        if (not isinstance(display_name, str) or not 1 <= len(display_name.strip()) <= 80
                or any(ord(c) < 32 for c in display_name)
                or not isinstance(claim_secret, str) or not re.fullmatch('[A-Za-z0-9_-]{43}', claim_secret)):
            fail('invitation/invalid-claim')
        async with self.access.mutation_lock:
            row = self.records(session_id).get(identifier)
            if row is None or not secrets.compare_digest(row['digest'], digest(token)):
                fail('invitation/invalid-token')
            if row['status'] in {'pending', 'approved'} and row.get('claim_digest') == digest(claim_secret):
                self.claim_status(session_id, identifier, claim_secret)
                if row['display_name'] != display_name.strip():
                    fail('invitation/claim-conflict')
                return {'id': identifier, 'status': row['status']}
            if row['status'] != 'open' or not row['created_at'] <= time.time() < row['expires_at']:
                fail('invitation/unavailable')
            await self.access.sessions.get(session_id).append('note', {'kind': KIND, 'session_id': session_id,
                'action': 'claim', 'id': identifier, 'claimed_at': int(time.time()), 'claim_digest': digest(claim_secret),
                'display_name': display_name.strip(), 'subject': 'person_' + secrets.token_hex(16)})
            return {'id': identifier, 'status': 'pending'}

    def list(self, session_id, actor):
        self.access.require(session_id, actor, 'manage')
        return [{key: value for key, value in row.items() if key in
                 {'id', 'role', 'status', 'display_name', 'subject', 'created_at', 'expires_at'}}
                for source in self.records(session_id).values()
                for row in [{**source, 'status': 'expired' if source['status'] in {'open', 'pending'}
                             and time.time() >= source['expires_at'] else source['status']}]]

    async def approve(self, session_id, identifier, *, actor, expected_revision):
        """一次授权事件同时消费邀请，重试不重复生成成员。"""
        async with self.access.mutation_lock:
            state = self.access.require(session_id, actor, 'manage')
            row = self.records(session_id).get(identifier)
            if row is not None and row['status'] == 'approved':
                return state
            if row is None or row['status'] != 'pending' or time.time() >= row['expires_at']:
                fail('invitation/unavailable')
            if type(expected_revision) is not int or expected_revision != state['revision']:
                fail('access/stale-revision')
            await self.access.sessions.get(session_id).append('note', {'kind': 'collaboration/access',
                'session_id': session_id, 'actor': actor, 'subject': row['subject'],
                'capabilities': list(ROLES[row['role']]), 'expected_revision': state['revision'],
                'owner': state['owner'], 'invitation_id': identifier, 'display_name': row['display_name']})
            return self.access.snapshot(session_id)

    async def revoke(self, session_id, identifier, *, actor):
        """撤销开放或待批邀请；已加入成员应通过原成员权限服务移除。"""
        async with self.access.mutation_lock:
            self.access.require(session_id, actor, 'manage')
            row = self.records(session_id).get(identifier)
            if row is None or row['status'] == 'revoked':
                return False
            if row['status'] == 'approved':
                fail('invitation/already-approved')
            await self.access.sessions.get(session_id).append('note', {'kind': KIND, 'session_id': session_id,
                'action': 'revoke', 'id': identifier, 'actor': actor})
            return True

    def claim_status(self, session_id, identifier, claim_secret):
        row = self.records(session_id).get(identifier)
        if row is None or not isinstance(claim_secret, str) or not secrets.compare_digest(row.get('claim_digest', ''), digest(claim_secret)):
            fail('invitation/invalid-token')
        if time.time() >= row['expires_at']:
            fail('invitation/unavailable')
        if row['status'] == 'approved':
            state = self.access.require(session_id, row['subject'], 'read')
            if state['member_epochs'].get(row['subject']) != state['invitation_grants'][identifier]['member_epoch']:
                fail('invitation/unavailable')
        return row
