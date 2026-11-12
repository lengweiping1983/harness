"""安装作业保存资源身份，观察语言变化不修改作业或重新执行。"""
import asyncio
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from harness.app.terminal_setup import TerminalSetupService, _failure
from harness.core.errors import HarnessError
from harness.core.language import current_language
from harness.i18n import resources, tr
from harness.tools.schema import validate
from harness.web.protocol import SCHEMAS


class TerminalSetupLanguageResourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_job_outcomes_keep_resource_or_external_original(self):
        for outcome in ('complete', 'cancelled', 'failed', 'network', 'external'):
            with self.subTest(outcome=outcome):
                service = TerminalSetupService()
                identifier = str(uuid4())
                failure = {
                    'cancelled': asyncio.CancelledError(),
                    'failed': ValueError('fixture'),
                    'network': _failure('ui.terminalSetup.runtime.network', 'terminal/install-command'),
                    'external': HarnessError('External diagnostic 原文', code='fixture/external'),
                }.get(outcome)
                command = AsyncMock(side_effect=failure)
                with current_language.set('en'), patch('harness.app.terminal_setup.os.name', 'nt'), \
                        patch('harness.app.terminal_setup.detect_terminal', return_value={'ready': False}), \
                        patch.object(service, '_command', command), \
                        patch('harness.app.terminal_setup.verify_terminal', AsyncMock()):
                    initial = await service.start({'request_id': identifier})
                    self.assertEqual(initial['message'], {'key': 'ui.terminalSetup.runtime.detecting'})
                    await service.task
                original = service.snapshot(identifier)
                for language in ('en', 'zh-CN', 'en'):
                    with current_language.set(language):
                        row = service.snapshot(identifier)
                    self.assertEqual(row, original)
                    self.assertEqual(validate(row, SCHEMAS['TerminalRuntimeInstallation']), [])
                    message = row['message']
                    if outcome == 'external':
                        self.assertEqual(message, 'External diagnostic 原文')
                    else:
                        key = 'ui.terminalSetup.runtime.' + outcome
                        self.assertEqual(message, {'key': key})
                        self.assertTrue(resources(language)[key])
                        browser = json.loads(Path(f'web/src/i18n/locales/{language}.json').read_text(encoding='utf-8'))
                        self.assertEqual(browser[key], resources(language)[key])
                command.assert_awaited_once_with(identifier)
                original['message'] = 'changed snapshot'
                self.assertNotEqual(service.snapshot(identifier)['message'], 'changed snapshot')

    async def test_command_failures_keep_resource_identity_and_cleanup_once(self):
        cases = (
            (b'Connection refused', 'network'),
            (b'No module named pip', 'pip'),
            (b'Access is denied', 'permission'),
            (b'No matching distribution', 'package'),
            (b'\xff', 'encoding'),
            (b'', 'timeout'),
        )
        for language, (output, suffix) in ((language, case) for language in ('en', 'zh-CN', 'en') for case in cases):
            with self.subTest(language=language, suffix=suffix), current_language.set(language):
                process = SimpleNamespace(
                    stdout=SimpleNamespace(read=AsyncMock(side_effect=[output, b''])),
                    stderr=SimpleNamespace(read=AsyncMock(return_value=b'')),
                    wait=AsyncMock(return_value=1, side_effect=TimeoutError() if suffix == 'timeout' else None),
                    stop_owned=AsyncMock(), close=Mock(),
                )
                with patch('harness.app.terminal_setup.spawn_owned', AsyncMock(return_value=process)):
                    with self.assertRaises(HarnessError) as caught:
                        await TerminalSetupService()._command('fixture')
                self.assertEqual(caught.exception.details['copy'], {'key': 'ui.terminalSetup.runtime.' + suffix})
                self.assertEqual(caught.exception.message, tr('ui.terminalSetup.runtime.' + suffix))
                process.stop_owned.assert_awaited_once()
                process.close.assert_called_once()
