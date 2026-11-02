"""验证进程创建期间取消仍回收迟到的所属句柄。"""
import asyncio
from pathlib import Path
import subprocess
import sys
import threading
import textwrap
import unittest
from unittest.mock import AsyncMock, Mock, patch

from harness.tools.conpty_process import spawn_conpty
from harness.tools.owned_process import spawn_owned


class ProcessCreationCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_creation_and_loop_shutdown_do_not_spin_or_lose_late_handles(self):
        """外部截止时间覆盖创建自取消和循环取消快照，避免缺陷锁死本套件。"""
        script = textwrap.dedent('''
            import asyncio, faulthandler, threading
            from types import SimpleNamespace
            from unittest.mock import AsyncMock, Mock, patch
            from harness.tools.owned_process import spawn_owned
            from harness.tools.conpty_process import spawn_conpty
            faulthandler.dump_traceback_later(5, exit=True)
            async def check():
                for spawn, native, wrapper in (
                    (spawn_owned, 'harness.tools.windows_job.WindowsJobProcess',
                     'harness.tools.owned_process.OwnedProcess'),
                    (spawn_conpty, 'harness.tools.conpty_process.WindowsConPTYProcess',
                     'harness.tools.conpty_process.ConPTYProcess')):
                    cancelled = asyncio.CancelledError('fixture creation cancelled')
                    with patch('harness.tools.owned_process.os', SimpleNamespace(name='nt')), \
                         patch(native, side_effect=cancelled):
                        try:
                            await spawn(['fixture'], cwd='.', env={})
                        except asyncio.CancelledError as error:
                            assert error is cancelled
                        else:
                            raise AssertionError('creation cancellation was lost')
                    entered, release = threading.Event(), threading.Event()
                    process = Mock(stop_owned=AsyncMock())
                    def create(*args, **kwargs):
                        entered.set()
                        assert release.wait(2)
                        return object()
                    with patch('harness.tools.owned_process.os', SimpleNamespace(name='nt')), \
                         patch(native, side_effect=create), patch(wrapper, return_value=process):
                        task = asyncio.create_task(spawn(['fixture'], cwd='.', env={}))
                        assert await asyncio.to_thread(entered.wait, 2)
                        for pending in asyncio.all_tasks() - {asyncio.current_task()}:
                            pending.cancel()
                        release.set()
                        result = (await asyncio.gather(task, return_exceptions=True))[0]
                        assert isinstance(result, asyncio.CancelledError)
                        process.stop_owned.assert_awaited_once()
                        process.close.assert_called_once()
            asyncio.run(check())
            faulthandler.cancel_dump_traceback_later()
            print('creation cancellation and late ownership confirmed')
        ''')
        result = await asyncio.to_thread(subprocess.run, [sys.executable, '-X', 'utf8', '-c', script],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8'))
        self.assertIn(b'late ownership confirmed', result.stdout)

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
