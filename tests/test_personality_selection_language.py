"""内置全局人格按调用语言解析，用户正文和持久原件保持不变。"""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from harness.agents.personality import PersonalityController, profiles, render, system_personality
from harness.agents.personality_selection import PersonalitySelection
from harness.core.language import current_language
from harness.i18n import tr


class PersonalitySelectionLanguageTests(unittest.TestCase):
    def test_selected_builtin_renders_current_language_without_rewriting_saved_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            selection = PersonalitySelection(Path(folder))
            with current_language.set('en'):
                profile = next(row for row in profiles() if row['code'] == 'delivery-lead')
                saved = selection.select({**profile, 'id':profile['code'], 'source':'builtin'}, '')
            original = selection.path.read_bytes()
            controller = PersonalityController(SimpleNamespace(config=SimpleNamespace(home=Path(folder))))
            for language in ('en', 'zh-CN', 'en'):
                with current_language.set(language):
                    actual = selection.read()
                    self.assertEqual(actual['revision'], saved['revision'])
                    self.assertEqual(actual['profile']['name'], tr('personality.delivery-lead.name'))
                    self.assertEqual(actual['profile']['description'], tr('personality.delivery-lead.description'))
                    self.assertEqual(actual['profile']['prompt'], render('delivery-lead'))
                    self.assertEqual(controller.context({}), system_personality(render('delivery-lead')))
                    self.assertEqual(selection.path.read_bytes(), original)

    def test_all_builtin_codes_and_axes_reach_selected_prompt_without_rewriting(self):
        from harness.agents.personality import STYLE_CODES, STYLE_PREFERENCES, STYLE_AXES, recommend
        with tempfile.TemporaryDirectory() as folder:
            selection = PersonalitySelection(Path(folder))
            controller = PersonalityController(SimpleNamespace(config=SimpleNamespace(home=Path(folder))))
            revision = ''
            for code in STYLE_CODES:
                with current_language.set('en'):
                    profile = next(row for row in profiles() if row['code'] == code)
                    saved = selection.select({**profile, 'id': code, 'source': 'builtin'}, revision)
                revision = saved['revision']
                original = selection.path.read_bytes()
                for language in ('en', 'zh-CN', 'en'):
                    with self.subTest(code=code, language=language), current_language.set(language):
                        selected = selection.read()['profile']
                        self.assertEqual(selected['name'], tr('personality.' + code + '.name'))
                        self.assertEqual(selected['description'], tr('personality.' + code + '.description'))
                        expected = tr('python.personality.prompt', name=selected['name'],
                            style='\n'.join(tr('personality.axis.' + axis) for axis in STYLE_PREFERENCES[code]))
                        self.assertEqual(selected['prompt'], expected)
                        self.assertEqual(controller.context({}), system_personality(expected))
                        self.assertEqual(recommend(list(STYLE_PREFERENCES[code])), code)
                        self.assertEqual(selection.path.read_bytes(), original)
                        self.assertEqual(selection.read()['revision'], revision)
            self.assertEqual({value for pair in STYLE_AXES for value in pair},
                {value for values in STYLE_PREFERENCES.values() for value in values})

    def test_selected_user_text_remains_original_in_every_language(self):
        with tempfile.TemporaryDirectory() as folder:
            selection = PersonalitySelection(Path(folder))
            profile = {'id':'a'*32, 'source':'user', 'code':None, 'name':'Original 用户 {name}',
                       'description':'Original 说明 {name}', 'prompt':'Original prompt 原文 {language}'}
            selection.select(profile, '')
            original = selection.path.read_bytes()
            controller = PersonalityController(SimpleNamespace(config=SimpleNamespace(home=Path(folder))))
            for language in ('en', 'zh-CN', 'en'):
                with current_language.set(language):
                    self.assertEqual(selection.read()['profile'], profile)
                    self.assertEqual(controller.context({}), system_personality(profile['prompt']))
                    self.assertEqual(selection.path.read_bytes(), original)

    def test_invalid_builtin_snapshot_is_preserved_and_does_not_fall_back_to_raw_text(self):
        import json
        from harness.core.errors import HarnessError
        with tempfile.TemporaryDirectory() as folder:
            selection = PersonalitySelection(Path(folder))
            raw = json.dumps({'revision':'a'*32, 'profile':{'id':'invalid', 'source':'builtin', 'code':None,
                'name':'Original', 'description':'Original', 'prompt':'Original raw prompt'}}, ensure_ascii=False).encode('utf-8')
            selection.path.write_bytes(raw)
            for language in ('en', 'zh-CN', 'en'):
                with current_language.set(language), self.assertRaises(HarnessError) as caught:
                    selection.read()
                self.assertEqual(caught.exception.code, 'personality/storage-invalid')
                self.assertEqual(selection.path.read_bytes(), raw)
