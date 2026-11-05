"""权限边界不会把敏感参数交给审批展示或自动审阅。"""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from harness.app.permissions import _redact_sensitive
from harness.core.language import current_language
from harness.session.events import AppendOp
from harness.llm.types import ToolSchema
from harness.tools.builtin.todo_state import empty_snapshot
from harness.tools.builtin.todo_tools import TodoStore, build_todo_tools
from harness.tools.registry import ToolDefinition


def permission_fixture(policy):
    """在测试函数内加载共享夹具，避免 unittest 把夹具类重复收集。"""
    from tests.test_permission_copy_templates import PermissionCopyTemplateTests
    return PermissionCopyTemplateTests().fixture(policy)


class PermissionSensitiveTests(unittest.IsolatedAsyncioTestCase):
    async def test_redaction_masks_nested_fields_and_common_bearer_literals(self):
        value = _redact_sensitive({
            'api_key': 'fixture-api-key',
            'nested': {'refresh_token': 'fixture-refresh', 'text': 'Authorization: Bearer fixture-bearer'},
        })
        encoded = json.dumps(value, ensure_ascii=False)
        self.assertNotIn('fixture-api-key', encoded)
        self.assertNotIn('fixture-refresh', encoded)
        self.assertNotIn('fixture-bearer', encoded)
        self.assertEqual(value['api_key'], '[redacted]')
        for text in (
            '{"api_key":"fixture-json-secret"}',
            'password = "fixture secret with spaces"',
            'Authorization: Basic fixture-basic-secret',
            'sk-fixtureabcdefghijklmnop',
            'ghp_fixtureabcdefghijklmnop',
            'AKIA1234567890ABCDEF',
            'eyJfixture12345678.fixture.fixture',
            '-----BEGIN PRIVATE KEY-----\\nfixture\\n-----END PRIVATE KEY-----',
            'AWS_SHARED_CREDENTIALS_FILE=/host/aws',
            'export GIT_SSH_COMMAND="ssh -i /host/key"',
        ):
            masked = _redact_sensitive(text)
            self.assertNotIn('fixture', masked)
            self.assertNotIn('1234567890ABCDEF', masked)

    async def test_full_access_keeps_sensitive_path_boundary(self):
        host, policy, session, _, _, context, _ = permission_fixture('full_access')
        tool = ToolDefinition(ToolSchema(name='read', description='read', parameters={'type': 'object'}),
                              AsyncMock(), effect='read')
        with current_language.set('zh-CN'):
            reason = await policy.guard(tool, {'path': '.env'}, context)
        self.assertEqual(reason, '此操作引用了凭据或秘密，当前权限边界不允许继续。')

    async def test_full_access_scans_nested_sensitive_arguments(self):
        host, policy, session, _, _, context, _ = permission_fixture('full_access')
        tool = ToolDefinition(ToolSchema(name='browser', description='browser', parameters={'type': 'object'}),
                              AsyncMock(), effect='external_write')
        with current_language.set('zh-CN'):
            reason = await policy.guard(tool, {'headers': [{'Authorization': 'Bearer fixture-nested-secret'}]}, context)
        self.assertEqual(reason, '此操作引用了凭据或秘密，当前权限边界不允许继续。')

    async def test_full_access_allows_public_todo_snapshot_arguments(self):
        for language in ('en', 'zh-CN', 'en'):
            with self.subTest(language=language), current_language.set(language):
                host, policy, session, _, _, context, _ = permission_fixture('full_access')
                token = empty_snapshot(session.id).token
                tools = build_todo_tools(TodoStore(session_of=host.get_session))
                for tool in tools:
                    with self.subTest(tool=tool.schema.name):
                        self.assertIsNone(await policy.guard(tool, {'snapshot_token': token}, context))

    async def test_snapshot_exception_preserves_sensitive_values_and_other_fields(self):
        host, policy, session, _, _, context, _ = permission_fixture('full_access')
        token = empty_snapshot(session.id).token
        tools = build_todo_tools(TodoStore(session_of=host.get_session))
        for tool in tools:
            for arguments in (
                *({'snapshot_token': token, key: 'opaque-fixture-value'}
                  for key in ('token', 'access_token', 'refresh_token', 'api_key', 'password')),
                {'snapshot_token': {'access_token': 'opaque-fixture-value'}},
                {'snapshot_token': ['.env']},
                {'snapshot_token': 'Bearer fixture-secret'},
                {'snapshot_token': 'sk-fixtureabcdefghijklmnop'},
                {'snapshot_token': token, 'nested': {'snapshot_token': token}},
                {'snapshot_token': token, 'todos': [{'content': 'api_key=fixture-secret'}]},
            ):
                with self.subTest(tool=tool.schema.name, arguments=arguments), current_language.set('zh-CN'):
                    self.assertEqual(await policy.guard(tool, arguments, context),
                                     '此操作引用了凭据或秘密，当前权限边界不允许继续。')
        other = ToolDefinition(ToolSchema(name='browser', description='browser', parameters={'type': 'object'}),
                               AsyncMock(), effect='external_write')
        with current_language.set('zh-CN'):
            self.assertEqual(await policy.guard(other, {'snapshot_token': token}, context),
                             '此操作引用了凭据或秘密，当前权限边界不允许继续。')
        host.config.enable_interaction = True
        self.assertIsNone(await policy.external_agent_permission(context, 'fixture-agent', {
            'toolCall': {'toolCallId': 'snapshot', 'rawInput': {'snapshot_token': token}},
            'options': [{'optionId': 'allow', 'kind': 'allow_once', 'name': 'Allow'}],
        }))
        self.assertTrue(session.latest('note').data['sensitive'])
        host.questions.ask.assert_not_awaited()

    async def test_external_agent_full_access_does_not_auto_allow_sensitive_call(self):
        host, policy, session, _, _, context, _ = permission_fixture('full_access')
        host.config.enable_interaction = True
        result = await policy.external_agent_permission(context, 'fixture-agent', {
            'toolCall': {'toolCallId': 'call-sensitive', 'title': 'Read .env',
                         'rawInput': {'path': '.env'}},
            'options': [{'optionId': 'allow', 'kind': 'allow_once', 'name': 'Allow'},
                        {'optionId': 'deny', 'kind': 'reject_once', 'name': 'Deny'}],
        })
        self.assertIsNone(result)
        note = session.latest('note').data
        self.assertTrue(note['sensitive'])
        self.assertIsNone(note['option_id'])
        host.questions.ask.assert_not_awaited()

    async def test_auto_review_payload_redacts_user_intent_and_arguments(self):
        host, policy, session, _, _, context, _ = permission_fixture('auto_review')
        await session.append('user/message', {'text': '请使用 api_key=fixture-user-secret 完成请求', 'source': 'user'}, surface_op=AppendOp())
        host.adapter.complete.return_value = SimpleNamespace(
            text=json.dumps({'decision': 'ask', 'reason': '需要人工确认'}), has_tool_use=False, usage=None)
        tool = SimpleNamespace(schema=SimpleNamespace(name='bash'), effect='execute', dangerous=False,
                               approval_required=False)
        with current_language.set('zh-CN'):
            await policy._review(tool, {'command': 'curl -H "Authorization: Bearer fixture-arg-secret"'},
                                 context, session.id, None)
        payload = host.adapter.complete.await_args.args[0][1].text
        self.assertNotIn('fixture-user-secret', payload)
        self.assertNotIn('fixture-arg-secret', payload)
        self.assertIn('[redacted]', payload)
