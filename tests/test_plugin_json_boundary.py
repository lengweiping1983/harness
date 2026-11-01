"""插件JSON有界读取及双语解码错误不改写源文件。"""
import tempfile
import os
import unittest
from pathlib import Path
from unittest.mock import patch
from harness.core.language import current_language
from harness.i18n import tr
from harness.plugins.packages import _json, PackageError


class PluginJsonBoundaryTests(unittest.TestCase):
    def test_invalid_encoding_and_syntax_preserve_original(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'Original 文件.json'
            for language in ('en', 'zh-CN', 'en'):
                for raw in (b'{', b'\xff', b'', b'\xef\xbb\xbf{}', '{}'.encode('utf-16')):
                    with self.subTest(language=language, raw=raw), current_language.set(language):
                        path.write_bytes(raw)
                        with self.assertRaises(PackageError) as caught:
                            _json(path)
                        self.assertEqual(caught.exception.message, tr('plugin.validation.invalid_json', path=path))
                        self.assertEqual(path.read_bytes(), raw)

    def test_read_has_explicit_bound_independent_of_path_stat(self):
        sizes = []
        fdopen = os.fdopen
        class Stream:
            def __init__(self, descriptor, mode):
                self.stream = fdopen(descriptor, mode)
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.stream.close()
            def fileno(self):
                return self.stream.fileno()
            def read(self, size):
                sizes.append(size)
                return self.stream.read(size)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'plugin.json'
            path.write_bytes(b' ' * (1024 * 1024 + 2))
            with patch('harness.plugins.packages.os.fdopen', Stream), patch.object(Path, 'stat', side_effect=AssertionError('Must not trust path stat')):
                with self.assertRaises(PackageError) as caught:
                    _json(path)
        self.assertEqual(sizes, [1024 * 1024 + 1])
        self.assertEqual(caught.exception.message, tr('plugin.validation.b9486896d1d2'))

    def test_current_object_and_exact_limit_are_valid_but_nonobject_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fixture.json'
            raw = b'{}' + b' ' * (1024 * 1024 - 2)
            path.write_bytes(raw)
            self.assertEqual(_json(path), {})
            self.assertEqual(path.read_bytes(), raw)
            path.write_bytes(b'[]')
            with self.assertRaises(PackageError) as caught:
                _json(path)
            self.assertEqual(caught.exception.message, tr('plugin.validation.ab1ccbe65f15'))

    def test_duplicate_fields_nonfinite_values_and_invalid_unicode_preserve_bytes(self):
        invalid = [b'{"name":"first","name":"second"}', b'{"nested":{"id":1,"id":2}}',
                   b'{"value":NaN}', b'{"value":Infinity}', b'{"value":1e999}',
                   b'{"value":"\\ud800"}', b'{"value":'+b'['*1500+b'0'+b']'*1500+b'}']
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'plugin.json'
            for language in ('en', 'zh-CN', 'en'):
                for raw in invalid:
                    with self.subTest(language=language, size=len(raw)), current_language.set(language):
                        path.write_bytes(raw)
                        with self.assertRaises(PackageError) as caught:
                            _json(path)
                        self.assertEqual(caught.exception.message, tr('plugin.validation.invalid_json', path=path))
                        self.assertEqual(path.read_bytes(), raw)

    def test_reading_changed_file_rejects_result_and_preserves_new_original(self):
        fdopen = os.fdopen
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'plugin.json'
            path.write_bytes(b'{"name":"original"}')
            class ChangingStream:
                def __init__(self, descriptor, mode):
                    self.stream = fdopen(descriptor, mode)
                def __enter__(self):
                    return self
                def __exit__(self, *args):
                    self.stream.close()
                def fileno(self):
                    return self.stream.fileno()
                def read(self, size):
                    raw = self.stream.read(size)
                    path.write_bytes(b'{"name":"changed-original"}')
                    return raw
            with patch('harness.plugins.packages.os.fdopen', ChangingStream):
                with self.assertRaises(PackageError):
                    _json(path)
            self.assertEqual(path.read_bytes(), b'{"name":"changed-original"}')

    @unittest.skipUnless(os.open in os.supports_dir_fd, 'requires directory handles')
    def test_parent_link_replacement_after_validation_cannot_read_external_json(self):
        from harness.plugins.packages import _inside
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()/'source';root.mkdir()
            parent = root/'config';parent.mkdir()
            (parent/'apps.json').write_bytes(b'{"original":true}')
            target = _inside(root, 'config/apps.json')
            outside = root.parent/'outside';outside.mkdir()
            foreign = outside/'apps.json';foreign.write_bytes(b'{"private":true}')
            parent.rename(root/'original-config')
            parent.symlink_to(outside, target_is_directory=True)
            with self.assertRaises(PackageError):
                _json(target, root=root)
            self.assertEqual(foreign.read_bytes(), b'{"private":true}')

    @unittest.skipUnless(os.open in os.supports_dir_fd, 'requires directory handles')
    def test_root_replaced_between_stat_and_open_is_rejected(self):
        from harness.plugins import packages
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()/'source';root.mkdir()
            path = root/'plugin.json';path.write_bytes(b'{"original":true}')
            moved = root.with_name('original-source')
            opening = os.open
            replaced = False
            def replace_then_open(target, flags, *args, **kwargs):
                nonlocal replaced
                if target == root and not replaced:
                    replaced = True
                    root.rename(moved)
                    root.mkdir()
                    (root/'plugin.json').write_bytes(b'{"foreign":true}')
                return opening(target, flags, *args, **kwargs)
            class OSProxy:
                supports_dir_fd = {*os.supports_dir_fd, replace_then_open}
                def __getattr__(self, name):
                    return replace_then_open if name == 'open' else getattr(os, name)
            with patch.object(packages, 'os', OSProxy()):
                with self.assertRaises(PackageError):
                    _json(path, root=root)
            self.assertTrue(replaced)
            self.assertEqual((moved/'plugin.json').read_bytes(), b'{"original":true}')
            self.assertEqual(path.read_bytes(), b'{"foreign":true}')
