"""权限审批使用完整语言模板，保留原参数和真实执行边界。"""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from harness.app.permissions import PermissionPolicy
from harness.core.language import current_language
from harness.i18n import tr
from harness.llm.types import ToolSchema, ToolUseBlock
from harness.session.store import SessionStore
from harness.tools.registry import ToolDefinition, ToolRegistry, ToolRunContext


class PermissionCopyTemplateTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self, policy_name):
        sessions = SessionStore()
        session = sessions.create()
        host = SimpleNamespace(sessions=sessions, get_session=sessions.get,
            config=SimpleNamespace(permission_policy=policy_name, model='OriginalModel'),
            subagents=SimpleNamespace(state_for_session=lambda _: None, list_states=lambda: []),
            _agents={}, questions=SimpleNamespace(ask=AsyncMock()),
            adapter=SimpleNamespace(complete=AsyncMock()))
        policy = PermissionPolicy(host)
        registry = ToolRegistry()
        registry.add_guard(policy.guard)
        execute = AsyncMock(return_value={'ok': True})
        registry.register(ToolDefinition(ToolSchema(name='OriginalTool', description='Original 描述',
            parameters={'type': 'object', 'properties': {'text': {'type': 'string'}}, 'required': ['text']}),
            execute, effect='execute'))
        context = ToolRunContext(call_id='fixture', session_id=session.id, cwd='/Original/原文',
            turn=1, step=1, services={'session': session})
        call = ToolUseBlock(id='original-call', name='OriginalTool', input={'text': 'Original 用户原文 {tool}'})
        return host, policy, session, registry, execute, context, call

    async def test_manual_question_is_complete_and_allows_only_selected_operation(self):
        for language in ('en', 'zh-CN', 'en'):
            with self.subTest(language=language), current_language.set(language):
                host, policy, session, registry, execute, context, call = self.fixture('request_approval')
                host.questions.ask.return_value = [{'selected': [tr('python.permissions.f9881e36e9cd')]}]
                result = await registry.execute_call(call, context)
                self.assertFalse(result.is_error)
                execute.assert_awaited_once()
                asked = host.questions.ask.await_args.kwargs
                raw = json.dumps(call.input, ensure_ascii=False)
                expected = (f'Confirm execution OriginalTool?\nDirectory: /Original/原文\nParameters: {raw}' if language == 'en'
                    else f'确认执行 OriginalTool？\n目录：/Original/原文\n参数：{raw}')
                self.assertEqual(asked['questions'][0]['question'], expected)
                self.assertEqual(asked['session_id'], session.id)
                self.assertEqual(asked['call_id'], call.id)
                self.assertEqual(policy.grants, {})
                host.adapter.complete.assert_not_awaited()

    async def test_real_broker_keeps_action_ids_and_exact_operation_grants(self):
        from harness.core.events import EventBus
        from harness.interaction.questions import QuestionBroker

        for language in ('en', 'zh-CN', 'en'):
            for action in ('allow-once', 'allow-temporarily', 'deny'):
                with self.subTest(language=language, action=action), current_language.set(language):
                    host, policy, session, registry, execute, context, call = self.fixture('request_approval')
                    bus = EventBus()
                    broker = QuestionBroker(bus)
                    host.questions = broker
                    asked = []

                    async def answer(payload):
                        self.assertEqual(payload['questions'][0]['operation'],
                            {'tool': 'OriginalTool', 'cwd': '/Original/原文', 'effect': 'execute',
                             'approval': {'policy': 'request_approval', 'reasons': ['command_execution']}})
                        options = payload['questions'][0]['options']
                        self.assertEqual([option['id'] for option in options], ['allow-once', 'allow-temporarily', 'deny'])
                        self.assertEqual([option['label'] for option in options], [
                            tr('python.permissions.f9881e36e9cd'), tr('python.permissions.4e9b6b4d7c21'),
                            tr('python.permissions.136de7a8c46f')])
                        label = next(option['label'] for option in options if option['id'] == action)
                        asked.append(label)
                        await broker.answer_and_notify(payload['question_id'], [{'id': 'permission', 'selected': [label], 'selected_ids': [action]}],
                            session_id=session.id)

                    bus.on('user_question/ask', answer)
                    result = await registry.execute_call(call, context)
                    self.assertEqual(result.is_error, action == 'deny')
                    self.assertEqual(execute.await_count, 0 if action == 'deny' else 1)
                    self.assertEqual(len(policy.grants), 1 if action == 'allow-temporarily' else 0)
                    decision = session.latest('note').data
                    self.assertEqual(decision['decision'], tuple(asked))
                    self.assertEqual(decision['expires_after_seconds'], 900 if action == 'allow-temporarily' else 0)
                    self.assertEqual(broker.pending(), [])
                    host.adapter.complete.assert_not_awaited()

    async def test_automatic_denial_keeps_original_reason_and_does_not_execute(self):
        reason = 'Original reason 原因 {tool}'
        for language in ('en', 'zh-CN', 'en'):
            with self.subTest(language=language), current_language.set(language):
                host, _, session, registry, execute, context, call = self.fixture('auto_review')
                host.adapter.complete.return_value = SimpleNamespace(
                    text=json.dumps({'decision': 'deny', 'reason': reason}), has_tool_use=False, usage=None)
                result = await registry.execute_call(call, context)
                expected = ('Automatic review denied execution: ' + reason + ' Use a safer approach instead; do not bypass the denial by switching tools.' if language == 'en'
                    else '自动审阅拒绝执行：' + reason + ' 请改用更安全的方案，不得通过更换工具绕过拒绝。')
                self.assertTrue(result.is_error)
                self.assertEqual(result.display['error'], tr('python.registry.f2b86e87a36f', p0=expected))
                execute.assert_not_awaited()
                host.questions.ask.assert_not_awaited()
                messages = host.adapter.complete.await_args.args[0]
                self.assertEqual(messages[0].text, tr('owned.permissions.1d72dffc9a0f'))
                self.assertEqual(host.adapter.complete.await_args.kwargs['tools'], [])
                self.assertEqual(session.latest('note').data['reason'], reason)

    async def test_invalid_review_falls_back_to_current_language_manual_question(self):
        for language in ('en', 'zh-CN', 'en'):
            with self.subTest(language=language), current_language.set(language):
                host, _, _, registry, execute, context, call = self.fixture('auto_review')
                host.adapter.complete.return_value = SimpleNamespace(text='broken', has_tool_use=False, usage=None)
                host.questions.ask.return_value = [{'selected': [tr('python.permissions.136de7a8c46f')]}]
                result = await registry.execute_call(call, context)
                self.assertTrue(result.is_error)
                execute.assert_not_awaited()
                host.questions.ask.assert_awaited_once()
                question = host.questions.ask.await_args.kwargs['questions'][0]['question']
                self.assertTrue(question.startswith('Confirm execution ' if language == 'en' else '确认执行 '))

    async def test_audit_failure_before_or_after_append_never_admits_side_effect(self):
        from unittest.mock import patch
        for language in ('en', 'zh-CN', 'en'):
            for flow in ('once', 'temporary', 'automatic', 'hook'):
                for committed in (False, True):
                    with self.subTest(language=language, flow=flow, committed=committed), current_language.set(language):
                        host, policy, session, registry, execute, context, call = self.fixture('auto_review' if flow == 'automatic' else 'request_approval')
                        label = tr('python.permissions.4e9b6b4d7c21' if flow == 'temporary' else 'python.permissions.f9881e36e9cd')
                        host.questions.ask.return_value = [{'selected': [label]}]
                        host.adapter.complete.return_value = SimpleNamespace(text=json.dumps({'decision': 'allow', 'reason': 'Original reason'}), has_tool_use=False, usage=None)
                        if flow == 'hook':
                            host.hooks = SimpleNamespace(permission=AsyncMock(return_value={'behavior': 'allow', 'reason': 'Original reason'}))
                        append, failure, writes = session.append, OSError('Original audit 原文'), []
                        async def faulty(kind, data, **kwargs):
                            if kind == 'note' and data.get('kind') == 'permission_decision':
                                writes.append(data)
                                if committed:
                                    await append(kind, data, **kwargs)
                                raise failure
                            return await append(kind, data, **kwargs)
                        with patch.object(session, 'append', side_effect=faulty):
                            result = await registry.execute_call(call, context)
                        self.assertTrue(result.is_error)
                        execute.assert_not_awaited()
                        self.assertEqual(policy.grants, {})
                        self.assertEqual(len(writes), 1)
                        self.assertEqual(len([event for event in session.events_of_type('note') if event.data.get('kind') == 'permission_decision']), int(committed))

    async def test_revoke_audit_failure_keeps_grant_revoked(self):
        from unittest.mock import patch
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                host, policy, session, registry, execute, context, call = self.fixture('request_approval')
                host.tools = registry
                host.questions.ask.return_value = [{'selected': [tr('python.permissions.4e9b6b4d7c21')]}]
                self.assertFalse((await registry.execute_call(call, context)).is_error)
                grant_id = next(iter(policy.grants.values()))['id']
                with patch.object(session, 'append', AsyncMock(side_effect=OSError('Original revoke audit'))):
                    result = await policy.revoke_grant(session.id, grant_id)
                self.assertEqual(result['revoked'], grant_id)
                self.assertEqual(result['audit_error'], tr('ui.permissions.auditIncomplete'))
                self.assertEqual(policy.grants, {})
                execute.assert_awaited_once()
