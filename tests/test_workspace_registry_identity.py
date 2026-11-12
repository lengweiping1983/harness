"""项目注册的持久身份、实时存在性和嵌套快照保持一致。"""
import json
import tempfile
import unittest
from pathlib import Path

from harness.core.language import current_language
from harness.i18n import tr
from harness.web.api.settings import SettingsStore
from harness.web.registry import WorkspaceRegistry, REGISTRY_FILENAME


class WorkspaceRegistryIdentityTests(unittest.TestCase):
    def test_wrong_persisted_identity_is_rejected_without_rewriting(self):
        for language in ('en', 'zh-CN', 'en'):
            with tempfile.TemporaryDirectory() as directory, current_language.set(language):
                root = Path(directory).resolve()
                home, project = root / 'home', root / 'project'
                project.mkdir()
                registry = WorkspaceRegistry(home)
                registry.register(str(project))
                path = home / REGISTRY_FILENAME
                data = json.loads(path.read_text(encoding='utf-8'))
                data['workspaces'][0]['id'] = str(root / 'other')
                raw = json.dumps(data).encode()
                path.write_bytes(raw)
                with self.assertRaises(ValueError) as error:
                    WorkspaceRegistry(home).list()
                self.assertEqual(str(error.exception), tr('workspace.registry.current_format'))
                self.assertEqual(path.read_bytes(), raw)

    def test_cached_exists_is_live_and_nested_return_values_are_isolated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            home, project = root / 'home', root / 'project'
            project.mkdir()
            registry = WorkspaceRegistry(home)
            row = registry.register(str(project))
            path = home / REGISTRY_FILENAME
            data = json.loads(path.read_text(encoding='utf-8'))
            data['workspaces'][0]['metadata'] = {'original': ['原文']}
            path.write_text(json.dumps(data), encoding='utf-8')
            original = path.read_bytes()
            registry = WorkspaceRegistry(home)
            listed = registry.list()
            listed[0]['metadata']['original'].append('changed')
            self.assertEqual(registry.get(row['id'])['metadata'], {'original': ['原文']})
            project.rmdir()
            self.assertFalse(registry.list()[0]['exists'])
            self.assertFalse(registry.get(row['id'])['exists'])
            project.mkdir()
            self.assertTrue(registry.get(row['id'])['exists'])
            self.assertEqual(path.read_bytes(), original)

    def test_settings_corrupt_json_and_encoding_are_localized_without_rewrite(self):
        for language in ('en', 'zh-CN', 'en'):
            for raw in (b'{', b'\xff'):
                with tempfile.TemporaryDirectory() as directory, current_language.set(language):
                    path = Path(directory) / 'ui-settings.json'
                    path.write_bytes(raw)
                    store = SettingsStore(directory)
                    with self.assertRaises(ValueError) as error:
                        store.snapshot()
                    self.assertEqual(str(error.exception), tr('python.settings.format'))
                    self.assertEqual(path.read_bytes(), raw)
