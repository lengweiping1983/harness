"""当前设置及项目注册格式明确拒绝旧结构，错误按调用语言呈现且原件不变。"""
import json
import tempfile
import unittest
from pathlib import Path

from harness.core.errors import HarnessError
from harness.core.language import current_language
from harness.execution.config import ExecutionConfig
from harness.execution.workspaces import sync_projects
from harness.i18n import tr
from harness.web.api.settings import SettingsStore
from harness.web.registry import WorkspaceRegistry


class SettingsRegistryLanguageTests(unittest.TestCase):
    def test_invalid_persisted_settings_follow_language_and_preserve_original(self):
        cases = [([], 'format'), ({'version': True, 'settings': {}}, 'format'),
                 ({'version': 2, 'settings': {}}, 'format'),
                 ({'version': 1, 'settings': {'unknown': 1}}, 'unknown'),
                 ({'version': 1, 'settings': {'language': 'invalid'}}, 'invalid_field'),
                 ({'version': 1, 'settings': {'brave_web_api_key': 'bad key'}}, 'invalid_key')]
        for language in ('en', 'zh-CN'):
            with current_language.set(language):
                for payload, key in cases:
                    with self.subTest(language=language, payload=payload), tempfile.TemporaryDirectory() as directory:
                        path = Path(directory) / 'ui-settings.json'
                        raw = json.dumps(payload).encode()
                        path.write_bytes(raw)
                        store = SettingsStore(directory)
                        for operation in (store.snapshot, lambda: store.update({'language': 'en'})):
                            with self.assertRaises(ValueError) as error:
                                operation()
                            self.assertEqual(str(error.exception), tr('python.settings.' + key))
                            self.assertEqual(path.read_bytes(), raw)

    def test_current_settings_still_apply_valid_fields_and_reject_invalid_values(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(directory)
            snapshot, rejected = store.update({'language': 'en', 'brave_web_api_key': 'bad key', 'default_model': ' model '})
            self.assertEqual(rejected, ['brave_web_api_key'])
            self.assertEqual(snapshot, {'language': 'en', 'default_model': 'model'})
            self.assertEqual(SettingsStore(directory).snapshot(), snapshot)

    def test_executor_rejects_legacy_registry_before_writing_aliases(self):
        for language in ('en', 'zh-CN'):
            with current_language.set(language):
                for payload in ([], {'workspaces': []}, {'version': True, 'workspaces': []}, {'version': 2, 'workspaces': []}):
                    with self.subTest(language=language, payload=payload), tempfile.TemporaryDirectory() as directory:
                        root = Path(directory)
                        registry = root / 'workspaces.json'
                        raw = json.dumps(payload).encode()
                        registry.write_bytes(raw)
                        config = ExecutionConfig(root / 'private' / 'state', {}, workspace_registry=registry)
                        with self.assertRaises(HarnessError) as error:
                            sync_projects(config)
                        self.assertEqual(error.exception.code, 'execution/project-registry-unavailable')
                        self.assertIn(tr('python.workspace_sync.format'), error.exception.message)
                        self.assertEqual(registry.read_bytes(), raw)
                        self.assertFalse(config.home.exists())

    def test_current_registry_writer_syncs_and_removal_revokes_projects(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / 'project'
            project.mkdir()
            registry = WorkspaceRegistry(root / 'registry')
            row = registry.register(str(project), name='Fixture')
            config = ExecutionConfig(root / 'private' / 'state', {}, workspace_registry=root / 'registry' / 'workspaces.json')
            first = sync_projects(config)
            self.assertEqual(len(first.projects), 1)
            identifier, bound = next(iter(first.projects.items()))
            self.assertEqual(bound.root, project.resolve())
            self.assertEqual(next(iter(sync_projects(first).projects)), identifier)
            registry.unregister(row['id'])
            self.assertEqual(sync_projects(first).projects, {})
