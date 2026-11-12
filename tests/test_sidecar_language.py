"""技能附加配置错误保留文件并以当前语言报告字段。"""
import tempfile
import unittest
from pathlib import Path

from harness.core.language import current_language
from harness.skills.sidecar import read_sidecar


class SidecarLanguageTests(unittest.TestCase):
    def test_invalid_fields_are_localized_without_rewriting(self):
        cases = [('interface: []', 'interface'), ('interface: {display_name: 3}', 'interface.display_name'),
                 ('policy: {allow_implicit_invocation: no}', 'policy.allow_implicit_invocation'),
                 ('policy: {products: []}', 'policy.products'), ('dependencies: {tools: [3]}', 'dependencies.tools')]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / 'agents/openai.yaml'
            target.parent.mkdir()
            for language in ('en', 'zh-CN'):
                with current_language.set(language):
                    for raw, field in cases:
                        with self.subTest(language=language, field=field):
                            target.write_text(raw, encoding='utf-8')
                            interface, policy, dependencies, errors = read_sidecar(root)
                            self.assertEqual((interface, policy, dependencies), ({}, {}, {}))
                            self.assertEqual(len(errors), 1)
                            self.assertIn(field, errors[0])
                            self.assertEqual(any('\u4e00' <= char <= '\u9fff' for char in errors[0]), language == 'zh-CN')
                            self.assertEqual(target.read_text(encoding='utf-8'), raw)
