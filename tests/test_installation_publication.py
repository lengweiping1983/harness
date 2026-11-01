"""安装只发布完整且复验成功的目录，竞争不能卸载正在复制的对象。"""
import shutil
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from harness.plugins.installer import install_from_directory, uninstall
from harness.skills.paths import installation_root
from tests.filesystem_links import directory_link


class InstallationPublicationTests(unittest.TestCase):
    def source(self, root):
        source = root/'sample-skill';source.mkdir()
        (source/'SKILL.md').write_text('---\nname: sample-skill\ndescription: Original\n---\nOriginal instructions', encoding='utf-8')
        (source/'data.txt').write_bytes(b'Original data')
        return source

    def test_partial_copy_failure_does_not_publish_or_leave_staging_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve();source = self.source(root)
            target_root = installation_root('skill', 'user', home=root/'runtime')
            original = OSError('Original copy failure')
            def fail(src, dst):
                dst.mkdir();(dst/'SKILL.md').write_bytes((src/'SKILL.md').read_bytes())
                raise original
            with patch('harness.plugins.installer.shutil.copytree', side_effect=fail):
                with self.assertRaises(OSError) as caught:
                    install_from_directory(source, kind='skill', home=root/'runtime')
            self.assertIs(caught.exception, original)
            self.assertFalse((target_root/source.name).exists())
            self.assertEqual(list(target_root.parent.glob('.install-*')), [])
            self.assertEqual((source/'data.txt').read_bytes(), b'Original data')
            installed = install_from_directory(source, kind='skill', home=root/'runtime')
            self.assertEqual((installed/'data.txt').read_bytes(), b'Original data')

    def test_copy_is_revalidated_before_publication(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve();source = self.source(root)
            copy = shutil.copytree
            def corrupt(src, dst):
                copy(src, dst)
                (dst/'SKILL.md').write_text('---\nname: other-skill\ndescription: Original\n---\nOriginal', encoding='utf-8')
            with patch('harness.plugins.installer.shutil.copytree', side_effect=corrupt):
                from harness.core.errors import ValidationError
                with self.assertRaises(ValidationError):
                    install_from_directory(source, kind='skill', home=root/'runtime')
            target_root = installation_root('skill', 'user', home=root/'runtime')
            self.assertFalse((target_root/source.name).exists())
            self.assertEqual(list(target_root.parent.glob('.install-*')), [])

    def test_competing_install_and_uninstall_cannot_modify_inflight_copy(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve();source = self.source(root)
            entered, release = threading.Event(), threading.Event()
            copy = shutil.copytree
            errors = []
            def held(src, dst):
                entered.set()
                if not release.wait(3):
                    raise TimeoutError('fixture publication barrier')
                return copy(src, dst)
            def install():
                try:
                    install_from_directory(source, kind='skill', home=root/'runtime')
                except BaseException as error:
                    errors.append(error)
            worker = threading.Thread(target=install)
            with patch('harness.plugins.installer.shutil.copytree', side_effect=held):
                worker.start()
                try:
                    self.assertTrue(entered.wait(3))
                    for operation in (lambda: install_from_directory(source, kind='skill', home=root/'runtime'),
                                      lambda: uninstall(source.name, kind='skill', home=root/'runtime')):
                        with self.assertRaises(OSError):
                            operation()
                    self.assertFalse((installation_root('skill', 'user', home=root/'runtime')/source.name).exists())
                finally:
                    release.set();worker.join(3)
            self.assertFalse(worker.is_alive());self.assertEqual(errors, [])
            uninstall(source.name, kind='skill', home=root/'runtime')
            self.assertFalse((installation_root('skill', 'user', home=root/'runtime')/source.name).exists())

    def test_source_containing_installation_root_is_rejected_before_staging(self):
        from harness.core.errors import ValidationError
        from harness.core.language import current_language
        from harness.i18n import tr
        for language in ('en', 'zh-CN', 'en'):
            with tempfile.TemporaryDirectory() as folder, current_language.set(language):
                root = Path(folder).resolve()
                source = root/'.agents'/'skills';source.mkdir(parents=True)
                manifest = source/'SKILL.md'
                manifest.write_text('---\nname: skills\ndescription: Original\n---\nOriginal', encoding='utf-8')
                original = manifest.read_bytes()
                with self.assertRaises(ValidationError) as caught:
                    install_from_directory(source, kind='skill', home=root/'.harness')
                self.assertEqual(caught.exception.message, tr('plugin.installation.recursiveSource'))
                self.assertEqual(list(source.iterdir()), [manifest])
                self.assertEqual(manifest.read_bytes(), original)

    @unittest.skipUnless(os.open in os.supports_dir_fd, 'requires directory handles')
    def test_uninstall_root_replacement_does_not_delete_external_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve();source = self.source(root)
            target = install_from_directory(source, kind='skill', home=root/'runtime')
            original_root = target.parent
            moved = original_root.with_name('moved-skills')
            outside = root/'outside';outside.mkdir()
            other = outside/source.name;other.mkdir()
            (other/'private.txt').write_bytes(b'Unrelated original')
            remove = shutil.rmtree
            replaced = False
            def replace_then_remove(path, *args, **kwargs):
                nonlocal replaced
                if not replaced:
                    replaced = True
                    original_root.rename(moved)
                    original_root.symlink_to(outside, target_is_directory=True)
                    self.assertIsInstance(kwargs.get('dir_fd'), int)
                return remove(path, *args, **kwargs)
            with patch('harness.plugins.installer.shutil.rmtree', side_effect=replace_then_remove):
                uninstall(source.name, kind='skill', home=root/'runtime')
            self.assertTrue(replaced)
            self.assertFalse((moved/source.name).exists())
            self.assertEqual((other/'private.txt').read_bytes(), b'Unrelated original')

    def test_install_root_replaced_during_copy_preserves_external_files_and_cleans_own_staging(self):
        from harness.core.errors import ValidationError
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve();source = self.source(root)
            target_root = installation_root('skill', 'user', home=root/'runtime')
            target_root.mkdir(parents=True)
            moved = target_root.with_name('original-skills')
            outside = root/'outside';outside.mkdir()
            (outside/'private.txt').write_bytes(b'External original')
            copy = shutil.copytree
            def replace_then_copy(src, dst):
                target_root.rename(moved)
                directory_link(target_root, outside)
                return copy(src, dst)
            with patch('harness.plugins.installer.shutil.copytree', side_effect=replace_then_copy):
                if os.name == 'nt':
                    # Windows禁止改名包含本次已打开锁文件的目录，拒绝发生在替换前。
                    with self.assertRaises(PermissionError) as caught:
                        install_from_directory(source, kind='skill', home=root/'runtime')
                    self.assertEqual(caught.exception.filename, str(target_root))
                    self.assertFalse(moved.exists())
                else:
                    with self.assertRaises(ValidationError):
                        install_from_directory(source, kind='skill', home=root/'runtime')
            self.assertEqual((outside/'private.txt').read_bytes(), b'External original')
            self.assertEqual([item.name for item in outside.iterdir()], ['private.txt'])
            self.assertFalse((moved/source.name).exists())
            self.assertEqual(list(target_root.parent.glob('.install-*')), [])

    @unittest.skipUnless(os.open in os.supports_dir_fd and os.rename in os.supports_dir_fd, 'requires directory handles')
    def test_root_replaced_at_publication_does_not_redirect_accepted_directory(self):
        from harness.plugins import installer
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve();source = self.source(root)
            target_root = installation_root('skill', 'user', home=root/'runtime')
            target_root.mkdir(parents=True)
            moved = target_root.with_name('original-skills')
            outside = root/'outside';outside.mkdir()
            (outside/'private.txt').write_bytes(b'External original')
            rename = os.rename
            def replace_then_rename(src, dst, **kwargs):
                target_root.rename(moved)
                target_root.symlink_to(outside, target_is_directory=True)
                self.assertIsInstance(kwargs.get('dst_dir_fd'), int)
                return rename(src, dst, **kwargs)
            class OSProxy:
                supports_dir_fd = {*os.supports_dir_fd, replace_then_rename}
                def __getattr__(self, name):
                    return replace_then_rename if name == 'rename' else getattr(os, name)
            with patch.object(installer, 'os', OSProxy()):
                with self.assertRaises(OSError):
                    install_from_directory(source, kind='skill', home=root/'runtime')
            self.assertEqual((outside/'private.txt').read_bytes(), b'External original')
            self.assertEqual([item.name for item in outside.iterdir()], ['private.txt'])
            self.assertEqual((moved/source.name/'data.txt').read_bytes(), b'Original data')
            self.assertEqual(list(target_root.parent.glob('.install-*')), [])
