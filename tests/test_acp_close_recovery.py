"""ACP 清理失败保留原诊断，逐项收尾并允许显式再次关闭。"""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from harness.acp.client import AcpClient, AcpError, AcpRunnerConfig


class AcpCloseRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_failure_still_closes_writer_and_joins_reader_then_retry(self):
        client = AcpClient(AcpRunnerConfig('fixture'))
        writer = SimpleNamespace(close=Mock())
        process = SimpleNamespace(stdin=writer)
        client._proc = process
        reader_task = client._reader = asyncio.create_task(asyncio.Event().wait())
        pending = asyncio.get_running_loop().create_future()
        client._requests[1] = pending
        failure = OSError('Original stop failure 原文')
        with patch('harness.acp.client.stop_process', new_callable=AsyncMock, side_effect=[failure, None]) as stop:
            try:
                with self.assertRaises(OSError) as caught:
                    await client.close()
                self.assertIs(caught.exception, failure)
                self.assertTrue(reader_task.done())
                writer.close.assert_called_once()
                with self.assertRaises(AcpError):
                    await pending
                await client.close()
                self.assertEqual(stop.await_count, 2)
            finally:
                if not pending.done():
                    pending.cancel()
                elif not pending.cancelled():
                    pending.exception()
                reader_task.cancel()
                await asyncio.gather(reader_task, return_exceptions=True)

    async def test_stop_and_writer_failures_both_remain_visible(self):
        client = AcpClient(AcpRunnerConfig('fixture'))
        stop_error, writer_error = OSError('stop'), OSError('writer')
        client._proc = SimpleNamespace(stdin=SimpleNamespace(close=Mock(side_effect=writer_error)))
        with patch('harness.acp.client.stop_process', new_callable=AsyncMock, side_effect=stop_error):
            with self.assertRaises(BaseExceptionGroup) as caught:
                await client.close()
        self.assertEqual(caught.exception.exceptions, (stop_error, writer_error))

    async def test_actual_stderr_reader_decodes_original_utf8_without_protocol_writes(self):
        client = AcpClient(AcpRunnerConfig('fixture'))
        reader = asyncio.StreamReader()
        reader.feed_data('Original stderr 原文'.encode('utf-8'))
        reader.feed_eof()
        client._proc = SimpleNamespace(stderr=reader)
        await client._drain_stderr()
        self.assertEqual(client.stderr_tail, 'Original stderr 原文')
