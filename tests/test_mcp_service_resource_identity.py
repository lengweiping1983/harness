"""MCP 启动跨线程保存资源身份，外部诊断不依赖原文匹配。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from harness.app.mcp_errors import McpServiceError
from harness.app.mcp_service import McpService
from harness.core.language import current_language
from harness.i18n import resources, tr


class McpServiceResourceIdentityTests(unittest.TestCase):
    def test_start_repeat_and_stop_receipts_include_current_error_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / 'project'
            project.mkdir()
            service = McpService(root / 'state')
            try:
                with patch('harness.app.mcp_service.threading.Thread'):
                    for state in (service.start(project), service.start(project)):
                        self.assertEqual(state['status'], 'starting')
                        self.assertIn('error_copy', state)
                        self.assertIsNone(state['error_copy'])
                        json.dumps(state)
                service._thread = None
                state = service.stop()
                self.assertEqual(state['status'], 'stopped')
                self.assertIn('error_copy', state)
                self.assertIsNone(state['error_copy'])
                json.dumps(state)
            finally:
                service._thread = None
                service.stop()

    def test_background_owned_error_uses_reading_language_and_captures_start_language(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / 'project'
            project.mkdir()
            service = McpService(root / 'state')
            seen = []
            failure = McpServiceError('ui.mcpService.error.architecture', machine='OriginalMachine')

            def fail():
                seen.append(current_language.get())
                raise failure

            try:
                with patch.object(service, '_python', side_effect=fail), current_language.set('en'):
                    service.start(project)
                    service._thread.join(timeout=3)
                    self.assertFalse(service._thread.is_alive())
                self.assertEqual(seen, ['en'])
                self.assertIs(service._state['error'], failure)
                for language in ('en', 'zh-CN', 'en'):
                    with current_language.set(language), patch('harness.i18n.localize_text', side_effect=AssertionError('reverse lookup'), create=True):
                        snapshot = service.snapshot()
                        self.assertEqual(snapshot['error'], tr(failure.key, machine='OriginalMachine'))
                        self.assertEqual(snapshot['error_copy'], {'key': failure.key, 'params': {'machine': 'OriginalMachine'}})
                        self.assertEqual(snapshot['status'], 'failed')
                        json.dumps(snapshot)
                self.assertIs(service._state['error'], failure)
                self.assertFalse(service.home.exists())
            finally:
                service.stop()

    def test_background_external_error_equal_to_product_copy_remains_original(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / 'project'
            project.mkdir()
            service = McpService(root / 'state')
            original = resources('zh-CN')['ui.mcpService.error.startTimeout']
            try:
                with patch.object(service, '_python', side_effect=RuntimeError(original)):
                    service.start(project)
                    service._thread.join(timeout=3)
                    self.assertFalse(service._thread.is_alive())
                for language in ('en', 'zh-CN', 'en'):
                    with current_language.set(language):
                        self.assertEqual(service.snapshot()['error'], original)
                        self.assertIsNone(service.snapshot()['error_copy'])
                self.assertFalse(service.home.exists())
            finally:
                service.stop()

    def test_unexpected_exit_saves_key_and_cleans_owned_processes_once(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        manager = McpService(directory.name)
        manager._state.update(status='running', local_url='old', public_url='old')
        manager._cleanup = Mock()
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                snapshot = McpService.snapshot(manager)
                self.assertEqual(snapshot['error'], tr('ui.mcpService.error.exited'))
                self.assertEqual((snapshot['local_url'], snapshot['public_url']), ('', ''))
                self.assertEqual(snapshot['status'], 'failed')
        manager._cleanup.assert_called_once()
        self.assertTrue(manager._cancel.is_set())

    def test_thread_start_failure_does_not_leave_unstarted_service_busy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / 'project'
            project.mkdir()
            service = McpService(root / 'state')
            try:
                with patch('harness.app.mcp_service.threading.Thread') as thread:
                    thread.return_value.start.side_effect = RuntimeError('fixture thread failure')
                    with self.assertRaisesRegex(RuntimeError, 'fixture thread failure'):
                        service.start(project)
                self.assertEqual(service.snapshot()['status'], 'failed')
                self.assertIsNone(service._thread)
                self.assertTrue(service._cancel.is_set())
                self.assertFalse(service.home.exists())
            finally:
                service.stop()

    def test_startup_and_cleanup_errors_remain_in_state_without_escaping_thread(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root/'project';project.mkdir()
            service = McpService(root/'state')
            primary = McpServiceError('ui.mcpService.error.dependencies')
            cleanup = OSError('Original cleanup diagnostic 原文')
            try:
                with patch.object(service, '_python', side_effect=primary), patch.object(service, '_cleanup', side_effect=cleanup), \
                     patch('threading.excepthook') as hook:
                    service.start(project)
                    service._thread.join(timeout=3)
                    self.assertFalse(service._thread.is_alive())
                    hook.assert_not_called()
                error = service._state['error']
                self.assertEqual(error.exceptions, (primary, cleanup))
                self.assertEqual(service.snapshot()['error_details'], [{'key':primary.key,'params':{}}, str(cleanup)])
                for language in ('en', 'zh-CN', 'en'):
                    with current_language.set(language):
                        snapshot = service.snapshot()
                        self.assertEqual(snapshot['status'], 'failed')
                        self.assertIn(tr(primary.key), snapshot['error'])
                        self.assertIn(str(cleanup), snapshot['error'])
                        self.assertEqual((snapshot['local_url'], snapshot['public_url']), ('', ''))
                        json.dumps(snapshot)
            finally:
                service.stop()

    def test_exit_callback_is_registered_only_for_started_ownership_and_removed_after_stop(self):
        with tempfile.TemporaryDirectory() as directory, patch('harness.app.mcp_service.atexit.register') as register, \
             patch('harness.app.mcp_service.atexit.unregister') as unregister:
            root = Path(directory)
            project = root/'project';project.mkdir()
            service = McpService(root/'state')
            register.assert_not_called()
            for _ in range(2):
                with patch.object(service, '_python', side_effect=McpServiceError('ui.mcpService.error.dependencies')):
                    service.start(project)
                    service._thread.join(timeout=3)
                self.assertTrue(service._exit_registered)
                service.stop()
                self.assertFalse(service._exit_registered)
            self.assertEqual(register.call_count, 2)
            self.assertEqual(unregister.call_count, 2)
            service.stop()
            self.assertEqual(unregister.call_count, 2)
