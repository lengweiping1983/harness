"""读取和搜索严格保留UTF-8原文及有界截断语义。"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness.core.errors import ToolExecutionError
from harness.core.language import current_language
from harness.i18n import tr
from harness.tools.builtin.fs_tools import _read_snapshot, build_fs_tools
from harness.tools.builtin.fs_text import decode_utf8_prefix
from harness.tools.builtin.fs_grep_worker import read_text
from harness.tools.registry import ToolRunContext


class FileUtf8PrefixTests(unittest.IsolatedAsyncioTestCase):
    def test_partial_characters_are_omitted_but_invalid_sequences_are_rejected(self):
        for char in ('é', '中', '😀'):
            encoded = char.encode()
            for end in range(1, len(encoded)):
                prefix = b'Original' + encoded[:end]
                self.assertEqual(decode_utf8_prefix(prefix, truncated=True), 'Original')
                with self.assertRaises(UnicodeDecodeError):
                    decode_utf8_prefix(prefix, truncated=False)
        for data in (b'bad\xff', b'\xc0\x80', b'\xed\xa0\x80', b'\xf4\x90\x80\x80'):
            for truncated in (True, False):
                with self.assertRaises(UnicodeDecodeError):
                    decode_utf8_prefix(data, truncated=truncated)
        self.assertEqual(decode_utf8_prefix('Original �😀'.encode(), truncated=False), 'Original �😀')

    async def test_real_reads_and_search_worker_preserve_corrupt_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            bad = root / 'invalid.txt'
            tools = {tool.schema.name: tool for tool in build_fs_tools(str(root))}
            ctx = ToolRunContext(call_id='utf8', session_id='fixture', cwd=str(root), turn=1, step=1)
            for original in (b'Original\xff', b'Original\xf0\x9f'):
                bad.write_bytes(original)
                for language in ('en', 'zh-CN', 'en'):
                    with current_language.set(language):
                        with self.assertRaises(ToolExecutionError) as error:
                            tools['read'].execute({'path': str(bad)}, ctx)
                        self.assertEqual(error.exception.code, 'FS_ENCODING')
                        self.assertEqual(error.exception.message, tr('python.fs_tools.3e9082939296'))
                        with self.assertRaises(ToolExecutionError):
                            tools['write'].execute({'path': str(bad), 'content': 'replacement'}, ctx)
                        result = await tools['grep'].execute({'path': str(bad), 'pattern': 'Original'}, ctx)
                        self.assertEqual(result['matches'], [])
                        self.assertEqual(result['skipped_file_count'], 1)
                        self.assertEqual(result['skipped_files'], ['invalid.txt'])
                        self.assertIn(tr('fs.grep.summary.skipped', count=1, files='invalid.txt'), tools['grep'].to_content({}, result)[0].text)
                        self.assertEqual(bad.read_bytes(), original)
            valid = root / 'valid.txt'
            original = 'Original😀suffix'.encode()
            valid.write_bytes(original)
            for end in (9, 10, 11):
                text, _, partial = _read_snapshot(valid, end, require_complete=False)
                self.assertEqual((text, partial), ('Original', True))
                with patch('harness.tools.builtin.fs_grep_worker.READ_BYTES', end):
                    self.assertEqual(read_text(valid), ('Original', True))
            self.assertEqual(valid.read_bytes(), original)
