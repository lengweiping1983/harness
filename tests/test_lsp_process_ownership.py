"""LSP启动与关闭共用进程归属，取消不遗留所属子进程。"""
import asyncio
import contextlib
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from harness.tools.builtin.lsp_tools import LspClient
from harness.tools.processes import ProcessCleanupError


class LspProcessOwnershipTests(unittest.IsolatedAsyncioTestCase):
    def process(self):
        stdin = Mock()
        stdin.is_closing.return_value = False
        return SimpleNamespace(returncode=None, pid=123, stdin=stdin, terminate=Mock(), kill=Mock(), _transport=Mock())

    async def test_creation_cancel_reclaims_late_handle_before_propagating_repeated_cancel(self):
        entered, release, closing, closed = [asyncio.Event() for _ in range(4)]
        proc = self.process()

        async def communicate():
            closing.set()
            await closed.wait()
            proc.returncode = -15
            return b'', b''

        proc.communicate = AsyncMock(side_effect=communicate)

        async def create(*args, **kwargs):
            entered.set()
            await release.wait()
            return proc

        with patch('harness.tools.builtin.lsp_tools.asyncio.create_subprocess_exec', side_effect=create):
            task = asyncio.create_task(LspClient.start(['fixture'], '.'))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                task.cancel()
                await asyncio.sleep(0)
                self.assertFalse(task.done())
                release.set()
                await asyncio.wait_for(closing.wait(), 2)
                for _ in range(2):
                    task.cancel()
                    await asyncio.sleep(0)
                    self.assertFalse(task.done())
                    proc._transport.close.assert_not_called()
            finally:
                release.set()
                closed.set()
                result = (await asyncio.gather(task, return_exceptions=True))[0]
        self.assertIsInstance(result, asyncio.CancelledError)
        proc.terminate.assert_called_once()
        proc.communicate.assert_awaited_once()
        proc._transport.close.assert_called_once()

    async def test_concurrent_close_shares_cleanup_and_failure_allows_explicit_retry(self):
        proc = self.process()
        failure = OSError('Original cleanup failure')
        proc.communicate = AsyncMock(side_effect=[TimeoutError('initial timeout'), failure])
        client = LspClient(proc)
        with self.assertRaises(ProcessCleanupError) as error:
            await client.close()
        self.assertIs(error.exception.__cause__, failure)
        self.assertIs(client._proc, proc)
        proc._transport.close.assert_not_called()
        self.assertIsNone(client._close_task)
        entered, release = asyncio.Event(), asyncio.Event()

        async def communicate():
            entered.set()
            await release.wait()
            proc.returncode = 0
            return b'', b''

        proc.communicate = AsyncMock(side_effect=communicate)
        first = asyncio.create_task(client.close())
        second = asyncio.create_task(client.close())
        try:
            await asyncio.wait_for(entered.wait(), 2)
            first.cancel()
            await asyncio.sleep(0)
            first.cancel()
            await asyncio.sleep(0)
            self.assertFalse(first.done())
            self.assertFalse(second.done())
        finally:
            release.set()
            results = await asyncio.gather(first, second, return_exceptions=True)
        self.assertIsInstance(results[0], asyncio.CancelledError)
        self.assertIsNone(results[1])
        proc.communicate.assert_awaited_once()
        proc._transport.close.assert_called_once()

    async def test_real_controlled_process_is_reaped(self):
        client = await LspClient.start([sys.executable, '-c', 'import time; time.sleep(30)'], '.')
        try:
            await asyncio.wait_for(client.close(), 5)
            self.assertIsNotNone(client._proc.returncode)
            await client.close()
        finally:
            if client._proc.returncode is None:
                client._proc.kill()
            # 冻结在启动阶段的子进程可能对 kill 无响应，管道 read 永不 EOF；
            # 有界等待把这类环境级冻结转成快速失败，而不是无限挂死测试套件。
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(client._proc.communicate(), 10)
            client._proc._transport.close()
