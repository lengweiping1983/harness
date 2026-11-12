"""Hook失败摘要使用完整双语模板，外部正文不递归解释。"""
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from harness.app.hooks import ProjectHooks
from harness.core.errors import HarnessError
from harness.core.language import current_language
from harness.i18n import tr


class HookFailureTemplateTests(unittest.IsolatedAsyncioTestCase):
    async def test_dispatch_preserves_handler_label_and_original_failure(self):
        row = {'id': 'fixture', 'event': 'PreToolUse', 'enabled': True, 'matcher': '*',
               'label': 'Original Hook {x}', 'command': 'unused', 'type': 'command'}
        state = {'error': '', 'trusted': True, 'fingerprint': 'fixture', 'rows': [row]}
        hooks = ProjectHooks.__new__(ProjectHooks)
        hooks.harness = SimpleNamespace()
        hooks.project_root = Mock(return_value=Path('/fixture'))
        hooks.snapshot = Mock(return_value=state)
        hooks._source_snapshot = Mock(return_value=state)
        hooks._candidates = Mock(return_value=[row])
        hooks._restricted = Mock(return_value=False)
        hooks._record = Mock()
        hooks._journal = AsyncMock()
        ctx = SimpleNamespace(cwd='/fixture', session_id='owner', call_id='call', turn=1, step=1)
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                for mode in ('blocked', 'failed', 'exception'):
                    reason = 'Original diagnostic 原文 {error}'
                    hooks._command = AsyncMock(return_value={'stdout': '', 'stderr': reason,
                        'exit_code': 2 if mode == 'blocked' else 1, 'timed_out': False,
                        'stdout_dropped': 0, 'stderr_dropped': 0})
                    if mode == 'exception':
                        hooks._command.side_effect = RuntimeError(reason)
                    result = await hooks._dispatch('PreToolUse', 'FixtureTool', {}, ctx)
                    expected = tr('hook.feedback.named_failure', label=row['label'], reason=reason)
                    actual = result['denial'] if mode == 'blocked' else result['error']
                    self.assertEqual(actual, expected)
                    self.assertEqual(expected, row['label'] + (': ' if language == 'en' else '：') + reason)
                    self.assertEqual(result['blocks'], [expected] if mode == 'blocked' else [])

    async def test_stop_failure_is_a_complete_template(self):
        hooks = ProjectHooks.__new__(ProjectHooks)
        hooks.harness = SimpleNamespace(bus=Mock(), jobs=Mock())
        hooks._restricted = Mock(return_value=False)
        hooks._dispatch = AsyncMock(return_value={'error': 'Original diagnostic {x}'})
        agent = SimpleNamespace(recovery_read_only=False, task_read_only=False)
        session = SimpleNamespace(id='owner', header=SimpleNamespace(cwd='/fixture'))
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                with self.assertRaises(HarnessError) as error:
                    await hooks.stop(agent, session, 1, 1, False, 'Original reply')
                self.assertEqual(error.exception.code, 'hooks/completion-failed')
                self.assertEqual(error.exception.message, tr('python.hooks.85bfaf8afdbf', error='Original diagnostic {x}'))
