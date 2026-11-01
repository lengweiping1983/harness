"""语言设置错误随上下文翻译，拒绝损坏文件且保留原件。"""
import tempfile
import unittest
from pathlib import Path

from harness.core.language import current_language, normalize_language, saved_language


class LanguageValidationTests(unittest.TestCase):
    def test_invalid_preferences_and_settings_follow_language(self):
        messages = {}
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            path = home / 'ui-settings.json'
            original = '{"version": 0, "settings": {}}'
            path.write_text(original, encoding='utf-8')
            for language in ('zh-CN', 'en'):
                with current_language.set(language):
                    for value in ('Chinese', None, 'EN'):
                        with self.assertRaises(ValueError) as caught:
                            normalize_language(value)
                        messages[language, 'preference'] = str(caught.exception)
                    with self.assertRaises(ValueError) as caught:
                        saved_language(home)
                    messages[language, 'settings'] = str(caught.exception)
                self.assertEqual(path.read_text(encoding='utf-8'), original)
        for kind in ('preference', 'settings'):
            self.assertNotEqual(messages['zh-CN', kind], messages['en', kind])
            self.assertTrue(messages['en', kind].isascii())
