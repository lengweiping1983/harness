"""专业协作方法随团队提示词生效，不替换任务或角色，也不施加固定产出格式。"""
import unittest

from harness.agents.members import lead_persona, member_persona
from harness.agents.parser import AgentDefinition, AgentMember
from harness.agents.prompt_team import render_activation_notice, render_team_lead_section
from harness.agents.registry import AgentRegistry
from harness.core.language import current_language


class TeamExpertMethodTests(unittest.TestCase):
    def setUp(self):
        self.registry = AgentRegistry()
        self.lead = AgentMember(name='host', role='lead', prompt='协调当前任务')
        self.worker = AgentMember(name='explorer', prompt='保护开放构思与重构')
        self.team = AgentDefinition(name='creative-team', description='测试团队', agent_type='team',
            system_prompt='本次允许自由探索，最后按用户要求交付。', members=(self.lead, self.worker))

    def test_lead_and_member_receive_method_without_duplicate_injection(self):
        for lang in ('zh-CN', 'en'):
            token = current_language.set(lang)
            try:
                lead = lead_persona(self.team, self.registry)
                worker = member_persona(self.worker, self.team, self.registry)
                self.assertEqual(lead.count('<team_expert_method>'), 1)
                self.assertEqual(worker.count('<team_expert_method>'), 1)
                self.assertIn(self.team.system_prompt, lead)
                self.assertIn(self.worker.prompt, worker)
            finally:
                current_language.reset(token)

    def test_direct_lead_and_activation_keep_professional_method(self):
        self.assertIn('<team_expert_method>', member_persona(self.lead, self.team, self.registry))
        self.assertIn('<team_expert_method>', render_activation_notice(self.team, self.registry, '本次自由构思'))

    def test_non_team_does_not_gain_team_instructions(self):
        ordinary = AgentDefinition(name='writer', description='独立写作')
        self.assertEqual(render_team_lead_section(ordinary, self.registry), '')
