"""LSP源码预检有界且保留原始字节，可选预览不伪造正文。"""
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from harness.core.errors import ToolExecutionError
from harness.core.language import current_language
from harness.i18n import tr
from harness.tools.builtin.lsp_tools import build_lsp_tools, _read_lsp_source, _read_preview, LspClient
from harness.tools.registry import ToolRunContext


class LspSourceBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_bad_source_stops_before_server_start_and_preserves_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            target = root / 'Original-{limit}-原文.py'
            ctx = ToolRunContext('lsp', 'fixture', str(root), 1, 1)
            tool = build_lsp_tools(str(root))[0]
            with patch('harness.tools.builtin.lsp_tools.find_server', return_value=('fixture', [])), \
                 patch.object(LspClient, 'start', new=AsyncMock()) as start, \
                 patch('harness.tools.builtin.lsp_tools.MAX_SOURCE_BYTES', 16):
                for language in ('en', 'zh-CN', 'en'):
                    with current_language.set(language):
                        for original, key in [(b'Original\xff', 'lsp.source.invalid_utf8'),
                                              (b'Original\xf0\x9f', 'lsp.source.invalid_utf8'),
                                              (b'x' * 17, 'lsp.source.too_large')]:
                            target.write_bytes(original)
                            with self.assertRaises(ToolExecutionError) as error:
                                await tool.execute({'action': 'symbols', 'path': str(target)}, ctx)
                            self.assertEqual(error.exception.message, tr(key, path=str(target), limit=16))
                            self.assertEqual(target.read_bytes(), original)
                        target.write_bytes(b'Original')
                        failure = PermissionError('Original {reason} diagnostic')
                        with patch.object(Path, 'open', side_effect=failure), self.assertRaises(ToolExecutionError) as error:
                            await tool.execute({'action': 'symbols', 'path': str(target)}, ctx)
                        self.assertEqual(error.exception.message, tr('lsp.source.read_failed', path=str(target), reason=str(failure)))
                        self.assertIs(error.exception.__cause__, failure)
                start.assert_not_awaited()
                target.write_bytes(b'x' * 16)
                self.assertEqual(_read_lsp_source(target), 'x' * 16)

    def test_reads_are_bounded_and_preview_preserves_source_line_boundaries(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'fixture.py'
            target.write_bytes(b'keep')
            counts = []

            class Source(io.BytesIO):
                def read(self, count=-1):
                    counts.append(count)
                    return super().read(count)

            with patch('harness.tools.builtin.lsp_tools.MAX_SOURCE_BYTES', 16), \
                 patch.object(Path, 'open', return_value=Source(b'x' * 100)):
                with self.assertRaises(ToolExecutionError):
                    _read_lsp_source(target)
            self.assertEqual(counts, [17])
            counts.clear()
            with patch('harness.tools.builtin.lsp_tools.MAX_PREVIEW_BYTES', 10), \
                 patch.object(Path, 'open', return_value=Source('Original😀suffix'.encode())):
                self.assertEqual(_read_preview(str(target), 1), 'Original')
            self.assertEqual(counts, [11])
            target.write_text('Original\u2028same line\r\nSecond 原文😀\rThird', encoding='utf-8', newline='')
            self.assertEqual(_read_preview(str(target), 2), 'Second 原文😀')
            target.write_bytes(b'Original\xff')
            self.assertEqual(_read_preview(str(target), 1), '')
            self.assertEqual(target.read_bytes(), b'Original\xff')
