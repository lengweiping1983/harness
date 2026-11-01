"""沟通风格目录与语义偏好推荐的业务回归。"""
import itertools
import unittest

from harness.agents.personality import STYLE_AXES, normalize, profiles, recommend, render
from harness.core.errors import HarnessError
from harness.core.language import current_language


class PersonalityPreferencesTests(unittest.TestCase):
    def test_every_preference_combination_resolves_to_complete_profile(self):
        catalog = {row['code']: row for row in profiles()}
        selected = set()
        for choices in itertools.product(*STYLE_AXES):
            code = recommend(list(choices))
            selected.add(code)
            self.assertEqual(normalize(code), code)
            row = catalog[code]
            self.assertTrue(row['name'])
            self.assertTrue(row['description'])
            self.assertEqual(row['prompt'], render(code))
            self.assertNotIn('personality.axis.', row['prompt'])
            self.assertNotIn('personality.' + code, row['name'])
        self.assertEqual(selected, set(catalog))
        self.assertEqual(len(selected), 16)

    def test_invalid_preferences_are_rejected(self):
        for choices in (None, [], ['reflective'] * 4,
                        ['proactive', 'conceptual', 'analytical', 'invalid']):
            with self.subTest(choices=choices), self.assertRaises(HarnessError):
                recommend(choices)
        with self.assertRaises(HarnessError):
            normalize('unknown-style')

    def test_legacy_codes_explain_current_catalog_in_each_language(self):
        """旧代号明确拒绝，切换语言后提示重新选择当前目录。"""
        expected = {
            'en': ('current catalog', 'not supported'),
            'zh-CN': ('当前个性目录', '不支持'),
        }
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                self.assertIsNone(normalize(None))
                self.assertEqual(normalize('systems-planner'), 'systems-planner')
                for code in ('INTJ', 'enfp', 'unknown-style'):
                    with self.subTest(language=language, code=code):
                        with self.assertRaises(HarnessError) as caught:
                            normalize(code)
                        self.assertEqual(caught.exception.code, 'personality/invalid')
                        for fragment in expected[language]:
                            self.assertIn(fragment, caught.exception.message)
