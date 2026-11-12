"""技能工具的实际错误结果使用调用语言，不放宽可见性限制。"""
import unittest

from harness.core.language import current_language
from harness.i18n import tr
from harness.skills.registry import SkillRegistry
from harness.skills.parser import SkillMetadata
from harness.skills.tool import build_skill_tool
from harness.tools.registry import ToolRunContext


class SkillToolLanguageTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_and_explicit_only_skills(self):
        registry = SkillRegistry()
        registry.register(SkillMetadata(name='explicit-only', description='fixture', content='private', model_invocable=False))
        tool = build_skill_tool(registry)[0]
        ctx = ToolRunContext('fixture', 'session', None, 1, 1)
        for language in ('en', 'zh-CN'):
            with current_language.set(language):
                for name, key in [('missing', 'skill.load.unavailable'), ('explicit-only', 'skill.load.implicit_disabled')]:
                    with self.subTest(language=language, name=name):
                        result = await tool.execute({'name': name}, ctx)
                        self.assertTrue(result.is_error)
                        self.assertEqual(result.content[0].text, tr(key))
                        self.assertNotIn('private', result.content[0].text)
