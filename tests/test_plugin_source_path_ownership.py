"""源码保存拒绝外部版本变化，清理不能沿被替换的父路径误删文件。"""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from harness.plugins.packages import PackageConflict, PackageError
from harness.plugins.sources import PackageSources


class PluginSourcePathOwnershipTests(unittest.TestCase):
    def setUp(self):
        root = tempfile.TemporaryDirectory();self.addCleanup(root.cleanup)
        self.root = Path(root.name).resolve()
        self.source = self.root/'source';self.source.mkdir()
        (self.source/'plugin.json').write_text('{"name":"fixture"}',encoding='utf-8')
        self.parent = self.source/'sub';self.parent.mkdir()
        self.target = self.parent/'notes.txt';self.target.write_text('Original 原文',encoding='utf-8')
        self.service = PackageSources(self.root/'managed')
        self.revision = self.service.snapshot(str(self.source),'sub/notes.txt')['revision']

    def test_external_edit_during_save_is_preserved_without_publishing(self):
        original = os.fsync
        def change(descriptor):
            original(descriptor)
            self.target.write_text('External 原文',encoding='utf-8')
        with patch('harness.plugins.sources.os.fsync',side_effect=change):
            with self.assertRaises(PackageConflict):
                self.service.save(str(self.source),'sub/notes.txt','New 原文',self.revision,False)
        self.assertEqual(self.target.read_text(encoding='utf-8'),'External 原文')
        self.assertEqual([path.name for path in self.parent.iterdir()],['notes.txt'])

    @unittest.skipUnless(os.open in os.supports_dir_fd and os.unlink in os.supports_dir_fd,
                         '父目录句柄竞态需要支持dir_fd的平台；Windows原生边界另行验收')
    def test_replaced_parent_cannot_redirect_temporary_cleanup_to_foreign_file(self):
        outside = self.root/'outside';outside.mkdir()
        foreign = outside/'notes.txt';foreign.write_bytes(b'Foreign original')
        moved = self.source/'original-sub'
        original = os.fsync
        names = []
        def swap(descriptor):
            original(descriptor)
            names.extend(path.name for path in self.parent.iterdir() if path.name.startswith('.plugin-source-'))
            self.parent.rename(moved)
            self.parent.symlink_to(outside,target_is_directory=True)
            for name in names:(outside/name).write_bytes(b'Foreign temporary original')
        with patch('harness.plugins.sources.os.fsync',side_effect=swap):
            with self.assertRaises(PackageError):
                self.service.save(str(self.source),'sub/notes.txt','New 原文',self.revision,False)
        self.assertEqual(foreign.read_bytes(),b'Foreign original')
        self.assertEqual((moved/'notes.txt').read_text(encoding='utf-8'),'Original 原文')
        self.assertEqual([path.name for path in moved.iterdir()],['notes.txt'])
        self.assertTrue(names)
        for name in names:self.assertEqual((outside/name).read_bytes(),b'Foreign temporary original')

    @unittest.skipUnless(os.open in os.supports_dir_fd and os.unlink in os.supports_dir_fd, 'requires directory handles')
    def test_remove_cannot_follow_parent_replacement_to_foreign_file(self):
        outside = self.root/'outside';outside.mkdir()
        foreign = outside/'notes.txt';foreign.write_bytes(b'Foreign original')
        moved = self.source/'original-sub'
        unlink = os.unlink
        replaced = False
        def replace_then_unlink(name, **kwargs):
            nonlocal replaced
            if name == 'notes.txt' and not replaced:
                replaced = True
                self.parent.rename(moved)
                self.parent.symlink_to(outside, target_is_directory=True)
                self.assertIsInstance(kwargs.get('dir_fd'), int)
            return unlink(name, **kwargs)
        # Mock保留supports_dir_fd成员身份，使用实现代理而非替换os.unlink本身。
        class OSProxy:
            def __getattr__(self, name):
                return replace_then_unlink if name == 'unlink' else getattr(os, name)
            supports_dir_fd = {*os.supports_dir_fd, replace_then_unlink}
        with patch('harness.plugins.sources.os', OSProxy()):
            self.service.remove(str(self.source), 'sub/notes.txt', self.revision)
        self.assertTrue(replaced)
        self.assertFalse((moved/'notes.txt').exists())
        self.assertEqual(foreign.read_bytes(), b'Foreign original')
