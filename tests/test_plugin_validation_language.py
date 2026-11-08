"""插件拒绝路径双语化，保持来源、版本冲突与原件边界。"""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from harness.core.language import current_language
from harness.i18n import tr
from harness.plugins.packages import read_package, PackageError, PackageConflict, _inside, _snapshot
from harness.plugins.sources import PackageSources
from harness.plugins.git_sources import validate_source
from harness.plugins.marketplaces import MarketplaceStore
from harness.web.api.plugin_packages import build_router
from harness.web.errors import ApiError
from harness.web.router import Request
from harness.web.error_messages import public_error_message


class PluginValidationLanguageTests(unittest.TestCase):
    def test_plugin_io_error_hides_paths_preserves_diagnostics_and_uses_shared_resources(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / 'missing-plugin'
            service = SimpleNamespace(preview=lambda path: lambda: _snapshot(Path(path)))
            for language in ('en', 'zh-CN', 'en'):
                for failure in ('missing', 'permission'):
                    with self.subTest(language=language, failure=failure), current_language.set(language):
                        def call(operation, **_options):
                            if failure == 'permission':
                                raise PermissionError(13, 'Original permission detail', str(missing))
                            return operation()
                        ctx = SimpleNamespace(harness=SimpleNamespace(plugin_service=service), call=call)
                        handler, parameters = build_router(ctx).resolve('POST', '/api/v1/plugin-packages/preview')
                        body = json.dumps({'path': str(missing)}).encode()
                        with self.assertLogs('harness.web.api.plugin_packages', level='WARNING') as logs:
                            with self.assertRaises(ApiError) as caught:
                                handler(Request('POST', '/api/v1/plugin-packages/preview', {}, {}, body), **parameters)
                        error = caught.exception
                        copy = {'key': 'ui.collaboration.error.plugin-package/io-error'}
                        self.assertEqual(error.status, 400)
                        self.assertEqual(error.code, 'plugin-package/io-error')
                        self.assertEqual(error.message, tr(copy['key']))
                        self.assertEqual(error.details, {'copy': copy, 'errno': 2 if failure == 'missing' else 13})
                        payload = json.dumps(error.to_dict(), ensure_ascii=False)
                        self.assertNotIn(str(missing), payload)
                        self.assertNotIn('[Errno', payload)
                        self.assertIn(str(missing), '\n'.join(logs.output))
                        self.assertIsInstance(error.__cause__, OSError)
                        self.assertEqual(public_error_message(error.code, error.code), error.message)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_api_plugin_errors_preserve_language_conflict_details_and_clean_message(self):
        for language in ('en', 'zh-CN', 'en'):
            for conflict in (False, True):
                with self.subTest(language=language, conflict=conflict), current_language.set(language):
                    message = tr('plugin.validation.cded299936e1')
                    error = (PackageConflict if conflict else PackageError)(message)
                    error.details = {'diagnostic': 'Original 原文'}
                    def call(_coroutine, **_options):
                        raise error
                    ctx = SimpleNamespace(harness=SimpleNamespace(plugin_service=SimpleNamespace(preview=lambda path: path)), call=call)
                    handler, parameters = build_router(ctx).resolve('POST', '/api/v1/plugin-packages/preview')
                    with self.assertRaises(ApiError) as caught:
                        handler(Request('POST', '/api/v1/plugin-packages/preview', {}, {}, b'{"path":"/fixture"}'), **parameters)
                    self.assertEqual(caught.exception.message, message)
                    self.assertEqual(caught.exception.details, error.details)
                    self.assertEqual(caught.exception.code, 'plugin-package/changed' if conflict else 'plugin-package/invalid')
                    self.assertEqual(caught.exception.status, 409 if conflict else 400)

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

    def test_mcp_server_timeouts_reject_integers_outside_float_range(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / 'plugin.json'
            declaration = root / 'mcp.json'
            manifest_bytes = json.dumps({'name': 'fixture', 'mcpServers': 'mcp.json'}).encode()
            for key in ('startup_timeout_sec', 'tool_timeout_sec'):
                declaration_bytes = json.dumps({'mcpServers': {'fixture': {'command': 'fixture', key: 10 ** 400}}}).encode()
                manifest.write_bytes(manifest_bytes)
                declaration.write_bytes(declaration_bytes)
                for language in ('en', 'zh-CN', 'en'):
                    with self.subTest(key=key, language=language), current_language.set(language), self.assertRaises(PackageError) as caught:
                        read_package(root)
                    self.assert_language(caught.exception.message, language)
                    self.assertEqual(caught.exception.code, 'plugin-package/invalid')
                    self.assertEqual(manifest.read_bytes(), manifest_bytes)
                    self.assertEqual(declaration.read_bytes(), declaration_bytes)

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
