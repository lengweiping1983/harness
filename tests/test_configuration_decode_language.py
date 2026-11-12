"""设置和项目注册表的编码/JSON错误明确本地化，拒绝写回损坏原件。"""
import json
import tempfile
import unittest
from pathlib import Path

from harness.core.language import current_language, saved_language
from harness.i18n import tr
from harness.web.api.settings import SettingsStore
from harness.web.registry import WorkspaceRegistry


class ConfigurationDecodeLanguageTests(unittest.TestCase):
    def test_invalid_bytes_remain_unchanged_through_reads_and_writes(self):
        cases = [b'', b'{', b'\xff', b'\xef\xbb\xbf{}', '{"version":1}'.encode('utf-16')]
        for language in ('en', 'zh-CN', 'en'):
            for raw in cases:
                with self.subTest(language=language, raw=raw), tempfile.TemporaryDirectory() as directory, current_language.set(language):
                    root = Path(directory)
                    settings_path, registry_path = root / 'ui-settings.json', root / 'workspaces.json'
                    settings_path.write_bytes(raw)
                    registry_path.write_bytes(raw)
                    settings, registry = SettingsStore(root), WorkspaceRegistry(root, seed=str(root))
                    operations = [
                        (settings.snapshot, 'python.settings.format'),
                        (lambda: settings.update({'language': 'en'}), 'python.settings.format'),
                        (lambda: saved_language(root), 'language.invalid_settings_format'),
                        (registry.list, 'workspace.registry.current_format'),
                        (lambda: registry.register(str(root)), 'workspace.registry.current_format'),
                        (registry.ensure_seeded, 'workspace.registry.current_format'),
                        (registry.ensure_storage, 'workspace.registry.current_format'),
                    ]
                    for operation, key in operations:
                        with self.assertRaises(ValueError) as caught:
                            operation()
                        self.assertEqual(str(caught.exception), tr(key))
                        self.assertIsInstance(caught.exception.__cause__, (json.JSONDecodeError, UnicodeDecodeError))
                        self.assertEqual(settings_path.read_bytes(), raw)
                        self.assertEqual(registry_path.read_bytes(), raw)
                    self.assertEqual({path.name for path in root.iterdir()}, {'ui-settings.json', 'workspaces.json'})

    def test_missing_and_current_empty_files_keep_their_distinct_valid_behaviour(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertIsNone(saved_language(root))
            self.assertEqual(SettingsStore(root).snapshot(), {})
            self.assertEqual(WorkspaceRegistry(root).list(), [])
            self.assertEqual(list(root.iterdir()), [])
            (root / 'ui-settings.json').write_text('{"version":1,"settings":{}}', encoding='utf-8')
            (root / 'workspaces.json').write_text('{"version":1,"workspaces":[]}', encoding='utf-8')
            self.assertIsNone(saved_language(root))
            self.assertEqual(SettingsStore(root).snapshot(), {})
            self.assertEqual(WorkspaceRegistry(root).list(), [])
            settings = SettingsStore(root)
            settings.update({'language': 'en'})
            self.assertEqual(saved_language(root), 'en')
            registry = WorkspaceRegistry(root)
            entry = registry.register(str(root), name='Original 项目')
            self.assertEqual(WorkspaceRegistry(root).get(entry['id'])['name'], 'Original 项目')

    def test_explicit_repair_can_be_read_without_implicit_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'ui-settings.json'
            path.write_bytes(b'{')
            store = SettingsStore(root)
            with self.assertRaises(ValueError):
                store.snapshot()
            self.assertEqual(path.read_bytes(), b'{')
            # 模拟用户显式修复文件，不由读取路径自动生成或覆盖。
            path.write_text('{"version":1,"settings":{"language":"zh-CN"}}', encoding='utf-8')
            self.assertEqual(store.snapshot(), {'language': 'zh-CN'})
