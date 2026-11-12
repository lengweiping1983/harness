"""验证所属进程停止期间的取消和并发归属。"""
import asyncio
import io
import threading
import unittest

from harness.tools.owned_process import OwnedProcess
from harness.tools.conpty_process import ConPTYProcess


class BlockingProcess:
    """用可控停止边界替代操作系统信号，不创建用户进程。"""

    def __init__(self):
        self.pid = 42
        self.stdout, self.stderr = io.BytesIO(), io.BytesIO()
        self.entered, self.release = threading.Event(), threading.Event()
        self.stopped = False
        self.finished = threading.Event()
        self.calls = 0

    def active_count(self):
        return 0 if self.stopped else 1

    def root_code(self):
        return 0 if self.stopped else None

    def stop(self):
        self.calls += 1
        self.entered.set()
        if not self.release.wait(5):
            raise TimeoutError('test stop was not released')
        self.stopped = True
        self.finished.set()

    def wait(self):
        if not self.finished.wait(5):
            raise TimeoutError('test process did not exit')
        return 0

    def close_console(self):
        pass

    def close(self):
        self.stdout.close()
        self.stderr.close()


class OwnedProcessShutdownTests(unittest.IsolatedAsyncioTestCase):
    process_type = OwnedProcess

    async def test_cancelled_stopper_keeps_ownership_until_native_stop_finishes(self):
        native = BlockingProcess()
        process = self.process_type(native)
        first = asyncio.create_task(process.stop_owned())
        self.assertTrue(await asyncio.to_thread(native.entered.wait, 2))
        second = None
        try:
            first.cancel()
            for _ in range(3):
                await asyncio.sleep(0)
            self.assertFalse(first.done())
            second = asyncio.create_task(process.stop_owned())
            first.cancel()
            for _ in range(3):
                await asyncio.sleep(0)
            self.assertFalse(second.done())
            self.assertEqual(native.calls, 1)
        finally:
            native.release.set()
            results = await asyncio.gather(first, *([second] if second else []), return_exceptions=True)
            process.close()
        self.assertIsInstance(results[0], asyncio.CancelledError)
        self.assertEqual(native.calls, 1)
        self.assertTrue(process.cleanup_confirmed)
        if second:
            self.assertIsNone(results[1])


class ConPTYShutdownTests(OwnedProcessShutdownTests):
    process_type = ConPTYProcess
