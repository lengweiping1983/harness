"""工作流成员等待和清理遵循取消归属，失败不丢登记。"""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from harness.core.language import current_language
from harness.core.runs import AgentRun
from harness.i18n import tr
from harness.tools.builtin.workflow_host import WorkflowGuest, WorkflowLimits, _Child
from harness.tools.processes import ProcessCleanupError


class WorkflowChildCleanupTests(unittest.IsolatedAsyncioTestCase):
    def guest(self, count=1):
        manager = SimpleNamespace(interrupt=AsyncMock())
        guest = WorkflowGuest(manager, script='', meta={'name': 'Original'}, args=None, limits=WorkflowLimits())
        for call_id in range(1, count + 1):
            state = AgentRun(id=str(call_id), status='running')
            guest._children[call_id] = _Child(call_id, state.id, state)
            guest._schemas[call_id] = {'type': 'object'}
        return guest, manager

    async def test_result_waiter_cancel_does_not_return_partial_or_cancel_child_wait(self):
        guest, _ = self.guest()
        child = guest._children[1]
        release = asyncio.Event()
        child.wait_task = asyncio.create_task(release.wait())
        waiter = asyncio.create_task(guest._rpc_child_result({'callId': 1}))
        try:
            await asyncio.sleep(0)
            waiter.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await waiter
            self.assertFalse(child.wait_task.done())
            self.assertIs(guest._children[1], child)
        finally:
            release.set()
            await child.wait_task

    async def test_concurrent_disposal_and_repeated_cancel_share_owned_cleanup(self):
        guest, manager = self.guest()
        child = guest._children[1]
        entered, release = asyncio.Event(), asyncio.Event()

        async def interrupt(*args, **kwargs):
            entered.set()
            await release.wait()
            child.state.status = 'interrupted'

        manager.interrupt.side_effect = interrupt
        first = asyncio.create_task(guest._rpc_dispose_child({'callId': 1}))
        second = asyncio.create_task(guest._rpc_dispose_child({'callId': 1}))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            for _ in range(2):
                first.cancel()
                await asyncio.sleep(0)
                self.assertFalse(first.done())
                self.assertFalse(second.done())
                self.assertIs(guest._children[1], child)
                self.assertIn(1, guest._schemas)
        finally:
            release.set()
            results = await asyncio.gather(first, second, return_exceptions=True)
        self.assertIsInstance(results[0], asyncio.CancelledError)
        self.assertIsNone(results[1])
        manager.interrupt.assert_awaited_once()
        self.assertEqual(guest._children, {})
        self.assertEqual(guest._schemas, {})

    async def test_batch_failure_preserves_failed_child_and_continues_other_cleanup(self):
        guest, manager = self.guest(2)
        failed_child = guest._children[1]
        failure = RuntimeError('Original cleanup failure')

        async def interrupt(child_id, **kwargs):
            if child_id == '1':
                raise failure
            guest._children[2].state.status = 'interrupted'

        manager.interrupt.side_effect = interrupt
        stderr = asyncio.create_task(asyncio.sleep(0))
        with self.assertRaises(RuntimeError) as error:
            await guest._teardown(stderr)
        self.assertIs(error.exception, failure)
        self.assertIs(guest._children[1], failed_child)
        self.assertIn(1, guest._schemas)
        self.assertNotIn(2, guest._children)
        self.assertEqual(manager.interrupt.await_count, 2)
        self.assertIsNone(failed_child.disposal)

        async def retry(*args, **kwargs):
            failed_child.state.status = 'interrupted'

        manager.interrupt.side_effect = retry
        await guest._rpc_dispose_child({'callId': 1})
        self.assertEqual(guest._children, {})

    async def test_stop_returning_without_settlement_is_rejected_in_each_language(self):
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                guest, _ = self.guest()
                child = guest._children[1]
                with self.assertRaises(ProcessCleanupError) as error:
                    await guest._rpc_dispose_child({'callId': 1})
                self.assertEqual(error.exception.message, tr('workflow.child.cleanup_incomplete'))
                self.assertIs(guest._children[1], child)
                self.assertIn(1, guest._schemas)
                self.assertIsNone(child.disposal)

    async def test_terminal_status_does_not_release_pending_finalization(self):
        guest, manager = self.guest()
        child = guest._children[1]
        child.state.status = 'completed'
        finalization = asyncio.get_running_loop().create_future()
        child.state.finalization_task = finalization
        try:
            with self.assertRaises(ProcessCleanupError):
                await guest._rpc_dispose_child({'callId': 1})
            self.assertIs(guest._children[1], child)
            self.assertIn(1, guest._schemas)
            manager.interrupt.assert_awaited_once()
        finally:
            finalization.set_result(None)
        await guest._rpc_dispose_child({'callId': 1})
        self.assertEqual(guest._children, {})
