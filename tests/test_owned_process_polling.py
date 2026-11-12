"""验证所属进程等待的退避、超时和整组退出边界。"""
import asyncio
import hashlib
import io
import os
import sys
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from harness.core.language import current_language
from harness.i18n import tr
from harness.tools.owned_process import OwnedProcess, PosixGroupProcess, spawn_owned
from harness.tools.windows_job import WindowsJobProcess
from harness.tools.processes import ProcessCleanupError


class PollingTests(unittest.TestCase):
    def process(self, kind, active, codes=None):
        process = object.__new__(kind)
        process.pid = 42
        process._code = None
        process._finished = False
        process._confirmed = False
        process._lock = threading.RLock()
        process._changed = Mock()
        process._scan_count = Mock(side_effect=active)
        process.active_count = process._scan_count
        process.root_code = Mock(side_effect=codes or [0] * len(active))
        process._proc = SimpleNamespace(wait=Mock(return_value=0))
        return process

    def test_long_wait_backs_off_and_caps_interval(self):
        for kind in (PosixGroupProcess, WindowsJobProcess):
            with self.subTest(kind=kind):
                process = self.process(kind, [1] * 8 + [0])
                self.assertEqual(process.wait(), 0)
                self.assertEqual([call.args[0] for call in process._changed.wait.call_args_list],
                                 [.02, .04, .08, .16, .25, .25, .25, .25])
                process._changed.notify_all.assert_called_once()

    def test_timeout_clamps_last_wait_and_does_not_confirm_cleanup(self):
        for kind in (PosixGroupProcess, WindowsJobProcess):
            with self.subTest(kind=kind):
                process = self.process(kind, [1] * 10)
                clock = [0.0]
                process._changed.wait.side_effect = lambda delay: clock.__setitem__(0, clock[0] + delay)
                module = kind.__module__
                with patch(f'{module}.time.monotonic', side_effect=lambda: clock[0]):
                    with self.assertRaises(ProcessCleanupError):
                        process.wait(.1)
                self.assertAlmostEqual(clock[0], .1)
                self.assertEqual(process._changed.wait.call_count, 3)
                self.assertFalse(process._finished)
                self.assertFalse(process._confirmed)
                process._changed.notify_all.assert_not_called()

    def test_root_exit_does_not_finish_before_descendants(self):
        for kind in (PosixGroupProcess, WindowsJobProcess):
            with self.subTest(kind=kind):
                process = self.process(kind, [1, 1, 0])
                self.assertEqual(process.wait(), 0)
                self.assertEqual(process._changed.wait.call_count, 2)
                self.assertEqual(process._scan_count.call_count, 3)

    def test_windows_empty_job_still_waits_for_signaled_root(self):
        process = self.process(WindowsJobProcess, [0, 0], [None, 0])
        self.assertEqual(process.wait(), 0)
        process._changed.wait.assert_called_once_with(.02)

    def test_stop_wakes_existing_waiters(self):
        for kind in (PosixGroupProcess, WindowsJobProcess):
            with self.subTest(kind=kind):
                process = self.process(kind, [1])
                process.wait = Mock(return_value=0)
                process.kernel = SimpleNamespace(TerminateJobObject=Mock(return_value=1))
                process.job = 123
                # 在 Windows 上模拟完整 POSIX 系统边界，仍验证停止与等待行为。
                with patch('harness.tools.owned_process.os.killpg', create=True), \
                     patch('harness.tools.owned_process.signal.SIGKILL', 9, create=True):
                    process.stop(.1)
                process._changed.notify_all.assert_called_once()
                process.wait.assert_called_once_with(.1)

    def test_condition_wait_releases_lock_for_stop(self):
        process = self.process(PosixGroupProcess, [1, 0])
        process._changed = threading.Condition(process._lock)
        entered = threading.Event()
        def scan():
            entered.set()
            return int(not process._finished)
        process._scan_count = scan
        result = []
        thread = threading.Thread(target=lambda: result.append(process.wait(2)))
        thread.start()
        try:
            self.assertTrue(entered.wait(1))
            with process._lock:
                process._finished = True
                process._code = 0
                process._changed.notify_all()
            thread.join(1)
            self.assertFalse(thread.is_alive())
            self.assertEqual(result, [0])
        finally:
            thread.join(3)


class OwnedInputLanguageTests(unittest.IsolatedAsyncioTestCase):
    async def test_input_errors_follow_runtime_language_including_the_writer_thread(self):
        """同一实例切换语言，分别核对未提供管道和写入线程的提前关闭。"""
        class ClosedWriter(io.BytesIO):
            def write(self, value):
                return 0
        native = SimpleNamespace(pid=42, stdin=None, stdout=io.BytesIO(), stderr=io.BytesIO(),
                                 active_count=lambda: 0, root_code=lambda: 0)
        native.close = lambda: [stream.close() for stream in (native.stdin, native.stdout, native.stderr)
                               if stream is not None]
        process = OwnedProcess(native)
        try:
            for language in ('en', 'zh-CN', 'en'):
                with self.subTest(language=language), current_language.set(language):
                    native.stdin = None
                    with self.assertRaises(RuntimeError) as missing:
                        await process.write_input(b'fixture')
                    self.assertEqual(str(missing.exception), tr('process.stdin.unavailable'))
                    native.stdin = ClosedWriter()
                    with self.assertRaises(BrokenPipeError) as closed:
                        await process.write_input(b'fixture')
                    self.assertEqual(str(closed.exception), tr('process.stdin.closed'))
                    self.assertTrue(native.stdin.closed)
        finally:
            process.close()


@unittest.skipUnless(os.name == 'nt' or sys.platform == 'darwin' or sys.platform.startswith('linux'),
                     '需要支持的所属进程平台')
class NativePollingTests(unittest.IsolatedAsyncioTestCase):
    async def test_anonymous_input_is_fully_written_drained_and_closed(self):
        """超过系统管道缓冲区的输入并发排空，不能阻塞所属停止执行器。"""
        payload = ('输入原文\n' * 32768).encode('utf-8')
        script = 'import hashlib,sys;sys.stdout.buffer.write(hashlib.sha256(sys.stdin.buffer.read()).digest())'
        process = await spawn_owned([sys.executable, '-X', 'utf8', '-c', script],
                                    cwd=os.getcwd(), env=dict(os.environ), stdin=True)
        try:
            result = await asyncio.wait_for(asyncio.gather(process.write_input(payload),
                process.stdout.read(4096), process.stderr.read(4096), process.wait()), 10)
            self.assertEqual(result, [None, hashlib.sha256(payload).digest(), b'', 0])
            self.assertTrue(process.cleanup_confirmed)
            self.assertTrue(process.native.stdin.closed)
        finally:
            await process.stop_owned()
            process.close()

    async def test_pending_native_wait_yields_and_stop_reclaims_process(self):
        process = await spawn_owned([sys.executable, '-c', 'import time; time.sleep(30)'],
                                    cwd=os.getcwd(), env=dict(os.environ))
        waiter = asyncio.create_task(process.wait())
        try:
            # 原生等待保持挂起时，事件循环仍可响应后续停止。
            await asyncio.sleep(.1)
            self.assertFalse(waiter.done())
            await asyncio.wait_for(process.stop_owned(), 5)
            await asyncio.wait_for(waiter, 2)
            self.assertTrue(process.cleanup_confirmed)
        finally:
            await process.stop_owned()
            await waiter
            process.close()
