"""模型目录行的完整模板按调用语言解析，用户描述保持原样。"""
import unittest
from harness.agents.parser import AgentDefinition
from harness.skills.parser import SkillMetadata
from harness.core.language import current_language


class CatalogSummaryLanguageTests(unittest.TestCase):
    def test_same_catalog_entries_follow_runtime_language(self):
        agent = AgentDefinition(name='writer', description='Original 原文', agent_type='team')
        skill = SkillMetadata(name='fixture', description='Original 原文', content='', when_to_use='User 原文')
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language), self.subTest(language=language):
                self.assertEqual(agent.summary_line(), '- writer(Agent team): Original 原文' if language == 'en' else '- writer（团队）：Original 原文')
                self.assertEqual(skill.summary_line(), '- fixture: Original 原文 (User 原文)' if language == 'en' else '- fixture：Original 原文（User 原文）')
                self.assertEqual(AgentDefinition(name='plain', description='Original').summary_line(), '- plain: Original' if language == 'en' else '- plain：Original')
