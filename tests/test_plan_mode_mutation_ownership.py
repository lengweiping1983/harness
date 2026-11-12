"""计划提交和等待者尚未结束时，取消或删除不能清除所属状态。"""
import asyncio
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from harness import Harness
from harness.app.plan_mode import PlanModeController
from harness.core.errors import HarnessError
from harness.session.store import SessionStore


class PlanModeMutationOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = SessionStore(directory.name)
        self.session = self.store.create()
        self.controller = PlanModeController(self.store)

    async def test_cancel_cannot_claim_to_cancel_a_commit_already_in_progress(self):
        await self.session.start_turn()
        await self.controller.set(self.session, True, narrate=False)
        entered, release = asyncio.Event(), asyncio.Event()
        append = self.session.append
        async def blocked(*args, **kwargs):
            if args[0] == 'plan/mode':
                entered.set()
                await release.wait()
            return await append(*args, **kwargs)
        with patch.object(self.session, 'append', side_effect=blocked):
            task = asyncio.create_task(self.controller.settle(self.session))
            try:
                await entered.wait()
                with self.assertRaises(HarnessError) as caught:
                    self.controller.cancel(self.session)
                self.assertEqual(caught.exception.code, 'plan/busy')
                self.assertIsNotNone(self.controller.projection(self.session).pending)
            finally:
                release.set()
                await task
        self.assertTrue(self.controller.projection(self.session).active)
        self.assertIsNone(self.controller.projection(self.session).pending)
        self.assertEqual(len(self.session.events_of_type('plan/mode')), 1)

    async def test_forget_keeps_held_lock_and_epoch_until_operation_finishes(self):
        lock = self.controller.review_lock(self.session.id)
        await lock.acquire()
        try:
            with self.assertRaises(HarnessError):
                self.controller.forget(self.session.id)
            self.assertIs(self.controller.review_lock(self.session.id), lock)
        finally:
            lock.release()
        self.controller.forget(self.session.id)
        self.assertIsNot(self.controller.review_lock(self.session.id), lock)

    async def test_forget_cannot_replace_a_lock_between_release_and_waiter_resume(self):
        lock = self.controller.review_lock(self.session.id)
        await lock.acquire()
        waiter = asyncio.create_task(self.controller.set(self.session, True, narrate=False))
        await asyncio.sleep(0)
        lock.release()
        try:
            with self.assertRaises(HarnessError):
                self.controller.forget(self.session.id)
            self.assertIs(self.controller.review_lock(self.session.id), lock)
        finally:
            await waiter
        self.assertTrue(self.controller.projection(self.session).active)

    async def test_queued_switch_can_still_be_cancelled_before_commit(self):
        await self.session.start_turn()
        await self.controller.set(self.session, True, narrate=False)
        epoch = self.controller.review_epoch(self.session.id)
        self.assertEqual(self.controller.cancel(self.session), 'cancelled')
        self.assertGreater(self.controller.review_epoch(self.session.id), epoch)
        self.assertEqual(await self.controller.settle(self.session), 'noop')
        self.assertFalse(self.controller.projection(self.session).active)
        self.assertEqual(self.session.events_of_type('plan/mode'), [])

    async def test_synchronous_delete_rejects_before_any_mutation(self):
        app = SimpleNamespace(plan=self.controller, terminals=SimpleNamespace(list=lambda _: []),
                              questions=Mock(), goals=Mock(), subagents=Mock(), sessions=Mock(), permissions=Mock(),
                              repeat_reminder=None, _agents={self.session.id: object()})
        lock = self.controller.review_lock(self.session.id)
        await lock.acquire()
        try:
            with self.assertRaises(HarnessError):
                Harness.delete_session(app, self.session.id)
            app.questions.cancel_session.assert_not_called()
            app.goals.clear.assert_not_called()
            app.sessions.delete.assert_not_called()
            self.assertIn(self.session.id, app._agents)
        finally:
            lock.release()
