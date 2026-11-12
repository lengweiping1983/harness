"""配置解析的产品错误按当前语言输出，仍拒绝损坏与循环配置。"""
import json
import unittest

import yaml

from harness.i18n import tr

from harness.core.errors import SkillParseError
from harness.core.language import current_language
from harness.skills.yaml_config import load_mapping
from harness.skills.yaml_editing import update_interface


class ConfigValidationLanguageTests(unittest.TestCase):
    def test_yaml_failures_are_localized(self):
        for language in ('zh-CN', 'en'):
            with current_language.set(language):
                for text in ('name: first\nname: second', '[1, 2]', 'value: .inf', 'loop: &loop [*loop]'):
                    with self.subTest(language=language, text=text):
                        with self.assertRaises(SkillParseError) as caught:
                            load_mapping(text)
                        message = caught.exception.message
                        self.assertIn('YAML 配置无效' if language == 'zh-CN' else 'Invalid YAML configuration', message)

    def test_invalid_interface_is_localized_without_changing_input(self):
        text = 'interface: []\npolicy: {allow_implicit_invocation: false}\n'
        for language, expected in [('zh-CN', '必须为映射'), ('en', 'must be a mapping')]:
            with current_language.set(language):
                with self.assertRaises(ValueError) as caught:
                    update_interface(text, {'display_name': 'fixture'}, {'display_name'})
                self.assertIn(expected, str(caught.exception))
        self.assertEqual(text, 'interface: []\npolicy: {allow_implicit_invocation: false}\n')

    def test_duplicate_key_uses_complete_templates_and_preserves_literal_source(self):
        for language in ('en', 'zh-CN', 'en'):
            for key in ('Original {detail} 键', 'original-' + 'a' * 180):
                with self.subTest(language=language, key=key), current_language.set(language):
                    source = json.dumps(key, ensure_ascii=False) + ': first\n' + json.dumps(key, ensure_ascii=False) + ': second\n'
                    with self.assertRaises(SkillParseError) as caught:
                        load_mapping(source)
                    detail = tr('config.validation.message_4', mapping_key=repr(key[:160]))
                    self.assertIsInstance(caught.exception.__cause__, yaml.YAMLError)
                    self.assertEqual(str(caught.exception.__cause__), detail)
                    self.assertEqual(caught.exception.message, tr('config.validation.message_11', detail=detail))
