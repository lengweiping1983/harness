"""验证中途进入计划、审批广播及下一步骤的限制结算。"""
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from dataclasses import fields
from pathlib import Path

from harness import Harness, HarnessConfig
from harness.app import plans
from harness.i18n import tr
from harness.app.plan_mode import PlanModeController
from harness.session.events import TurnEndReason
from harness.session.store import SessionStore
from harness.tools.builtin.plan_tools import build_exit_plan_mode_tool
from harness.tools.registry import ToolRunContext
from tests.plan_mode_fixture import PlanModeTransport


class PlanModeLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.sessions = SessionStore(self.directory.name)
        self.session = self.sessions.create()
        self.controller = PlanModeController(self.sessions)
        self.updates = []
        self.session.bus.on('plan_mode', lambda payload: self.updates.append(payload))
        await self.session.start_turn()
        await self.session.end_turn(TurnEndReason(kind='completed'))
        await self.controller.set(self.session, True)
        await self.session.start_turn()
        self.updates.clear()
        self.ctx = ToolRunContext(call_id='review-test', session_id=self.session.id,
            cwd=self.directory.name, turn=2, step=1, services={'session': self.session})

    async def review(self, selected, custom=''):
        questions = SimpleNamespace(ask=AsyncMock(return_value=[{
            'id': 'plan-review', 'selected': [tr('ui.components.chat.blocks.QuestionCard.b168fdafe0f4') if selected == 'Approve' else tr('ui.components.chat.blocks.QuestionCard.b367cdfb55b5')], 'custom': custom}]))
        return await build_exit_plan_mode_tool(self.controller, questions).execute(
            {'plan': '# 中途进入的计划\n核对并实施。'}, self.ctx)

    async def test_real_broker_preserves_action_identity_and_localized_labels(self):
        from harness.core.events import EventBus
        from harness.core.language import current_language
        from harness.interaction.questions import QuestionBroker

        for language in ('en', 'zh-CN', 'en'):
            for action in ('approve', 'keep-planning'):
                session = self.sessions.create()
                await self.controller.set(session, True)
                bus = EventBus()
                broker = QuestionBroker(bus)
                received = []

                async def answer(payload):
                    options = payload['questions'][0]['options']
                    received.append(options)
                    self.assertEqual([item['id'] for item in options], ['approve', 'keep-planning'])
                    self.assertEqual([item['label'] for item in options], [
                        tr('ui.components.chat.blocks.QuestionCard.b168fdafe0f4'),
                        tr('ui.components.chat.blocks.QuestionCard.b367cdfb55b5')])
                    label = next(item['label'] for item in options if item['id'] == action)
                    await broker.answer_and_notify(payload['question_id'], [
                        {'id': 'plan-review', 'selected': [label]}], session_id=session.id)

                bus.on('user_question/ask', answer)
                ctx = ToolRunContext(call_id='localized-review', session_id=session.id,
                    cwd=self.directory.name, turn=1, step=1, services={'session': session})
                with current_language.set(language):
                    result = await build_exit_plan_mode_tool(self.controller, broker).execute(
                        {'plan': '# Original 标题\nOriginal 正文'}, ctx)
                self.assertEqual(result.data['approved'], action == 'approve')
                self.assertEqual(plans.versions(session)[-1]['status'],
                    'approved' if action == 'approve' else 'revision_requested')
                self.assertEqual(len(received), 1)
                self.assertEqual(broker.pending(), [])

    async def test_invalid_plan_types_do_not_create_review_or_change_mode(self):
        from harness.core.errors import ToolExecutionError
        from harness.core.language import current_language
        from harness.i18n import tr

        class InvalidPlan:
            def __str__(self):
                raise AssertionError('非法计划不能触发隐式字符串转换')

        questions = SimpleNamespace(ask=AsyncMock())
        tool = build_exit_plan_mode_tool(self.controller, questions)
        before = list(self.session.events)
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                for args in (None, [], '# Wrong container', {}, {'plan': None},
                             {'plan': True}, {'plan': 42}, {'plan': ['# Wrong type']},
                             {'plan': {'heading': '# Wrong type'}}, {'plan': InvalidPlan()}):
                    with self.subTest(language=language, args=args):
                        with self.assertRaises(ToolExecutionError) as error:
                            await tool.execute(args, self.ctx)
                        self.assertEqual(error.exception.message, tr('plan.arguments.planString'))
                        self.assertEqual(list(self.session.events), before)
                        self.assertTrue(self.controller.is_active(self.session))
        questions.ask.assert_not_awaited()

    async def test_approval_broadcasts_exit_before_next_step(self):
        result = await self.review('Approve')
        self.assertTrue(result.data['approved'])
        self.assertEqual(plans.versions(self.session)[-1]['status'], 'approved')
        self.assertEqual(len(self.updates), 1)
        self.assertTrue(self.updates[-1]['active'])
        self.assertIs(self.updates[-1]['pending'], False)
        self.assertFalse(self.updates[-1]['effective'])
        self.assertTrue(self.controller.projection(self.session).active)
        write_tool = SimpleNamespace(schema=SimpleNamespace(name='write'), effect='workspace_write',
                                     dangerous=False, approval_required=False)
        self.assertIsNotNone(self.controller.guard(write_tool, {}, self.ctx))
        await self.controller.settle(self.session)
        self.assertFalse(self.updates[-1]['active'])
        self.assertFalse(self.controller.is_active(self.session))
        self.assertEqual(self.session.latest('plan/mode').data['active'], False)
        self.assertIsNone(self.controller.guard(write_tool, {}, self.ctx))

    async def test_revision_request_does_not_exit_or_publish_exit(self):
        result = await self.review('Keep planning', '补充测试范围')
        self.assertFalse(result.data['approved'])
        self.assertTrue(self.controller.is_active(self.session))
        self.assertEqual(self.updates, [])
        self.assertEqual(plans.versions(self.session)[-1]['status'], 'revision_requested')

    async def test_queued_manual_switch_publishes_once_for_its_session(self):
        self.assertEqual(await self.controller.set(self.session, False), 'queued')
        self.assertEqual(len(self.updates), 1)
        self.assertEqual(self.updates[-1]['session_id'], self.session.id)
        self.assertIs(self.updates[-1]['pending'], False)

    async def test_old_approval_cannot_override_a_new_mode_choice(self):
        async def answer_after_switch(**kwargs):
            await self.controller.set(self.session, False)
            await self.controller.set(self.session, True)
            return [{'id': 'plan-review', 'selected': [tr('ui.components.chat.blocks.QuestionCard.b168fdafe0f4')]}]
        questions = SimpleNamespace(ask=answer_after_switch)
        result = await build_exit_plan_mode_tool(self.controller, questions).execute(
            {'plan': '# 已过期的审批\n等待期间用户重新选择了计划模式。'}, self.ctx)
        self.assertFalse(result.data['approved'])
        self.assertEqual(plans.versions(self.session)[-1]['status'], 'closed')
        self.assertTrue(self.controller.is_active(self.session))


class PlanModeTurnBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_approval_on_last_step_settles_before_turn_returns(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        flags = {item.name: False for item in fields(HarnessConfig) if item.name.startswith('enable_')}
        flags.update(enable_plan_mode=True, enable_interaction=True)
        app = Harness(HarnessConfig(api_key='fixture-only', home=root / 'state', cwd=root,
            language='zh-CN', model='fixture-model', max_steps=2, max_retries=0, **flags),
            transport=PlanModeTransport())
        self.addAsyncCleanup(app.close)
        session = app.create_session()
        await app.run_async(session, 'first ordinary message')
        await app.set_plan_mode(session, True)
        app.questions.ask = AsyncMock(side_effect=[
            [{'id': 'scope', 'selected': ['Current scope']}],
            [{'id': 'plan-review', 'selected': [tr('ui.components.chat.blocks.QuestionCard.b168fdafe0f4')]}],
        ])
        await app.run_async(session, 'plan fixture request')
        self.assertEqual(plans.versions(session)[-1]['status'], 'approved')
        self.assertIsNone(session.open_turn)
        self.assertFalse(app.plan.projection(session).active)
        self.assertIsNone(app.plan.projection(session).pending)
