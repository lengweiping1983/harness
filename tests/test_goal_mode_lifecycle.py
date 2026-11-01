"""核对目标的真实生命周期、证据要求及计划切换隔离。"""
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path

from harness import Harness, HarnessConfig
from tests.goal_mode_fixture import GoalModeTransport, prepare


class GoalModeLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        prepare(root)
        flags = {item.name: False for item in fields(HarnessConfig) if item.name.startswith('enable_')}
        flags.update(enable_goal=True, enable_plan_mode=True, enable_interaction=True, enable_fs=True)
        self.app = Harness(HarnessConfig(api_key='fixture-only', home=root / 'state', cwd=root,
            model='fixture-model', language='zh-CN', max_steps=6, max_retries=0, **flags), transport=GoalModeTransport())
        self.addAsyncCleanup(self.app.close)
        self.session = self.app.create_session()

    async def test_noninitial_goal_completes_with_actual_read_evidence_and_stays_complete(self):
        await self.app.run_async(self.session, 'ordinary first message')
        result = await self.app.run_async(self.session, '/goal goal fixture complete')
        self.assertEqual(result.reason, 'completed')
        goal = self.app.goals.get(self.session.id)
        self.assertEqual(goal.phase, 'complete')
        self.assertEqual(goal.completion[0]['evidence'][0]['name'], 'read')
        identity = goal.id
        await self.app.run_async(self.session, 'ordinary follow-up')
        self.assertEqual(self.app.goals.get(self.session.id).id, identity)
        self.assertEqual(self.app.goals.get(self.session.id).phase, 'complete')

    async def test_paused_and_blocked_goals_survive_ordinary_supplements(self):
        for phase in ('paused', 'blocked'):
            with self.subTest(phase=phase):
                session = self.app.create_session()
                goal = self.app.goals.create(session.id, objective='保留未完成目标')
                if phase == 'paused':
                    self.app.goals.pause(session.id, goal_id=goal.id, revision=goal.revision)
                else:
                    self.app.goals.block(session.id, goal_id=goal.id, revision=goal.revision, reason='等待依赖')
                await self.app.run_async(session, '补充普通要求')
                current = self.app.goals.get(session.id)
                self.assertEqual(current.id, goal.id)
                self.assertEqual(current.phase, phase)
                self.assertEqual(current.activation, 'disarmed')

    async def test_plan_selection_preserves_paused_goal_and_clear_does_not_restore_it(self):
        goal = self.app.goals.create(self.session.id, objective='保留目标')
        paused = self.app.goals.pause(self.session.id, goal_id=goal.id, revision=goal.revision)
        await self.app.set_plan_mode(self.session, True)
        await self.app.set_plan_mode(self.session, False)
        self.assertEqual(self.app.goals.get(self.session.id).id, goal.id)
        self.assertEqual(self.app.goals.get(self.session.id).phase, 'paused')
        await self.app.goal_runtime.control(self.session.id, {'action': 'clear', 'goal_id': paused.id, 'revision': paused.revision})
        await self.app.run_async(self.session, '清除后的普通要求')
        self.assertIsNone(self.app.goals.get(self.session.id))
