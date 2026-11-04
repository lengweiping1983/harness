"""审批原因必须来自实际准入分支，不能改变执行与授权边界。"""
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from harness.core.language import current_language
from harness.core.events import EventBus
from harness.app.hooks import ProjectHooks
from harness.i18n import tr
from harness.llm.types import LlmCallConfig, ToolSchema, ToolUseBlock
from harness.tools.registry import ToolDefinition, ToolRegistry
from tests import test_permission_copy_templates as fixtures


def approval_fixture(policy_name='request_approval', *, name='OriginalTool', effect='read', args=None, **flags):
    """沿真实注册表和权限钩子执行，异步应答只选择拒绝。"""
    host, policy, session, _, execute, context, _ = fixtures.PermissionCopyTemplateTests().fixture(policy_name)
    registry = ToolRegistry()
    registry.add_guard(policy.guard)
    registry.register(ToolDefinition(ToolSchema(name=name, description='Original description 原文',
        parameters={'type': 'object'}), execute, effect=effect, **flags))
    host.questions.ask.side_effect = lambda **_: [{'selected': [tr('python.permissions.136de7a8c46f')]}]
    call = ToolUseBlock(id='original-call', name=name, input=args or {})
    return host, policy, session, registry, execute, context, call


class PermissionApprovalReasonTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_admission_branches_produce_unique_reasons(self):
        with tempfile.TemporaryDirectory() as directory:
            outside = str(Path(directory).parent / 'outside.txt')
            cases = [
                ({'effect': 'read'}, []),
                ({'approval_required': True}, ['tool_requires_approval']),
                ({'dangerous': True}, ['dangerous_tool']),
                ({'effect': 'external_write'}, ['external_write']),
                ({'effect': 'execute'}, ['command_execution']),
                ({'name': 'bash', 'effect': 'execute'}, ['command_execution']),
                ({'name': 'bash'}, ['command_execution']),
                ({'name': 'mcp__fixture', 'effect': 'read'}, []),
                ({'name': 'mcp__fixture', 'effect': 'workspace_write'}, ['workspace_write', 'connector_operation']),
                ({'name': 'mcp__fixture', 'effect': 'unknown'}, ['connector_operation', 'unknown_effect']),
                ({'name': 'browser', 'args': {'action': 'click'}}, ['browser_interaction', 'network_access']),
                ({'name': 'browser', 'args': {'action': 'navigate'}}, ['network_access']),
                ({'name': 'web_search'}, ['network_access']),
                ({'effect': 'unknown'}, ['unknown_effect']),
                ({'name': 'present', 'effect': 'unknown'}, []),
                ({'name': 'write', 'effect': 'workspace_write', 'args': {'path': outside}}, ['workspace_write', 'outside_working_directory']),
                ({'name': 'read', 'effect': 'read', 'args': {'path': outside}}, ['outside_working_directory']),
                ({'policy_name': 'read_only', 'name': 'read', 'effect': 'read', 'args': {'path': outside}}, ['outside_working_directory', 'read_only_policy']),
                ({'name': 'edit', 'effect': 'workspace_write', 'args': {'path': 'inside.txt'}}, ['workspace_write']),
                ({'policy_name': 'read_only', 'effect': 'workspace_write'}, ['read_only_policy']),
                ({'policy_name': 'read_only', 'dangerous': True}, ['dangerous_tool', 'read_only_policy']),
                ({'policy_name': 'full_access', 'dangerous': True, 'approval_required': True, 'effect': 'execute'}, []),
            ]
            for language in ('en', 'zh-CN', 'en'):
                for settings, reasons in cases:
                    with self.subTest(language=language, settings=settings), current_language.set(language):
                        host, policy, session, registry, execute, context, call = approval_fixture(**settings)
                        context = replace(context, cwd=directory)
                        result = await registry.execute_call(call, context)
                        self.assertEqual(result.is_error, bool(reasons))
                        if reasons:
                            execute.assert_not_awaited()
                            operation = host.questions.ask.await_args.kwargs['questions'][0]['operation']
                            expected = {'policy': settings.get('policy_name', 'request_approval'), 'reasons': reasons}
                            self.assertEqual(operation['approval'], expected)
                            self.assertEqual(operation['cwd'], directory)
                            audit = session.latest('note').data['approval']
                            self.assertEqual(audit['policy'], expected['policy'])
                            self.assertEqual(list(audit['reasons']), reasons)
                        else:
                            host.questions.ask.assert_not_awaited()
                            execute.assert_awaited_once()
                        self.assertEqual(policy.grants, {})

    async def test_automatic_review_reports_the_actual_fallback(self):
        for language in ('en', 'zh-CN', 'en'):
            for outcome in ('confirmation_requested', 'input_too_large', 'budget_unavailable', 'unavailable'):
                with self.subTest(language=language, outcome=outcome), current_language.set(language):
                    args = {'text': 'x' * 25000} if outcome == 'input_too_large' else {}
                    host, _, session, registry, execute, context, call = approval_fixture('auto_review', effect='execute', args=args)
                    host.adapter.complete.return_value = SimpleNamespace(text=json.dumps({'decision': 'ask', 'reason': 'Original reviewer 原文'}), has_tool_use=False, usage=None)
                    if outcome == 'unavailable':
                        host.adapter.complete.return_value.text = 'invalid'
                    if outcome == 'budget_unavailable':
                        host._agents[session.id] = SimpleNamespace(config=LlmCallConfig(model='fixture'),
                            budget=SimpleNamespace(acquire=AsyncMock(return_value=None)), status=SimpleNamespace(status='tool'))
                    result = await registry.execute_call(call, context)
                    self.assertTrue(result.is_error)
                    execute.assert_not_awaited()
                    operation = host.questions.ask.await_args.kwargs['questions'][0]['operation']
                    self.assertEqual(operation['approval'], {'policy': 'auto_review', 'reasons': ['command_execution'], 'review': outcome})
                    if outcome in ('input_too_large', 'budget_unavailable'):
                        host.adapter.complete.assert_not_awaited()
                    else:
                        host.adapter.complete.assert_awaited_once()
                    if outcome == 'confirmation_requested':
                        self.assertEqual(session.events_of_type('note')[0].data['reason'], 'Original reviewer 原文')

    async def test_real_hook_dispatch_changes_the_approval_target_without_bypassing_the_guard(self):
        for behavior in (None, 'allow', 'deny', 'ask'):
            with self.subTest(behavior=behavior), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                work = root / 'work'
                work.mkdir()
                outside = str(root / 'outside.txt')
                host, policy, _, original, execute, context, call = approval_fixture(
                    name='write', effect='workspace_write', args={'path': 'inside.txt'})
                host.config.home, host.config.cwd = root, str(work)
                host.bus = EventBus()
                host.plan = SimpleNamespace(is_active_session_id=lambda _: False)
                host.permissions = policy
                registry = ToolRegistry()
                registry.register(original.get('write'))
                host.tools = registry
                hooks = ProjectHooks(host)
                host.hooks = hooks
                document = {'hooks': {'PreToolUse': [{'matcher': '*', 'hooks': [{'type': 'command', 'command': 'fixture'}]}]}}
                if behavior is not None:
                    document['hooks']['PermissionRequest'] = [{'matcher': '*', 'hooks': [{'type': 'command', 'command': 'fixture'}]}]
                state = hooks.save(work, document, 'missing')
                hooks.set_trust(work, state['fingerprint'], True)

                async def command(row, *_args, **_kwargs):
                    data = {'permissionDecision': 'allow', 'updatedInput': {'path': outside}} if row['event'] == 'PreToolUse' else {'decision': {'behavior': behavior}}
                    return {'stdout': json.dumps({'hookSpecificOutput': data}), 'stderr': '', 'exit_code': 0,
                            'timed_out': False, 'stdout_dropped': 0, 'stderr_dropped': 0}

                hooks._command = AsyncMock(side_effect=command)
                registry.add_guard(hooks.before)
                registry.add_guard(policy.guard)
                result = await registry.execute_call(call, replace(context, cwd=str(work)))
                self.assertEqual(call.input['path'], 'inside.txt')
                if behavior is None:
                    self.assertTrue(result.is_error)
                    operation = host.questions.ask.await_args.kwargs['questions'][0]['operation']
                    self.assertEqual(operation['approval']['reasons'], ['workspace_write', 'outside_working_directory'])
                    self.assertIn(json.dumps({'path': outside}, ensure_ascii=False), host.questions.ask.await_args.kwargs['questions'][0]['question'])
                    execute.assert_not_awaited()
                else:
                    host.questions.ask.assert_not_awaited()
                    self.assertEqual(result.is_error, behavior != 'allow')
                    self.assertEqual(execute.await_count, int(behavior == 'allow'))
                    if behavior == 'allow':
                        self.assertEqual(execute.await_args.args[0]['path'], outside)

    async def test_workspace_write_requires_explicit_allow_once(self):
        host, _, _, registry, execute, context, call = approval_fixture(
            name='write', effect='workspace_write', args={'path': 'inside.txt'})
        host.questions.ask.side_effect = None
        host.questions.ask.return_value = [{'selected_ids': ['allow-once'], 'selected': [tr('python.permissions.f9881e36e9cd')]}]
        result = await registry.execute_call(call, context)
        self.assertFalse(result.is_error)
        execute.assert_awaited_once()
