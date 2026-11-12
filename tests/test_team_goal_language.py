"""团队目标指引与拒绝错误双语化，不改变写权限和生命周期检查。"""
import unittest
from types import SimpleNamespace

from harness.app.team_goal import TeamGoal
from harness.core.errors import ToolError
from harness.core.language import current_language
from harness.i18n import tr


class TeamGoalLanguageTests(unittest.IsolatedAsyncioTestCase):
    async def test_member_guidance_and_guards_follow_language(self):
        goal = SimpleNamespace(id='goal')
        session = SimpleNamespace(id='main', current_turn=1, open_turn=1)
        harness = SimpleNamespace(goals=SimpleNamespace(get=lambda _: goal, context=lambda _: 'original context'))
        for language in ('zh-CN', 'en'):
            with current_language.set(language):
                coordinator = TeamGoal(harness, session, goal)
                state = SimpleNamespace(session=None, text='original task')
                coordinator.register(state)
                self.assertIn(tr('goal.team.member_policy'), state.text)
                self.assertIn('original task', state.text)
                self.assertFalse(state._team_goal_writer)
                with self.assertRaises(ToolError) as caught:
                    await coordinator.context(SimpleNamespace(services={}), write=True)
                self.assertEqual(caught.exception.code, 'GOAL_TEAM_READ_ONLY')
                self.assertEqual(caught.exception.message, tr('goal.team.read_only'))
                coordinator.closed = True
                with self.assertRaises(ToolError) as caught:
                    await coordinator.context(SimpleNamespace(services={}))
                self.assertEqual(caught.exception.code, 'GOAL_TEAM_EXPIRED')
                self.assertEqual(caught.exception.message, tr('goal.team.expired'))
