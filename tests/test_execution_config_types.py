"""执行配置拒绝字符串集合，保留原件并按语言报告错误。"""
import json
import tempfile
import unittest
from pathlib import Path
from harness.core.errors import ConfigError, HarnessError
from harness.core.language import current_language
from harness.execution.config import ProjectConfig, ExecutionConfig
from harness.execution.runtime import ExecutionRuntime


class ExecutionConfigTypeTests(unittest.TestCase):
    def test_direct_configuration_rejects_non_array_identity_and_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for language in ('en', 'zh-CN', 'en'):
                for values in ({'principals':'alice'}, {'principals':{'alice':True}},
                               {'verification':{'check':'echo'}}, {'verification':[]},
                               {'verification':{1:['echo']}}):
                    with self.subTest(language=language, values=values), current_language.set(language):
                        with self.assertRaises(ConfigError) as error:
                            ProjectConfig(root, **values)
                        self.assertEqual(any('\u4e00' <= c <= '\u9fff' for c in error.exception.message), language == 'zh-CN')

    def test_json_configuration_rejects_string_values_and_keeps_original(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / 'project'
            project.mkdir()
            config = root / 'config.json'
            for values in ({'principals':'alice'}, {'verification':{'check':'echo'}},
                           {'principals':None}, {'verification':None}):
                raw = json.dumps({'projects':{'fixture':{'root':'project',**values}}}).encode()
                config.write_bytes(raw)
                with self.subTest(values=values), self.assertRaises(ConfigError):
                    ExecutionConfig.load(config)
                self.assertEqual(config.read_bytes(), raw)

    def test_valid_identity_arrays_require_exact_principal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / 'project'
            project.mkdir()
            config = root / 'config.json'
            config.write_text(json.dumps({'home':'private','projects':{'fixture':{
                'root':'project','principals':['alice'],'verification':{'check':['echo','literal argument']}}}}))
            loaded = ExecutionConfig.load(config)
            self.assertEqual(loaded.projects['fixture'].principals, ('alice',))
            self.assertEqual(loaded.projects['fixture'].verification['check'], ('echo','literal argument'))
            runtime = object.__new__(ExecutionRuntime)
            runtime.config = loaded
            self.assertIs(runtime._project('fixture','alice'), loaded.projects['fixture'])
            for principal in ('ali','a','bob'):
                with self.assertRaises(HarnessError):
                    runtime._project('fixture',principal)

    def test_project_shape_errors_are_explicit_and_preserve_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'project').mkdir()
            config = root / 'config.json'
            for language in ('en', 'zh-CN'):
                for entry in ({}, {'root': None}, {'root': 1}, {'root': ''},
                              {'root': '   '}, {'root': 'project', 'legacy_root': 'other'}):
                    raw = json.dumps({'projects': {'fixture': entry}}).encode()
                    config.write_bytes(raw)
                    with self.subTest(language=language, entry=entry), current_language.set(language):
                        with self.assertRaises(ConfigError) as error:
                            ExecutionConfig.load(config)
                        self.assertEqual(any('\u4e00' <= c <= '\u9fff' for c in error.exception.message), language == 'zh-CN')
                        self.assertEqual(config.read_bytes(), raw)

    def test_direct_project_map_requires_dictionary_and_string_identifiers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for projects in ([], ['project'], {1: ProjectConfig(root)}):
                with self.subTest(projects=projects), self.assertRaises(ConfigError):
                    ExecutionConfig(root / 'state', projects)
