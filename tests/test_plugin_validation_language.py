"""插件拒绝路径双语化，保持来源、版本冲突与原件边界。"""
import json
import tempfile
import unittest
from pathlib import Path
from harness.core.language import current_language
from harness.plugins.packages import read_package, PackageError, PackageConflict, _inside
from harness.plugins.sources import PackageSources
from harness.plugins.git_sources import validate_source
from harness.plugins.marketplaces import MarketplaceStore


class PluginValidationLanguageTests(unittest.TestCase):
    def assert_language(self, text, language):
        self.assertEqual(any('\u4e00' <= c <= '\u9fff' for c in text), language == 'zh-CN', text)

    def test_manifest_rejections_preserve_original_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / 'plugin.json'
            cases = [({'name': 'INVALID'}, 'plugin-package/invalid'),
                ({'name': 'fixture', 'version': ''}, 'plugin-package/invalid'),
                ({'name': 'fixture', 'agents': []}, 'plugin-package/invalid'),
                ({'name': 'fixture', 'extensions': []}, 'plugin-package/invalid'),
                ({'name': 'fixture', 'appTemplates': ['fixture']}, 'plugin-package/invalid'),
                ({'name': 'fixture', 'mcpServers': {'fixture': {'command': ''}}}, 'plugin-package/invalid')]
            for language in ('en', 'zh-CN', 'en'):
                for document, code in cases:
                    original = json.dumps(document).encode()
                    manifest.write_bytes(original)
                    with current_language.set(language), self.assertRaises(PackageError) as caught:
                        read_package(root)
                    self.assert_language(caught.exception.message, language)
                    self.assertEqual(caught.exception.code, code)
                    self.assertEqual(manifest.read_bytes(), original)
                    self.assertEqual(list(root.iterdir()), [manifest])

    def test_path_parameters_and_git_rejections_do_not_access_network(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for language in ('en', 'zh-CN', 'en'):
                with current_language.set(language), self.assertRaises(PackageError) as caught:
                    _inside(root, '../original.txt')
                self.assert_language(caught.exception.message, language)
                self.assertIn('../original.txt', caught.exception.message)
                for source in ({'source': 'url', 'url': 'http://fixture.invalid'}, {'source': 'url', 'url': 'https://fixture.invalid/repo', 'ref': '-bad'}):
                    with current_language.set(language), self.assertRaises(PackageError) as caught:
                        validate_source(source)
                    self.assert_language(caught.exception.message, language)
            self.assertEqual(list(root.iterdir()), [])

    def test_source_conflict_preserves_file_and_current_success_still_saves(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            source.mkdir()
            (source / 'plugin.json').write_text('{"name":"fixture"}')
            target = source / 'notes.txt'
            target.write_text('original')
            service = PackageSources(root / 'managed')
            for language in ('en', 'zh-CN', 'en'):
                with current_language.set(language), self.assertRaises(PackageConflict) as caught:
                    service.save(str(source), 'notes.txt', 'new', 'stale', False)
                self.assert_language(caught.exception.message, language)
                self.assertEqual(target.read_text(), 'original')
            snapshot = service.snapshot(str(source), 'notes.txt')
            service.save(str(source), 'notes.txt', 'new', snapshot['revision'], False)
            self.assertEqual(target.read_text(), 'new')

    def test_marketplace_errors_recompute_language_without_mutating_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / '.agents/plugins/marketplace.json'
            path.parent.mkdir(parents=True)
            original = json.dumps({'name': 'fixture', 'plugins': [{'name': 'item', 'source': {'source': 'unimplemented'}}]}).encode()
            path.write_bytes(original)
            store = MarketplaceStore(root / 'state', root)
            for language in ('en', 'zh-CN', 'en'):
                with current_language.set(language):
                    row = store.read(str(root))['plugins'][0]
                self.assert_language(row['error'], language)
                self.assertIn('unimplemented', row['error'])
                self.assertEqual(path.read_bytes(), original)
                self.assertFalse((root / 'state').exists())
