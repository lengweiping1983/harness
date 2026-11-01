"""错误成员引用应给出可直接修正的当前花名册，而不启动错误成员。"""
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from harness.agents.parser import AgentDefinition, AgentMember
from harness.app.prompt_team_run import PromptTeamRun
from harness.core.errors import ToolExecutionError


class TeamDelegationFeedbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_agent_definition_name_is_not_member_id(self):
        run = PromptTeamRun.__new__(PromptTeamRun)
        run.lock, run.closed, run.direct = asyncio.Lock(), False, False
        run.session = SimpleNamespace(id='parent')
        run.team = AgentDefinition(name='delivery', description='交付', agent_type='team', members=(
            AgentMember(name='coordinator', role='lead', prompt='协调'),
            AgentMember(name='developer', agent='delivery-builder'),
            AgentMember(name='verifier', agent='delivery-verifier', access='read')))
        spawn = Mock()
        run.harness = SimpleNamespace(subagents=SimpleNamespace(require_accepting=Mock(), spawn=spawn))
        with self.assertRaises(ToolExecutionError) as failure:
            await run.spawn({'member': 'delivery-builder'}, SimpleNamespace(session_id='parent'))
        message = str(failure.exception)
        self.assertIn('developer', message)
        self.assertIn('verifier', message)
        self.assertNotIn('coordinator', message)
        spawn.assert_not_called()

    async def test_busy_member_returns_existing_identity_even_at_capacity(self):
        for at_capacity in (False, True):
            with self.subTest(at_capacity=at_capacity):
                run = PromptTeamRun.__new__(PromptTeamRun)
                run.lock, run.closed, run.direct = asyncio.Lock(), False, False
                run.session = SimpleNamespace(id='parent')
                run.team = AgentDefinition(name='research-team', description='研究', agent_type='team', members=(
                    AgentMember(name='host', role='lead', prompt='协调'),
                    AgentMember(name='research', prompt='研究')))
                existing = SimpleNamespace(id='sub-real-existing', status='running', settled=False)
                run.states = {'research': existing}
                if at_capacity:
                    run.states.update({f'other-{i}': SimpleNamespace(settled=False) for i in range(3)})
                spawn = Mock()
                run.harness = SimpleNamespace(subagents=SimpleNamespace(require_accepting=Mock(), spawn=spawn))
                with self.assertRaises(ToolExecutionError) as failure:
                    await run.spawn({'member': 'research', 'prompt': '原任务'}, SimpleNamespace(session_id='parent'))
                message = str(failure.exception)
                self.assertIn('research', message)
                self.assertIn(existing.id, message)
                self.assertIn('wait_agent', message)
                self.assertIn('followup_task', message)
                self.assertIs(run.states['research'], existing)
                spawn.assert_not_called()
