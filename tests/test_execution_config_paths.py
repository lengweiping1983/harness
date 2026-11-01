"""执行器当前路径字段拒绝类型兜底，保留相对路径及显式空注册表语义。"""
import json
import tempfile
import unittest
from pathlib import Path

from harness.core.errors import ConfigError
from harness.core.language import current_language
from harness.execution.config import ExecutionConfig, ProjectConfig
from harness.i18n import tr


class ExecutionConfigPathTests(unittest.TestCase):
    def test_file_paths_reject_invalid_values_and_preserve_original(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            project = root / 'project'
            project.mkdir()
            path = root / 'config.json'
            for language in ('en', 'zh-CN', 'en'):
                for field in ('home', 'workspace_registry'):
                    invalids = ['', '   ', '\0', 0, False, [], {}] + ([None] if field == 'home' else [])
                    for value in invalids:
                        with self.subTest(language=language, field=field, value=value), current_language.set(language):
                            raw = json.dumps({'home': 'private', 'projects': {'fixture': {'root': 'project'}}, field: value}).encode()
                            path.write_bytes(raw)
                            with self.assertRaises(ConfigError) as caught:
                                ExecutionConfig.load(path)
                            self.assertEqual(caught.exception.message, tr('execution.config.' + field))
                            self.assertEqual(path.read_bytes(), raw)
                            self.assertFalse((root / 'private').exists())

    def test_direct_construction_keeps_pathlike_support_and_localized_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            projects = {'fixture': ProjectConfig(root / '.')}
            for language in ('en', 'zh-CN', 'en'):
                for field, values in [('home', [None, 0, False, '', ' ', '\0', [], {}]), ('workspace_registry', [0, False, '', ' ', '\0', [], {}])]:
                    for value in values:
                        with self.subTest(language=language, field=field, value=value), current_language.set(language):
                            with self.assertRaises(ConfigError) as caught:
                                ExecutionConfig(**{'home': root / 'private', 'projects': projects, field: value})
                            self.assertEqual(caught.exception.message, tr('execution.config.' + field))
                for value in [None, 0, False, '', '\0', [], {}]:
                    with self.subTest(root=value), current_language.set(language):
                        with self.assertRaises(ConfigError) as caught:
                            ProjectConfig(value)
                        self.assertEqual(caught.exception.message, tr('execution.config.root'))
            config = ExecutionConfig(root / 'private', projects, workspace_registry=root / 'registry.json')
            self.assertEqual(config.home, root / 'private')
            self.assertEqual(config.workspace_registry, root / 'registry.json')
            self.assertFalse(config.home.exists())

    def test_relative_paths_defaults_and_explicit_null_keep_current_meanings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / 'project').mkdir()
            base = {'projects': {'fixture': {'root': 'project'}}}
            default = ExecutionConfig.from_dict(base, base=root)
            self.assertEqual(default.home, root / '.harness-executor')
            self.assertIsNone(default.workspace_registry)
            explicit = ExecutionConfig.from_dict({**base, 'home': 'private', 'workspace_registry': None}, base=root)
            self.assertEqual(explicit.home, root / 'private')
            self.assertIsNone(explicit.workspace_registry)
            registry = ExecutionConfig.from_dict({**base, 'home': 'private', 'workspace_registry': 'registry.json'}, base=root)
            self.assertEqual(registry.workspace_registry, root / 'registry.json')
            self.assertEqual(registry.projects['fixture'].root, root / 'project')
            self.assertEqual([entry.name for entry in root.iterdir()], ['project'])

    def test_missing_and_unreadable_project_paths_keep_localized_error_and_original_cause(self):
        from unittest.mock import patch
        for language in ('en', 'zh-CN', 'en'):
            with tempfile.TemporaryDirectory() as directory, current_language.set(language):
                root = Path(directory)
                with self.assertRaises(ConfigError) as caught:
                    ProjectConfig(root/'missing')
                self.assertEqual(caught.exception.message, tr('execution.config.root'))
                self.assertIsInstance(caught.exception.__cause__, FileNotFoundError)
                original = PermissionError('Original unreadable project')
                with patch.object(Path, 'resolve', side_effect=original):
                    with self.assertRaises(ConfigError) as caught:
                        ProjectConfig(root)
                self.assertEqual(caught.exception.message, tr('execution.config.root'))
                self.assertIs(caught.exception.__cause__, original)
                self.assertEqual(list(root.iterdir()), [])
