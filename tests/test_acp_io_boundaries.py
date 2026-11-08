"""ACP 排队发送及超长协议行必须保持关闭边界和调用语言。"""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from harness.acp.client import AcpClient, AcpError, AcpRunnerConfig
from harness.core.language import current_language
from harness.i18n import tr


class AcpIoBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_queued_send_rechecks_closed_connection_before_writing(self):
        for language in ('en', 'zh-CN', 'en'):
            with self.subTest(language=language), current_language.set(language):
                client = AcpClient(AcpRunnerConfig('fixture'))
                writer = SimpleNamespace(write=Mock(), drain=AsyncMock(), close=Mock())
                process = SimpleNamespace(stdin=writer)
                client._proc = process
                await client._write_lock.acquire()
                sending = asyncio.create_task(client._send({'jsonrpc': '2.0', 'method': 'fixture'}))
                await asyncio.sleep(0)
                self.assertFalse(sending.done())
                with patch('harness.acp.client.stop_process', new_callable=AsyncMock) as stop:
                    await client.close()
                    stop.assert_awaited_once_with(process)
                client._write_lock.release()
                with self.assertRaises(AcpError) as caught:
                    await sending
                self.assertEqual(str(caught.exception), tr('acp.client.3041b8ac6fcf'))
                writer.write.assert_not_called()
                writer.drain.assert_not_awaited()

    async def test_oversized_streamreader_line_uses_frame_error_and_closes_process(self):
        for language in ('en', 'zh-CN', 'en'):
            with self.subTest(language=language), current_language.set(language):
                client = AcpClient(AcpRunnerConfig('fixture', max_frame_bytes=1024))
                reader = asyncio.StreamReader(limit=1025)
                reader.feed_data(b'x' * 1026 + b'\n')
                process = SimpleNamespace(stdout=reader, stdin=SimpleNamespace(close=Mock()))
                client._proc = process
                pending = asyncio.get_running_loop().create_future()
                client._requests[1] = pending
                with patch('harness.acp.client.stop_process', new_callable=AsyncMock) as stop:
                    await client._read()
                    stop.assert_awaited_once_with(process)
                with self.assertRaises(AcpError) as caught:
                    await pending
                self.assertEqual(str(caught.exception), tr('acp.client.65516b50779b'))
                self.assertTrue(client._closed)

    async def test_non_finite_json_numbers_reject_frame_and_close_process(self):
        for language in ('en', 'zh-CN', 'en'):
            with self.subTest(language=language), current_language.set(language):
                for constant in ('NaN', 'Infinity', '-Infinity'):
                    client = AcpClient(AcpRunnerConfig('fixture'))
                    reader = asyncio.StreamReader()
                    reader.feed_data((f'{{"jsonrpc":"2.0","id":1,"result":{{"value":{constant}}}}}\n').encode())
                    reader.feed_eof()
                    process = SimpleNamespace(stdout=reader, stdin=SimpleNamespace(close=Mock()))
                    client._proc = process
                    pending = asyncio.get_running_loop().create_future()
                    client._requests[1] = pending
                    with patch('harness.acp.client.stop_process', new_callable=AsyncMock) as stop:
                        await client._read()
                        stop.assert_awaited_once_with(process)
                    with self.assertRaises(AcpError) as caught:
                        await pending
                    self.assertEqual(str(caught.exception), tr('acp.client.761c95d5aa14'))
                    self.assertTrue(client._closed)

    async def test_valid_send_keeps_original_utf8_frame(self):
        client = AcpClient(AcpRunnerConfig('fixture'))
        writer = SimpleNamespace(write=Mock(), drain=AsyncMock())
        client._proc = SimpleNamespace(stdin=writer)
        await client._send({'jsonrpc': '2.0', 'method': 'Original 原文'})
        writer.write.assert_called_once_with(b'{"jsonrpc":"2.0","method":"Original \xe5\x8e\x9f\xe6\x96\x87"}\n')
        writer.drain.assert_awaited_once()
