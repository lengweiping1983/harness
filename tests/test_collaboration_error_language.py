"""共享协作真实授权和持久记录回归，消息本地化不得改变权限、状态码或原件。"""
import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from harness.app.collaboration_errors import collaboration_error_message
from harness.app.session_access import SessionAccess
from harness.app.access_identity import AccessIdentity
from harness.core.identity import validate_subject
from harness.app.session_invitations import SessionInvitations
from harness.core.errors import HarnessError
from harness.core.language import current_language
from harness.session.store import SessionStore
from harness.web.api.collaboration import build_router
from harness.web.errors import ApiError
from harness.web.router import Request
from harness.web.shared_projection import frame


class CollaborationErrorLanguageTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_session_and_disabled_sharing_use_registered_messages_without_mutation(self):
        store = SessionStore()
        session = store.create()
        original = tuple(session.events)
        decisions = Mock()
        loop = asyncio.get_running_loop()
        ctx = SimpleNamespace(shared_access=None,
            harness=SimpleNamespace(session_access=SessionAccess(store), session_decisions=decisions),
            call=lambda coro: asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=5))
        router = build_router(ctx)
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                for identity, code, status in (('ses_' + 'f' * 32, 'session/not-found', 403),
                                                (session.id, 'access/not-enabled', 409)):
                    path = f'/api/v1/sessions/{identity}/questions'
                    handler, params = router.resolve('GET', path)
                    with self.assertRaises(ApiError) as caught:
                        await asyncio.to_thread(handler, Request('GET', path, {}, {}, b''), **params)
                    self.assertEqual(caught.exception.code, code)
                    self.assertEqual(caught.exception.status, status)
                    self.assertEqual(caught.exception.message, collaboration_error_message(code))
                    self.assertNotIn(code, caught.exception.message)
        decisions.pending.assert_not_called()
        self.assertEqual(tuple(session.events), original)
        self.assertEqual([header.id for header in store.list_sessions()], [session.id])

    async def test_http_conflict_retains_status_and_code_with_translated_message(self):
        store = SessionStore()
        session = store.create()
        access = SessionAccess(store)
        state = await access.initialize(session.id, 'owner')
        invitations = SessionInvitations(access)
        invite = await invitations.create(session.id, actor='owner')
        await invitations.claim(invite['token'], display_name='User 原文', claim_secret='a' * 43)
        loop = asyncio.get_running_loop()
        ctx = SimpleNamespace(shared_access=None, harness=SimpleNamespace(session_access=access, session_invitations=invitations),
                              call=lambda coro: asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=5))
        handler, params = build_router(ctx).resolve('POST', f'/api/v1/sessions/{session.id}/invitations/{invite["id"]}/approve')
        request = Request('POST', '', {}, {}, json.dumps({'expected_revision': -10}).encode())
        for language, phrase in [('en', 'permissions changed'), ('zh-CN', '权限已变化'), ('en', 'permissions changed')]:
            with self.subTest(language=language), current_language.set(language):
                with self.assertRaises(ApiError) as caught:
                    await asyncio.to_thread(handler, request, **params)
                self.assertEqual(caught.exception.code, 'access/stale-revision')
                self.assertEqual(caught.exception.status, 409)
                self.assertIn(phrase, caught.exception.message)
                self.assertNotIn(caught.exception.code, caught.exception.message)
                self.assertEqual(access.snapshot(session.id)['members'], state['members'])
        # 本地化拒绝不能消费申请，正确版本仍能正常批准。
        approved = await invitations.approve(session.id, invite['id'], actor='owner', expected_revision=state['revision'])
        self.assertEqual(approved['members'][invitations.records(session.id)[invite['id']]['subject']], ['read', 'contribute'])

    async def test_numeric_digest_is_rejected_without_rewriting_original(self):
        for field, action in [('digest', 'create'), ('claim_digest', 'claim')]:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                store = SessionStore(Path(directory))
                session = store.create()
                access = SessionAccess(store)
                await access.initialize(session.id, 'owner')
                invitations = SessionInvitations(access)
                now = int(time.time())
                identifier = 'a' * 32
                record = {'kind': 'collaboration/invitation', 'session_id': session.id, 'action': 'create',
                          'id': identifier, 'actor': 'owner', 'role': 'collaborator', 'digest': '1' * 64,
                          'created_at': now, 'expires_at': now + 3600}
                if action == 'claim':
                    await session.append('note', record)
                    record = {'kind': 'collaboration/invitation', 'session_id': session.id, 'action': 'claim', 'id': identifier,
                              'claim_digest': '1' * 64, 'display_name': 'User 原文', 'subject': 'person_' + 'a' * 32, 'claimed_at': now}
                record[field] = int('1' * 64)
                await session.append('note', record)
                path = store.events_path(session.id)
                original = path.read_bytes()
                for language in ('en', 'zh-CN'):
                    with current_language.set(language), self.assertRaises(HarnessError) as caught:
                        invitations.records(session.id)
                    self.assertEqual(caught.exception.code, 'invitation/invalid-record')
                    self.assertEqual(path.read_bytes(), original)

    def test_shared_error_projection_never_exposes_private_exception(self):
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                for code in ('access/denied', 'provider/unavailable'):
                    projected = frame({'type': 'error', 'code': code, 'message': 'private credential material', 'details': {'secret': 'private'}})
                    self.assertEqual(projected['code'], code)
                    self.assertEqual(projected['message'], collaboration_error_message(code))
                    self.assertNotIn('private', json.dumps(projected))
                    self.assertNotEqual(projected['message'], code)

    async def test_invalid_subject_is_localized_without_appending_access_events(self):
        store = SessionStore()
        session = store.create()
        access = SessionAccess(store)
        await access.initialize(session.id, 'owner')
        original = tuple(session.events)
        for language, expected in [('en', 'The participant identity is invalid.'), ('zh-CN', '参与者身份无效。'), ('en', 'The participant identity is invalid.')]:
            with current_language.set(language):
                for value in ('', 'invalid subject', None, 3, 'a' * 129):
                    for call in (lambda: validate_subject(value), lambda: AccessIdentity(value)):
                        with self.subTest(language=language, value=value), self.assertRaises(HarnessError) as caught:
                            call()
                        self.assertEqual(caught.exception.code, 'access/invalid-subject')
                        self.assertEqual(caught.exception.message, expected)
                    with self.assertRaises(HarnessError) as caught:
                        await access.set_capabilities(session.id, actor='owner', subject=value, capabilities=['read'], expected_revision=access.snapshot(session.id)['revision'])
                    self.assertEqual(caught.exception.message, expected)
                    self.assertEqual(tuple(session.events), original)
                self.assertEqual(validate_subject('User:name_1@example.com'), 'User:name_1@example.com')

    def test_all_public_http_codes_reach_shared_projection_without_private_details(self):
        from harness.web.error_messages import ERROR_MESSAGE_KEYS
        from harness.i18n import tr
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                for code, key in ERROR_MESSAGE_KEYS.items():
                    projected = frame({'type': 'error', 'code': code, 'message': 'Private 原文',
                                       'details': {'token': 'Private 原文'}})
                    self.assertEqual(projected, {'type': 'error', 'code': code, 'message': tr(key, code=code)})
                    self.assertNotIn(code, projected['message'])
                self.assertEqual(collaboration_error_message('unknown/code'),
                                 tr('ui.collaboration.operation_failed', code='unknown/code'))
