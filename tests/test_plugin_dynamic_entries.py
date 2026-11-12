"""当前清单布局和源码创建入口保持明确身份及无覆盖语义。"""
import json
import tempfile
import unittest
from pathlib import Path

from harness.plugins.packages import read_package, PackageConflict, PackageError
from harness.plugins.sources import PackageSources


class PluginDynamicEntryTests(unittest.TestCase):
    def test_both_manifest_locations_remain_current_and_root_identity_wins(self):
        for layout in ('portable', 'overlay', 'both'):
            with self.subTest(layout=layout), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                if layout != 'overlay':
                    (root/'plugin.json').write_text(json.dumps({'name':'fixture','version':'1.0.0'}), encoding='utf-8')
                if layout != 'portable':
                    (root/'.codex-plugin').mkdir()
                    (root/'.codex-plugin/plugin.json').write_text(json.dumps({'name':'fixture' if layout=='overlay' else 'overlay-name','version':'1.0.0'}), encoding='utf-8')
                skill = root/'skills'/'sample';skill.mkdir(parents=True)
                (skill/'SKILL.md').write_text('---\nname: sample\ndescription: Original\n---\nOriginal', encoding='utf-8')
                original = {str(path.relative_to(root)):path.read_bytes() for path in root.rglob('*') if path.is_file()}
                self.assertEqual(read_package(root).name, 'fixture')
                source = PackageSources(root.parent/'fixture-managed')
                self.assertEqual(source.root(str(root)), root)
                self.assertEqual({str(path.relative_to(root)):path.read_bytes() for path in root.rglob('*') if path.is_file()}, original)

    def test_new_source_requires_missing_revision_and_cannot_overwrite_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root/'plugin.json').write_text('{"name":"fixture"}', encoding='utf-8')
            service = PackageSources(root.parent/'fixture-managed')
            created = service.save(str(root), 'notes.txt', 'Original 原文', 'missing', False)
            self.assertEqual(created['content'], 'Original 原文')
            with self.assertRaises(PackageConflict):
                service.save(str(root), 'notes.txt', 'Replacement', 'missing', False)
            self.assertEqual((root/'notes.txt').read_text(encoding='utf-8'), 'Original 原文')
            for name in ('../outside.txt', '/outside.txt'):
                with self.assertRaises(PackageError):
                    service.target(root, name, missing=True)
            for manifest in ('plugin.json',):
                with self.assertRaises(PackageError):
                    service.remove(str(root), manifest, service.snapshot(str(root), manifest)['revision'])
