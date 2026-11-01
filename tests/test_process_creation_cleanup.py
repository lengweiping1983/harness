"""验证进程创建期间取消仍回收迟到的所属句柄。"""
import asyncio
import threading
import unittest
from unittest.mock import AsyncMock, Mock, patch

from harness.tools.conpty_process import spawn_conpty
from harness.tools.owned_process import spawn_owned


class ProcessCreationCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def check_creation(self, spawn, native_path, wrapper_path):
        entered, release = threading.Event(), threading.Event()
        stopping, stopped = asyncio.Event(), asyncio.Event()
        process = Mock()

        def create(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise TimeoutError('creation was not released')
            return object()

        async def stop():
            stopping.set()
            await stopped.wait()

        process.stop_owned = AsyncMock(side_effect=stop)
        with patch(native_path, side_effect=create), patch(wrapper_path, return_value=process):
            task = asyncio.create_task(spawn(['fixture'], cwd='.', env={}))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                task.cancel()
                await asyncio.sleep(0)
                self.assertFalse(task.done())
                release.set()
                await asyncio.wait_for(stopping.wait(), 2)
                for _ in range(2):
                    task.cancel()
                    await asyncio.sleep(0)
                    self.assertFalse(task.done())
                    process.close.assert_not_called()
            finally:
                release.set()
                stopped.set()
                result = (await asyncio.gather(task, return_exceptions=True))[0]
        self.assertIsInstance(result, asyncio.CancelledError)
        process.stop_owned.assert_awaited_once()
        process.close.assert_called_once()

    async def test_conpty_creation_cancel_reclaims_late_handle(self):
        await self.check_creation(spawn_conpty, 'harness.tools.conpty_process.WindowsConPTYProcess',
                                  'harness.tools.conpty_process.ConPTYProcess')

    async def test_owned_creation_cancel_reclaims_late_handle(self):
        with patch('harness.tools.owned_process.os.name', 'nt'):
            await self.check_creation(spawn_owned, 'harness.tools.windows_job.WindowsJobProcess',
                                      'harness.tools.owned_process.OwnedProcess')
