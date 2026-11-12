"""LSP 帧在读取正文前拒绝超限及歧义长度，正常连帧不丢字节。"""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from harness.core.language import current_language
from harness.i18n import tr
from harness.tools.builtin.lsp_tools import LspClient, _RpcError


class LspFrameBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_oversized_body_is_rejected_before_reading_it(self):
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                stream = AsyncMock()
                stream.read.return_value = b''
                client = LspClient(SimpleNamespace(stdout=stream))
                client._buffer = b'Content-Length: 33554433\r\n\r\n'
                with self.assertRaises(_RpcError) as caught:
                    await client._read_message()
                self.assertEqual(str(caught.exception), tr('lsp.frame_too_large'))
                stream.read.assert_not_awaited()

    async def test_oversized_header_and_duplicate_lengths_are_rejected(self):
        for header, key in ((b'X-Header: '+b'x'*65536+b'\r\n\r\n', 'lsp.frame_too_large'),
                            (b'Content-Length: 2\r\nContent-Length: 2\r\n\r\n{}', 'python.lsp_tools.0a2e394d11bf'),
                            (b'Content-Length: +2\r\n\r\n{}', 'python.lsp_tools.0a2e394d11bf')):
            stream = AsyncMock()
            stream.read.return_value = b''
            client = LspClient(SimpleNamespace(stdout=stream))
            client._buffer = header
            with self.assertRaises(_RpcError) as caught:
                await client._read_message()
            self.assertEqual(str(caught.exception), tr(key))
            stream.read.assert_not_awaited()

    async def test_fragmented_utf8_frames_keep_buffered_next_message(self):
        messages = [{'id': 1, 'result': 'Original 原文'}, {'method': 'original/notification'}]
        frames = []
        for message in messages:
            body = json.dumps(message, ensure_ascii=False).encode('utf-8')
            frames.append(b'Content-Length: '+str(len(body)).encode()+b'\r\n\r\n'+body)
        stream = AsyncMock()
        stream.read.side_effect = [frames[0][:10], frames[0][10:]+frames[1]]
        client = LspClient(SimpleNamespace(stdout=stream))
        self.assertEqual(await client._read_message(), messages[0])
        self.assertEqual(await client._read_message(), messages[1])
        self.assertEqual(stream.read.await_count, 2)
