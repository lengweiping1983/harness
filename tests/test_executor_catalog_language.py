"""独立执行器目录与路径拒绝提示按实际读取语言解析。"""
import tempfile
from copy import deepcopy
import unittest
from pathlib import Path
from harness.core.errors import HarnessError
from harness.core.language import current_language
from harness.i18n import tr
from harness.execution.catalog import definitions
from harness.execution.policy import PathPolicy, file_version


class ExecutorCatalogLanguageTests(unittest.TestCase):
    def test_catalog_language_changes_without_changing_machine_contract(self):
        contract = None
        for language in ('en','zh-CN','en'):
            with current_language.set(language):
                entries = definitions()
                actual = []
                for name, _, schema, effect, method in entries:
                    machine_schema = deepcopy(schema)
                    if name == 'harness_exec':
                        # 仅已声明的说明字段随语言变化，其余协议逐字段保持一致。
                        encoding = machine_schema['properties']['output_encoding']
                        self.assertEqual(encoding.pop('description'), tr('encoding.parameter'))
                    actual.append((name, machine_schema, effect, method))
                if contract is None:
                    contract = actual
                self.assertEqual(actual, contract)
                self.assertEqual(len(entries),25)
                self.assertEqual(len({entry[0] for entry in entries}), 25)
                self.assertIn('load_workspace_dependencies', {entry[0] for entry in entries})
                for name, description, *_ in entries:
                    with self.subTest(language=language, name=name):
                        self.assertEqual(any('\u4e00' <= char <= '\u9fff' for char in description),language=='zh-CN')

    def test_same_path_policy_resolves_rejections_at_call_time(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            policy=PathPolicy(root,root/'private')
            for language in ('en','zh-CN','en'):
                with current_language.set(language):
                    for operation in (lambda:policy.resolve(''),lambda:policy.resolve('../outside'),
                                      lambda:policy.resolve('.env'),lambda:policy.resolve('private/secret'),
                                      lambda:file_version(root)):
                        with self.subTest(language=language),self.assertRaises(HarnessError) as error:
                            operation()
                        self.assertEqual(any('\u4e00' <= char <= '\u9fff' for char in error.exception.message),language=='zh-CN')
