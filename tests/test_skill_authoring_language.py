"""技能创建的默认说明与验证错误遵循当前语言。"""
import os
import tempfile
import unittest
import subprocess
import sys
from pathlib import Path

from harness.core.language import current_language
from harness.i18n import tr
from harness.skills.authoring import create_skill, interface_text, skill_name
from harness.skills.yaml_config import load_mapping


class SkillAuthoringLanguageTests(unittest.TestCase):
    def test_generated_examples_follow_language(self):
        for language in ('en', 'zh-CN'):
            with current_language.set(language), tempfile.TemporaryDirectory() as directory:
                create_skill(directory, 'fixture', 'description', 'instructions', scaffold=True,
                             examples=True, resources=('scripts', 'references', 'assets'))
                root = Path(directory) / 'fixture'
                output = subprocess.check_output([sys.executable, str(root / 'scripts/example.py')], text=True, encoding='utf-8',
                                                 env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
                for text in (output, (root / 'references/api_reference.md').read_text(encoding='utf-8'),
                             (root / 'assets/example_asset.txt').read_text(encoding='utf-8')):
                    self.assertEqual(any('\u4e00' <= char <= '\u9fff' for char in text), language == 'zh-CN')

    def test_generated_description_and_errors_follow_language(self):
        for language in ('en', 'zh-CN'):
            with current_language.set(language):
                description = load_mapping(interface_text('fixture'))['interface']['short_description']
                self.assertTrue(25 <= len(description) <= 64)
                self.assertEqual(any('\u4e00' <= char <= '\u9fff' for char in description), language == 'zh-CN')
                with self.assertRaises(ValueError) as caught:
                    skill_name('Invalid Name')
                self.assertEqual(any('\u4e00' <= char <= '\u9fff' for char in str(caught.exception)), language == 'zh-CN')
                with tempfile.TemporaryDirectory() as directory:
                    result = create_skill(directory, 'fixture', 'original description', 'original instructions')
                    self.assertEqual(result['description'], 'original description')
                    self.assertEqual(result['content'], 'original instructions')

    def test_interface_override_feedback_preserves_literal_original_in_each_language(self):
        for language in ('en', 'zh-CN', 'en'):
            for override in ('unknown=Original {override} 原文', 'display_name=', 'Original {field} 原文'):
                with self.subTest(language=language, override=override), current_language.set(language):
                    with self.assertRaises(ValueError) as caught:
                        interface_text('fixture', [override])
                    self.assertEqual(str(caught.exception), tr('skill.authoring.bb0bb940629a', override=override))
                    self.assertIn(override, str(caught.exception))
