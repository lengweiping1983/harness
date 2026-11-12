"""计划正文绕过审批时，验证真实循环纠正、显式步骤预算与写入隔离。"""
import asyncio
import json
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

from harness import Harness, HarnessConfig
from harness.app import plans
from harness.core.language import current_language
from harness.i18n import tr
from harness.interaction.questions import QuestionError
from harness.llm.transport import Transport


def tool(name, arguments):
    """构造供应商协议中的工具调用。"""
    return {'tool_calls': [{'index': 0, 'id': 'call-' + uuid4().hex, 'type': 'function',
        'function': {'name': name, 'arguments': json.dumps(arguments)}}]}


class ScriptedPlanTransport(Transport):
    def __init__(self, replies):
        self.replies = replies
        self.requests = []

    async def post_stream(self, url, *, headers, body, timeout):
        self.requests.append(json.loads(body))
        delta = self.replies[min(len(self.requests) - 1, len(self.replies) - 1)]
        for chunk in [
            {'choices': [{'delta': {'role': 'assistant', **delta}, 'finish_reason': None}]},
            {'choices': [{'delta': {}, 'finish_reason': 'tool_calls' if 'tool_calls' in delta else 'stop'}],
             'usage': {'prompt_tokens': 20, 'completion_tokens': 4}},
        ]:
            yield ('data: ' + json.dumps(chunk) + '\n\n').encode()
        yield b'data: [DONE]\n\n'


class PlanCompletionTests(unittest.IsolatedAsyncioTestCase):
    async def create_app(self, replies, *, language='zh-CN', interaction=True):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        flags = {item.name: False for item in fields(HarnessConfig) if item.name.startswith('enable_')}
        flags.update(enable_plan_mode=True, enable_interaction=interaction, enable_fs=True)
        transport = ScriptedPlanTransport(replies)
        app = Harness(HarnessConfig(api_key='fixture-only', home=root/'state', cwd=root,
            language=language, model='fixture-model', max_steps=10, max_retries=0, **flags), transport=transport)
        self.addAsyncCleanup(app.close)
        session = app.create_session()
        await app.set_plan_mode(session, True)
        return app, session, transport, root

    async def test_plain_plan_is_corrected_then_reviewed_before_writing(self):
        app, session, transport, root = await self.create_app([
            {'content': '# 游戏实施计划\n创建游戏文件。'},
            tool('write', {'path': 'premature.txt', 'content': 'forbidden'}),
            tool('exit_plan_mode', {'plan': '# 游戏实施计划\n批准后创建 approved.txt。'}),
            tool('write', {'path': 'approved.txt', 'content': 'approved'}),
            {'content': '完成'},
        ])
        async def approve(**kwargs):
            self.assertFalse((root/'premature.txt').exists())
            self.assertFalse((root/'approved.txt').exists())
            self.assertTrue(app.plan.is_active(session))
            return [{'id': 'plan-review', 'selected': [kwargs['questions'][0]['options'][0]['label']]}]
        app.questions.ask = AsyncMock(side_effect=approve)
        result = await app.run_async(session, '开发一个全新的3D游戏')
        self.assertEqual(result.reason, 'completed')
        self.assertEqual(len(transport.requests), 5)
        self.assertTrue(any(e.data.get('kind') == 'stop_hook_feedback' and 'exit_plan_mode' in e.data.get('text', '')
                            for e in session.events_of_type('note')))
        self.assertFalse((root/'premature.txt').exists())
        self.assertEqual((root/'approved.txt').read_text(encoding='utf-8'), 'approved')
        self.assertEqual(plans.versions(session)[-1]['status'], 'approved')
        self.assertFalse(app.plan.is_active(session))
        denied = next(e for e in session.events_of_type('tool/result') if e.data.get('name') == 'write')
        self.assertTrue(denied.data['results'][0]['is_error'])

    async def test_repeated_plain_reply_uses_explicit_step_budget_in_both_languages(self):
        for language in ('zh-CN', 'en'):
            with self.subTest(language=language):
                app, session, transport, root = await self.create_app([{'content': 'plan without a heading'}], language=language)
                result = await app.run_async(session, 'Plan the implementation')
                self.assertEqual(result.reason, 'max-steps')
                self.assertEqual(result.error_info['code'], 'agent/step-limit')
                self.assertEqual(len(transport.requests), 10)
                self.assertTrue(app.plan.is_active(session))
                self.assertEqual(plans.versions(session), [])
                self.assertIsNone(session.open_turn)
                self.assertFalse((root/'approved.txt').exists())
                self.assertEqual(session.latest('turn/end').data['reason']['kind'], 'error')

    async def test_review_channel_failure_can_report_blocker_without_approval(self):
        app, session, transport, _ = await self.create_app([
            tool('exit_plan_mode', {'plan': '# 审批计划\n等待批准。'}), {'content': '审批不可用，请手动切换模式。'}], interaction=False)
        result = await app.run_async(session, '制定计划')
        self.assertEqual(result.reason, 'completed')
        self.assertEqual(len(transport.requests), 2)
        self.assertTrue(app.plan.is_active(session))
        self.assertFalse(any(p['status'] == 'approved' for p in plans.versions(session)))

    async def test_revision_feedback_does_not_allow_plain_plan_to_finish(self):
        app, session, transport, _ = await self.create_app([
            tool('exit_plan_mode', {'plan': '# 初版计划\n等待批准。'}), {'content': '# 修改后计划\n补充测试。'}])
        app.questions.ask = AsyncMock(return_value=[{'id': 'plan-review', 'selected': ['Keep planning'], 'custom': '补充测试'}])
        result = await app.run_async(session, '制定计划')
        self.assertEqual(result.reason, 'max-steps')
        self.assertEqual(result.error_info['code'], 'agent/step-limit')
        self.assertEqual(plans.versions(session)[-1]['status'], 'revision_requested')
        self.assertTrue(app.plan.is_active(session))

    async def test_invalid_plan_arguments_do_not_exempt_review(self):
        app, session, _, _ = await self.create_app([
            tool('exit_plan_mode', {'plan': 'missing required heading'}), {'content': '# 仍未提交的计划'}])
        result = await app.run_async(session, '制定计划')
        self.assertEqual(result.reason, 'max-steps')
        self.assertEqual(result.error_info['code'], 'agent/step-limit')
        self.assertEqual(plans.versions(session), [])
        self.assertTrue(app.plan.is_active(session))

    async def test_normal_mode_plain_answer_still_finishes(self):
        app, session, transport, _ = await self.create_app([{'content': '普通回答'}])
        await app.set_plan_mode(session, False)
        result = await app.run_async(session, '你好')
        self.assertEqual(result.reason, 'completed')
        self.assertEqual(len(transport.requests), 1)

    async def test_closed_review_can_report_blocker_but_does_not_exempt_next_turn(self):
        app, session, transport, _ = await self.create_app([
            tool('exit_plan_mode', {'plan': '# 待审计划\n等待批准。'}), {'content': '审批已关闭。'}])
        app.questions.ask = AsyncMock(side_effect=QuestionError('fixture review closed'))
        result = await app.run_async(session, '制定计划')
        self.assertEqual(result.reason, 'completed')
        self.assertEqual(plans.versions(session)[-1]['status'], 'closed')
        self.assertTrue(app.plan.is_active(session))
        result = await app.run_async(session, '重新制定计划')
        self.assertEqual(result.reason, 'max-steps')
        self.assertEqual(result.error_info['code'], 'agent/step-limit')
        self.assertEqual(len(transport.requests), 12)

    async def test_manual_exit_during_correction_allows_ordinary_completion(self):
        app, session, transport, _ = await self.create_app([{'content': '普通回答'}])
        original = transport.post_stream
        async def reply_after_exit(*args, **kwargs):
            if transport.requests:
                await app.set_plan_mode(session, False)
            async for chunk in original(*args, **kwargs):
                yield chunk
        transport.post_stream = reply_after_exit
        result = await app.run_async(session, '制定计划')
        self.assertEqual(result.reason, 'completed')
        self.assertEqual(len(transport.requests), 2)
        self.assertFalse(app.plan.is_active(session))

    async def test_parent_restriction_and_runtime_language_are_preserved(self):
        app, session, _, _ = await self.create_app([{'content': 'unused'}])
        await session.start_turn()
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                self.assertEqual(app.plan.completion_feedback(session), tr('plan.completion.required'))
        child = app.create_session()
        app.plan.bind_parent(child.id, session.id)
        await child.start_turn()
        self.assertTrue(app.plan.is_active(child))
        self.assertIsNone(app.plan.completion_feedback(child))

    async def test_entering_plan_while_stop_hook_waits_cannot_finish_without_review(self):
        app, session, transport, _ = await self.create_app([
            {'content': '准备结束'}, tool('exit_plan_mode', {'plan': '# 后切入的计划\n等待审批。'}), {'content': '完成'}])
        await app.set_plan_mode(session, False)
        app.questions.ask = AsyncMock(side_effect=lambda **kwargs: [{'id': 'plan-review', 'selected': [kwargs['questions'][0]['options'][0]['label']]}])
        agent = app._agent_for(session)
        checked = []
        async def enter_plan(*args):
            if not checked:
                await app.set_plan_mode(session, True)
            checked.append(True)
        agent.stop_guard = enter_plan
        result = await agent.prompt(session, '制定计划')
        self.assertEqual(result.reason, 'completed')
        self.assertEqual(len(transport.requests), 3)
        self.assertEqual(plans.versions(session)[-1]['status'], 'approved')
        self.assertFalse(app.plan.is_active(session))

    async def test_cancelled_review_aborts_without_approval_or_writing(self):
        app, session, transport, root = await self.create_app([
            tool('exit_plan_mode', {'plan': '# 被取消的计划\n批准后才写文件。'})])
        app.questions.ask = AsyncMock(side_effect=asyncio.CancelledError())
        result = await app.run_async(session, '制定计划')
        self.assertEqual(result.reason, 'aborted')
        self.assertEqual(len(transport.requests), 1)
        self.assertEqual(plans.versions(session)[-1]['status'], 'closed')
        self.assertTrue(app.plan.is_active(session))
        self.assertFalse((root/'approved.txt').exists())
        self.assertIsNone(session.open_turn)
