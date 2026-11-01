"""权限状态以已提交事件为准，遗忘不能越过在途写入或等待者。"""
import asyncio
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from harness import Harness
from harness.app.permissions import PermissionPolicy
from harness.core.errors import HarnessError
from harness.session.store import SessionStore


class PermissionMutationOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = directory.name
        self.store = SessionStore(directory.name)
        self.session = self.store.create()
        host = SimpleNamespace(get_session=self.store.get, sessions=self.store,
                               config=SimpleNamespace(permission_policy='full_access'),
                               subagents=SimpleNamespace(state_for_session=lambda _: None))
        self.policy = PermissionPolicy(host)
        self.policy.publish = AsyncMock()

    async def test_postcommit_failure_adopts_real_policy_and_invalidates_grants(self):
        initial = self.policy.snapshot(self.session.id)
        changed = self.policy._changes.setdefault(self.session.id, asyncio.Event())
        self.policy.grants[(self.session.id, 'original')] = {'id': 'original'}
        failure = OSError('Original after commit 原文')
        append = self.session.append
        async def committed(*args, **kwargs):
            await append(*args, **kwargs)
            raise failure
        with patch.object(self.session, 'append', side_effect=committed) as write:
            with self.assertRaises(OSError) as caught:
                await self.policy.set_policy(self.session.id, 'read_only', expected_version=initial['version'])
            self.assertIs(caught.exception, failure)
            write.assert_awaited_once()
        actual = self.policy.snapshot(self.session.id)
        self.assertEqual(actual['permission_policy'], 'read_only')
        self.assertNotEqual(actual['version'], initial['version'])
        self.assertEqual(self.policy.grants, {})
        self.assertTrue(changed.is_set())
        self.assertEqual(len(self.session.events_of_type('note')), 1)

    async def test_cancel_after_durable_append_keeps_new_policy_and_wakes_old_waiters(self):
        initial = self.policy.snapshot(self.session.id)
        changed = self.policy._changes.setdefault(self.session.id, asyncio.Event())
        self.policy.grants[(self.session.id, 'original')] = {'id': 'original'}
        entered, release = asyncio.Event(), asyncio.Event()
        async def observer(session, event):
            if event.type == 'note' and event.data.get('kind') == 'permission_policy':
                entered.set()
                await release.wait()
        self.session.bus.on('session/append', observer)
        task = asyncio.create_task(self.policy.set_policy(self.session.id, 'read_only', expected_version=initial['version']))
        try:
            await entered.wait()
            self.assertEqual(self.policy.snapshot(self.session.id)['permission_policy'], 'read_only')
            self.assertEqual(self.policy.grants, {})
            self.assertTrue(changed.is_set())
            task.cancel('Original cancellation')
            with self.assertRaises(asyncio.CancelledError):
                await task
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
        reopened = SessionStore(self.root).get(self.session.id)
        self.assertEqual(reopened.latest('note').data['policy'], 'read_only')
        self.policy.publish.assert_not_awaited()

    async def test_unrelated_note_does_not_revoke_current_grants(self):
        original = self.policy.snapshot(self.session.id)
        changed = self.policy._changes.setdefault(self.session.id, asyncio.Event())
        grant = {'id': 'original'}
        self.policy.grants[(self.session.id, 'original')] = grant
        await self.session.append('note', {'kind': 'original unrelated audit'})
        self.assertEqual(self.policy.snapshot(self.session.id), original)
        self.assertIs(self.policy.grants[(self.session.id, 'original')], grant)
        self.assertFalse(changed.is_set())

    async def test_precommit_failure_keeps_original_policy_grants_and_version(self):
        initial = self.policy.snapshot(self.session.id)
        changed = self.policy._changes.setdefault(self.session.id, asyncio.Event())
        grant = {'id': 'original'}
        self.policy.grants[(self.session.id, 'original')] = grant
        failure = OSError('Original before commit 原文')
        with patch.object(self.session, 'append', AsyncMock(side_effect=failure)):
            with self.assertRaises(OSError) as caught:
                await self.policy.set_policy(self.session.id, 'read_only', expected_version=initial['version'])
            self.assertIs(caught.exception, failure)
        self.assertEqual(self.policy.snapshot(self.session.id), initial)
        self.assertIs(self.policy.grants[(self.session.id, 'original')], grant)
        self.assertFalse(changed.is_set())

    async def test_forget_cannot_replace_a_lock_with_a_waiter_ready_to_resume(self):
        initial = self.policy.snapshot(self.session.id)
        await self.policy.set_policy(self.session.id, None, expected_version=initial['version'])
        lock = self.policy._locks[self.session.id]
        await lock.acquire()
        waiter = asyncio.create_task(self.policy.set_policy(self.session.id, 'read_only', expected_version=initial['version']))
        await asyncio.sleep(0)
        lock.release()
        try:
            with self.assertRaises(HarnessError) as caught:
                self.policy.forget(self.session.id)
            self.assertEqual(caught.exception.code, 'permissions/busy')
            self.assertIs(self.policy._locks[self.session.id], lock)
        finally:
            await waiter

    async def test_delete_rejects_busy_policy_before_any_other_mutation(self):
        initial = self.policy.snapshot(self.session.id)
        await self.policy.set_policy(self.session.id, None, expected_version=initial['version'])
        lock = self.policy._locks[self.session.id]
        app = SimpleNamespace(permissions=self.policy, plan=SimpleNamespace(assert_idle=Mock(), forget=Mock()), terminals=SimpleNamespace(list=lambda _: []),
                              questions=Mock(), goals=Mock(), subagents=Mock(), sessions=Mock(),
                              repeat_reminder=None, _agents={})
        await lock.acquire()
        try:
            with self.assertRaises(HarnessError):
                Harness.delete_session(app, self.session.id)
            app.questions.cancel_session.assert_not_called()
            app.goals.clear.assert_not_called()
            app.sessions.delete.assert_not_called()
        finally:
            lock.release()
