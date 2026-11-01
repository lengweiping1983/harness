"""浏览器策略按回合语言和真实能力组装。"""
import unittest

from harness.core.language import current_language
from harness.prompt.tool_policies import browser_testing_policy, build_tool_policies


class BrowserPolicyLanguageTests(unittest.TestCase):
    def test_language_and_capability_matrix(self):
        for language in ('zh-CN', 'en'):
            with current_language.set(language):
                self.assertEqual(browser_testing_policy(set()), '')
                for names in ({'browser'}, {'bash'}, {'pwsh'}):
                    text = browser_testing_policy(names)
                    self.assertIn('浏览器测试前' if language == 'zh-CN' else 'Before browser testing', text)
                    self.assertIn('WebGL', text)
                    self.assertEqual('screenshot_id' in text, 'browser' in names)
                    if language == 'en':
                        self.assertFalse(any('\u4e00' <= char <= '\u9fff' for char in text))

    def test_registered_provider_does_not_freeze_language(self):
        with current_language.set('zh-CN'):
            provider = next(provider for name, _, provider in build_tool_policies({'browser'})
                            if name == 'tool:browser-testing')
        for language, expected in [('en', 'Before browser testing'), ('zh-CN', '浏览器测试前')]:
            with current_language.set(language):
                self.assertIn(expected, provider())
