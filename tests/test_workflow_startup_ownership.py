"""工作流启动取消接管迟到进程，收敛后才传播取消。"""
import asyncio
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from harness.core.language import current_language
from harness.i18n import tr
from harness.tools.builtin.workflow_host import WorkflowGuest, WorkflowLimits
from harness.tools.processes import ProcessCleanupError


class WorkflowStartupOwnershipTests(unittest.IsolatedAsyncioTestCase):
    def guest(self):
        return WorkflowGuest(SimpleNamespace(), script='return 1', meta={'name': 'Original'},
                             args=None, limits=WorkflowLimits())

    async def test_late_handle_cleanup_survives_repeated_cancellation(self):
        entered, release, stopping, stopped = [asyncio.Event() for _ in range(4)]
        proc = SimpleNamespace(returncode=None, pid=123, kill=Mock(), stdin=Mock(), _transport=Mock())

        async def wait():
            stopping.set()
            await stopped.wait()
            proc.returncode = -9
            return -9

        proc.wait = AsyncMock(side_effect=wait)

        async def create(*args, **kwargs):
            entered.set()
            await release.wait()
            return proc

        guest = self.guest()
        with patch('harness.tools.builtin.workflow_host.asyncio.create_subprocess_exec', side_effect=create):
            task = asyncio.create_task(guest.run())
            try:
                await asyncio.wait_for(entered.wait(), 2)
                task.cancel()
                await asyncio.sleep(0)
                self.assertFalse(task.done())
                release.set()
                await asyncio.wait_for(stopping.wait(), 2)
                for _ in range(2):
                    task.cancel()
                    await asyncio.sleep(0)
                    self.assertFalse(task.done())
                    proc._transport.close.assert_not_called()
            finally:
                release.set()
                stopped.set()
                result = (await asyncio.gather(task, return_exceptions=True))[0]
        self.assertIsInstance(result, asyncio.CancelledError)
        self.assertIs(guest._proc, proc)
        proc.kill.assert_called_once()
        proc.wait.assert_awaited_once()
        proc._transport.close.assert_called_once()

    async def test_cleanup_failure_keeps_handle_and_original_cause_in_each_language(self):
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                entered, release = asyncio.Event(), asyncio.Event()
                failure = OSError('Original cleanup failure')
                proc = SimpleNamespace(returncode=None, pid=123, kill=Mock(), stdin=Mock(),
                                       wait=AsyncMock(side_effect=failure), _transport=Mock())

                async def create(*args, **kwargs):
                    entered.set()
                    await release.wait()
                    return proc

                guest = self.guest()
                with patch('harness.tools.builtin.workflow_host.asyncio.create_subprocess_exec', side_effect=create):
                    task = asyncio.create_task(guest.run())
                    await asyncio.wait_for(entered.wait(), 2)
                    task.cancel()
                    release.set()
                    with self.assertRaises(ProcessCleanupError) as error:
                        await task
                self.assertIs(guest._proc, proc)
                self.assertIs(error.exception.__cause__, failure)
                self.assertEqual(error.exception.message, tr('workflow.process.cleanup_failed'))
                proc._transport.close.assert_not_called()

    async def test_real_late_process_is_stopped_and_reaped(self):
        entered, release = asyncio.Event(), asyncio.Event()
        create_process = asyncio.create_subprocess_exec
        processes = []

        async def create(*args, **kwargs):
            proc = await create_process(sys.executable, '-c', 'import time; time.sleep(30)',
                                        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                                        stderr=asyncio.subprocess.PIPE)
            processes.append(proc)
            entered.set()
            await release.wait()
            return proc

        guest = self.guest()
        with patch('harness.tools.builtin.workflow_host.asyncio.create_subprocess_exec', side_effect=create):
            task = asyncio.create_task(guest.run())
            try:
                await asyncio.wait_for(entered.wait(), 2)
                task.cancel()
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 5)
                self.assertIsNotNone(processes[0].returncode)
                self.assertIs(guest._proc, processes[0])
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)
                for proc in processes:
                    if proc.returncode is None:
                        proc.kill()
                    await proc.wait()
                    proc._transport.close()
