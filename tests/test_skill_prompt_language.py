"""技能宿主说明及内置预设按当前语言解析，保留用户正文。"""
import unittest
from pathlib import Path

from harness.core.language import current_language
from harness.connectors.presets import PRESETS, get_preset
from harness.skills.parser import SkillMetadata
from harness.skills.rendering import render_loaded_skill
from harness.skills.context import snapshot, project


class SkillPromptLanguageTests(unittest.TestCase):
    def test_skill_wrapper_and_reference_follow_language(self):
        skill = SkillMetadata(name='fixture', description='user description', content=('user body ' * 100).strip(),
                              path=Path('/tmp/fixture/SKILL.md'), dependencies={'tools': [{'type': 'mcp', 'value': 'original'}]})
        for language in ('en', 'zh-CN'):
            with current_language.set(language):
                text = render_loaded_skill(skill)
                self.assertIn('Skill directory:' if language == 'en' else '技能目录：', text)
                self.assertIn(skill.content, text)
                value = snapshot(skill)
                from harness.skills.context import spans
                seen = {}
                project(value.text, spans(value.text, [value]), seen)
                reference = project(value.text, spans(value.text, [value]), seen)
                self.assertIn('Use the complete instructions' if language == 'en' else '完整指令', reference)

    def test_preset_views_do_not_mutate_shared_registration(self):
        original = PRESETS['github'].record.to_dict()
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                preset = get_preset('github')
                self.assertIsNot(preset.record, PRESETS['github'].record)
                text = preset.summary()['description']
                self.assertEqual(any('\u4e00' <= char <= '\u9fff' for char in text), language == 'zh-CN')
        self.assertEqual(PRESETS['github'].record.to_dict(), original)
