"""验证终端探测和依赖安装复用清理等待时不遗落句柄。"""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from harness.app.terminal_setup import TerminalSetupService, verify_terminal


class TerminalSetupCleanupTests(unittest.IsolatedAsyncioTestCase):
    def process(self, *, probe=False):
        entered, release = asyncio.Event(), asyncio.Event()

        async def stop():
            entered.set()
            await release.wait()

        output = 'HARNESS_CONPTY_READY\n中文终端\n80 24\n'.encode() if probe else b''
        process = SimpleNamespace(stdout=SimpleNamespace(read=AsyncMock(side_effect=[output, b''] if probe else [b''])),
            stderr=SimpleNamespace(read=AsyncMock(return_value=b'')), wait=AsyncMock(return_value=0),
            stop_owned=AsyncMock(side_effect=stop), close=Mock())
        return process, entered, release

    async def cancel_during_cleanup(self, operation, process, entered, release):
        task = asyncio.create_task(operation())
        try:
            await asyncio.wait_for(entered.wait(), 2)
            for _ in range(2):
                task.cancel()
                await asyncio.sleep(0)
                self.assertFalse(task.done())
                process.close.assert_not_called()
        finally:
            release.set()
            result = (await asyncio.gather(task, return_exceptions=True))[0]
        self.assertIsInstance(result, asyncio.CancelledError)
        process.stop_owned.assert_awaited_once()
        process.close.assert_called_once()

    async def test_probe_cancel_waits_for_stop_and_close(self):
        process, entered, release = self.process(probe=True)
        with patch('harness.tools.conpty_process.spawn_conpty', AsyncMock(return_value=process)):
            await self.cancel_during_cleanup(verify_terminal, process, entered, release)

    async def test_install_cancel_waits_for_readers_and_close(self):
        process, entered, release = self.process()
        service = TerminalSetupService()
        with patch('harness.app.terminal_setup.spawn_owned', AsyncMock(return_value=process)):
            await self.cancel_during_cleanup(lambda: service._command('probe'), process, entered, release)
        process.stdout.read.assert_awaited_once()
        process.stderr.read.assert_awaited_once()

    async def test_probe_cleanup_error_is_reported_without_claiming_close(self):
        process, _, _ = self.process(probe=True)
        process.stop_owned = AsyncMock(side_effect=OSError('cleanup failed'))
        with patch('harness.tools.conpty_process.spawn_conpty', AsyncMock(return_value=process)):
            with self.assertRaisesRegex(OSError, 'cleanup failed'):
                await verify_terminal()
        process.close.assert_not_called()
