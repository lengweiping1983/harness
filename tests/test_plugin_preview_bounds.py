"""插件预览增量限额与审核摘要稳定性。"""
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from harness.core.language import current_language
from harness.i18n import tr
from harness.plugins.packages import PackageError, _snapshot


class PluginPreviewBoundsTests(unittest.TestCase):
    def test_snapshot_stops_enumerating_at_the_file_or_directory_limit(self):
        for directories in (False, True):
            with self.subTest(directories=directories), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                for index in range(20):
                    target = root / str(index)
                    if directories:
                        target.mkdir()
                    else:
                        target.write_text('原文', encoding='utf-8')
                original = os.scandir
                examined = []
                closed = []

                class Entries:
                    def __init__(self, path):
                        self.entries = original(path)

                    def __enter__(self):
                        return self

                    def __exit__(self, *args):
                        self.entries.close()
                        closed.append(True)

                    def __iter__(self):
                        for entry in self.entries:
                            examined.append(entry.name)
                            yield entry

                with patch('harness.plugins.packages.MAX_FILES', 4), patch('harness.plugins.packages.os.scandir', Entries):
                    for language in ('en', 'zh-CN', 'en'):
                        examined.clear()
                        closed.clear()
                        with current_language.set(language), self.assertRaises(PackageError) as caught:
                            _snapshot(root)
                        with current_language.set(language):
                            expected = tr('plugin.validation.directoryLimit', limit=4) if directories else tr('plugin.validation.d1de9c1057bd', p0=4)
                        self.assertEqual(caught.exception.message, expected)
                        self.assertEqual(len(examined), 5)
                        self.assertEqual(closed, [True])
                self.assertEqual(len(list(root.iterdir())), 20)

    def test_snapshot_preserves_the_existing_digest_order_and_exact_copy_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'source'
            root.mkdir()
            content = {'a/z.txt': b'original\r\n', 'a.txt': '原文'.encode(), 'b/y.txt': b'last'}
            for name, data in content.items():
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            expected = hashlib.sha256()
            for path in sorted(root.rglob('*')):
                if path.is_dir():
                    continue
                data = path.read_bytes()
                expected.update(path.relative_to(root).as_posix().encode())
                expected.update(b'\0' + str(len(data)).encode() + b'\0' + data)
            copy = Path(folder) / 'copy'
            self.assertEqual(_snapshot(root, copy), expected.hexdigest())
            self.assertEqual({path.relative_to(copy).as_posix(): path.read_bytes() for path in copy.rglob('*') if path.is_file()}, content)
