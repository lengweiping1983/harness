"""Hook 声明和决策双语反馈不改变授权、输入或外部正文。"""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from harness.app.hooks import ProjectHooks, validate_document, matches
from harness.core.language import current_language


def output(data):
    return {'stdout': json.dumps(data), 'stderr': '', 'exit_code': 0, 'timed_out': False, 'stdout_dropped': 0}


class HookLanguageTests(unittest.IsolatedAsyncioTestCase):
    def assert_language(self, text, language):
        self.assertEqual(any('\u4e00' <= c <= '\u9fff' for c in text), language == 'zh-CN', text)

    def test_invalid_declarations_and_regex_limit_do_not_authorize_handlers(self):
        cases = [('PreToolUse', {'type': 'mcp_tool', 'server': '', 'tool': 'tool'}),
            ('SessionEnd', {'type': 'mcp_tool', 'server': 'server', 'tool': 'tool'}),
            ('PreToolUse', {'type': 'command', 'command': 'fixture', 'additionalContextLimit': True}),
            ('PreToolUse', {'type': 'command', 'command': 'fixture', 'timeout': 10 ** 400})]
        for language in ('en', 'zh-CN', 'en'):
            for event, handler in cases:
                document = {'hooks': {event: [{'hooks': [handler]}]}}
                original = json.dumps(document)
                with current_language.set(language), self.assertRaises(ValueError) as caught:
                    validate_document(document)
                self.assert_language(str(caught.exception), language)
                self.assertEqual(json.dumps(document), original)
            with current_language.set(language), patch('harness.app.hooks.regex.search', side_effect=TimeoutError), self.assertRaises(ValueError) as caught:
                matches('fixture', 'tool')
            self.assert_language(str(caught.exception), language)
            self.assertIsInstance(caught.exception.__cause__, TimeoutError)

    def test_output_decisions_preserve_machine_fields_and_user_reason(self):
        cases = [('PostToolUse', {'continue': 'invalid'}, 'failed'),
            ('PreToolUse', {'continue': False}, 'failed'),
            ('PermissionRequest', {'hookSpecificOutput': {'decision': {'behavior': 'unknown'}}}, 'failed'),
            ('PermissionRequest', {'hookSpecificOutput': {'decision': {'behavior': 'deny'}}}, 'blocked'),
            ('PreToolUse', {'hookSpecificOutput': {'permissionDecision': 'allow'}}, 'failed'),
            ('PreToolUse', {'hookSpecificOutput': {'updatedInput': {}}}, 'failed'),
            ('PostToolUse', {'continue': False}, 'stopped')]
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                for event, data, status in cases:
                    record = output(data)
                    original = dict(record)
                    decision = ProjectHooks._interpret(event, record)
                    self.assertEqual(decision['status'], status)
                    self.assert_language(decision['reason'], language)
                    self.assertEqual(record, original)
                explicit = ProjectHooks._interpret('PermissionRequest', output({'hookSpecificOutput': {'decision': {'behavior': 'deny', 'message': 'Original 原文'}}}))
                self.assertEqual(explicit['reason'], 'Original 原文')
                allowed = ProjectHooks._interpret('PreToolUse', output({'hookSpecificOutput': {'permissionDecision': 'allow', 'updatedInput': {'path': 'original.txt'}}}))
                self.assertEqual(allowed['updated_input'], {'path': 'original.txt'})
                self.assertEqual(allowed['status'], 'passed')

    async def test_unavailable_mcp_is_skipped_without_calling_remote_tool(self):
        hooks = ProjectHooks.__new__(ProjectHooks)
        supervisor = Mock()
        hooks.harness = SimpleNamespace(mcp=SimpleNamespace(get=lambda _: supervisor))
        row = {'server': 'fixture', 'tool': 'tool', 'input': {}, 'timeout': 1}
        for language in ('en', 'zh-CN', 'en'):
            for connected in (False, True):
                supervisor.connected = connected
                supervisor.remote_tool.return_value = None
                with current_language.set(language):
                    result = await hooks._mcp_tool(row, {}, SimpleNamespace())
                self.assertTrue(result['skipped'])
                self.assert_language(result['stderr'], language)
                self.assertEqual(result['stdout'], '')
                supervisor.call.assert_not_called()
