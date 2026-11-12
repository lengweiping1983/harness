"""运行期进程清理有界，失败仍完成其他收尾并保留句柄。"""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from harness.core.language import current_language
from harness.i18n import tr
from harness.tools.builtin.workflow_host import WorkflowGuest, WorkflowLimits
from harness.tools.processes import ProcessCleanupError


class WorkflowProcessTeardownTests(unittest.IsolatedAsyncioTestCase):
    def guest(self):
        guest = WorkflowGuest(SimpleNamespace(), script='', meta={'name': 'Original'}, args=None, limits=WorkflowLimits())
        proc = SimpleNamespace(pid=123, returncode=None, stdin=Mock(), _transport=Mock(), kill=Mock())
        guest._proc = proc
        return guest, proc

    async def test_graceful_timeout_and_forced_timeout_are_reported_without_unbounded_wait(self):
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                guest, proc = self.guest()
                failure = TimeoutError('Original forced timeout')
                proc.wait = AsyncMock(side_effect=[TimeoutError('graceful timeout'), failure])
                stderr = asyncio.create_task(asyncio.Event().wait())
                with self.assertRaises(ProcessCleanupError) as error:
                    await asyncio.wait_for(guest._teardown(stderr), 1)
                self.assertIs(error.exception.__cause__, failure)
                self.assertEqual(error.exception.message, tr('workflow.process.cleanup_failed'))
                self.assertIs(guest._proc, proc)
                self.assertTrue(stderr.done())
                proc.kill.assert_called_once()
                proc._transport.close.assert_not_called()
                self.assertEqual(proc.wait.await_count, 2)

    async def test_cancelled_run_stops_immediately_and_closes_transport_only_after_exit(self):
        guest, proc = self.guest()
        guest._cancelled = True
        entered, release = asyncio.Event(), asyncio.Event()

        async def wait():
            entered.set()
            await release.wait()
            proc.returncode = -9
            return -9

        proc.wait = AsyncMock(side_effect=wait)
        stderr = asyncio.create_task(asyncio.Event().wait())
        closing = asyncio.create_task(guest._teardown(stderr))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            proc.kill.assert_called_once()
            proc._transport.close.assert_not_called()
            self.assertFalse(closing.done())
        finally:
            release.set()
            await closing
        proc.wait.assert_awaited_once()
        proc._transport.close.assert_called_once()
        self.assertTrue(stderr.done())

    async def test_member_and_process_cleanup_failures_are_both_preserved(self):
        guest, proc = self.guest()
        guest._children[1] = SimpleNamespace(wait_task=None)
        member_error = RuntimeError('Original member cleanup failure')
        process_error = OSError('Original process cleanup failure')
        guest._rpc_dispose_child = AsyncMock(side_effect=member_error)
        proc.wait = AsyncMock(side_effect=process_error)
        stderr = asyncio.create_task(asyncio.sleep(0))
        with self.assertRaises(ExceptionGroup) as error:
            await guest._teardown(stderr)
        self.assertIs(error.exception.exceptions[0], member_error)
        self.assertIs(error.exception.exceptions[1].__cause__, process_error)
        self.assertTrue(stderr.done())
        self.assertIs(guest._proc, proc)
        proc._transport.close.assert_not_called()
