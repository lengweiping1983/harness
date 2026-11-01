"""同一 LSP 工具实例按调用语言呈现，并拒绝损坏的协议编码。"""
import asyncio
import json
import unittest
from types import SimpleNamespace

from harness.core.language import current_language
from harness.tools.builtin.lsp_tools import LspClient, _RpcError, _format_symbols, build_lsp_tools, missing_server_message


class LspLanguageEncodingTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_language_for_symbols_install_hints_and_headers(self):
        with current_language.set('zh-CN'):
            tool = build_lsp_tools(None)[0]
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                symbols = _format_symbols([{'name': 'OriginalUserName', 'kind': 5}])
                self.assertIn('Class' if language == 'en' else '类', symbols)
                for kind, english, chinese in ((10, 'Enum', '枚举'), (21, 'Null', '空值'), (22, 'Enum member', '枚举成员')):
                    rendered = _format_symbols([{'name': 'Original-{symbol}', 'kind': kind}])
                    self.assertEqual(rendered, (english if language == 'en' else chinese) + ' Original-{symbol}')
                for target in ('java', 'lua', 'csharp', 'python', 'unknown'):
                    text = missing_server_message(target)
                    if language == 'en':
                        self.assertFalse(any('\u4e00' <= ch <= '\u9fff' for ch in text), text)
                        self.assertNotIn('、', text)
                result = tool.to_content({}, {'available': True, 'action': 'hover', 'language': 'python', 'text': 'Original 用户内容'})[0].text
                self.assertIn('Original 用户内容', result)
                self.assertTrue(result.startswith('(' if language == 'en' else '（'))
                self.assertEqual(len(_format_symbols([{'name':str(i),'kind':5} for i in range(4)], limit=2).splitlines()), 2)

    def test_all_registered_symbol_and_install_keys_reach_final_rendering(self):
        from harness.i18n import tr
        from harness.tools.builtin.lsp_tools import _SYMBOL_KIND_KEYS, _INSTALL_HINT_KEYS
        for language in ('en', 'zh-CN', 'en'):
            with self.subTest(language=language), current_language.set(language):
                for kind, key in _SYMBOL_KIND_KEYS.items():
                    self.assertEqual(_format_symbols([{'kind': kind, 'name': 'Original-{kind}-原文'}]),
                                     tr(key) + ' Original-{kind}-原文')
                for kind in (None, 0, 27, 'external-kind'):
                    self.assertEqual(_format_symbols([{'kind': kind, 'name': 'Original'}]),
                                     tr('python.lsp_tools.e892654a499d') + ' Original')
                for name, key in _INSTALL_HINT_KEYS.items():
                    self.assertIn(tr(key), missing_server_message(name))

    async def test_chunked_utf8_and_multiple_frames_preserve_text(self):
        reader = asyncio.StreamReader()
        packets = []
        for text in ('Original 中文😀', 'next'):
            body = json.dumps({'text': text}, ensure_ascii=False).encode()
            packets.append(f'Content-Length: {len(body)}\r\n\r\n'.encode() + body)
        async def feed():
            for byte in b''.join(packets):
                reader.feed_data(bytes([byte]))
                await asyncio.sleep(0)
            reader.feed_eof()
        task = asyncio.create_task(feed())
        client = LspClient(SimpleNamespace(stdout=reader))
        self.assertEqual(await client._read_message(), {'text': 'Original 中文😀'})
        self.assertEqual(await client._read_message(), {'text': 'next'})
        await task

    async def test_invalid_utf8_does_not_turn_into_a_successful_response(self):
        for raw in (b'{"result":"\xff"}', b'{"result":"\xe4\xb8"}'):
            for language in ('en', 'zh-CN'):
                with current_language.set(language), self.assertRaises(_RpcError):
                    # 每次使用完整独立帧，不重放已消费的协议输入。
                    reader = asyncio.StreamReader()
                    reader.feed_data(f'Content-Length: {len(raw)}\r\n\r\n'.encode() + raw)
                    reader.feed_eof()
                    client = LspClient(SimpleNamespace(stdout=reader))
                    await client._read_message()

    async def test_document_symbols_need_no_line_and_keep_workspace_and_document_separate(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import AsyncMock, patch
        from harness.core.errors import ToolExecutionError
        from harness.tools.builtin.lsp_tools import path_to_uri
        from harness.tools.registry import ToolRunContext

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            target = root / 'Original-{rootUri}-原文.py'
            lf_source = 'class Original: pass\n# 用户原文😀\n'
            sources = {
                'lf': lf_source,
                'crlf': lf_source.replace('\n', '\r\n'),
                'cr': lf_source.replace('\n', '\r'),
                'mixed': lf_source.replace('\n', '\r\n', 1),
                'no-final-newline': lf_source.removesuffix('\n'),
                'empty': '',
            }
            for ending, original in sources.items():
                with self.subTest(ending=ending):
                    # 按字节固定夹具，避免平台写入转换和读取归一化掩盖换行变化。
                    source_bytes = original.encode('utf-8')
                    target.write_bytes(source_bytes)
                    tool = build_lsp_tools(str(root))[0]
                    ctx = ToolRunContext('lsp', 'fixture', str(root), 1, 1)
                    for language in ('en', 'zh-CN', 'en'):
                        with current_language.set(language):
                            client = SimpleNamespace(request=AsyncMock(side_effect=[{}, [
                                {'name': 'Original', 'kind': 5, 'selectionRange': {'start': {'line': 0}}},
                            ]]), notify=AsyncMock(), close=AsyncMock())
                            with patch('harness.tools.builtin.lsp_tools.find_server', return_value=('fixture-lsp', [])), \
                                 patch.object(LspClient, 'start', new=AsyncMock(return_value=client)) as start:
                                value = await tool.execute({'action': 'symbols', 'path': str(target)}, ctx)
                                self.assertTrue(value['available'])
                                self.assertIsNone(value['line'])
                                self.assertIn('Original', value['text'])
                                initialize = client.request.await_args_list[0]
                                self.assertEqual(initialize.args[0], 'initialize')
                                self.assertEqual(initialize.args[1]['rootUri'], path_to_uri(root))
                                self.assertEqual(initialize.args[1]['rootPath'], str(root))
                                self.assertEqual(initialize.args[1]['workspaceFolders'][0]['uri'], path_to_uri(root))
                                query = client.request.await_args_list[1]
                                self.assertEqual(query.args[0], 'textDocument/documentSymbol')
                                self.assertEqual(query.args[1], {'textDocument': {'uri': path_to_uri(target)}})
                                opened = client.notify.await_args_list[1]
                                self.assertEqual(opened.args[0], 'textDocument/didOpen')
                                self.assertEqual(opened.args[1]['textDocument']['text'], original)
                                client.close.assert_awaited_once()
                                self.assertEqual(start.await_count, 1)
                                for action in ('definition', 'references', 'hover'):
                                    with self.assertRaises(ToolExecutionError):
                                        await tool.execute({'action': action, 'path': str(target)}, ctx)
                                self.assertEqual(start.await_count, 1)
                            self.assertEqual(target.read_bytes(), source_bytes)
