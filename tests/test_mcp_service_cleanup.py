"""托管 MCP 逐项收尾，失败句柄保留供显式重试。"""
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import Mock

from harness.app.mcp_service import McpService


def process(*, failure=None):
    """构造不会启动系统进程的可控句柄。"""
    handle = Mock()
    handle.poll.return_value = None
    handle.wait.side_effect = failure
    return handle


class McpServiceCleanupTests(unittest.TestCase):
    def manager(self, main, tunnel):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        service = McpService(directory.name)
        service._process, service._tunnel = main, tunnel
        service._state.update(status='running', local_url='old', public_url='old')
        return service

    def test_failed_main_still_closes_tunnel_and_retains_only_failed_handle(self):
        failure = OSError('Original main wait failure 原文')
        main, tunnel = process(failure=failure), process()
        service = self.manager(main, tunnel)
        with self.assertRaises(OSError) as caught:
            service._cleanup()
        self.assertIs(caught.exception, failure)
        tunnel.terminate.assert_called_once()
        tunnel.wait.assert_called_once_with(timeout=8)
        tunnel.stdout.close.assert_called_once()
        self.assertIs(service._process, main)
        self.assertIsNone(service._tunnel)
        main.stdout.close.assert_not_called()
        main.wait.side_effect = None
        main.poll.return_value = 0
        service._cleanup()
        self.assertIsNone(service._process)
        main.stdin.close.assert_called_once()
        tunnel.terminate.assert_called_once()

    def test_multiple_failures_preserve_original_exceptions(self):
        first, second = OSError('main'), PermissionError('tunnel')
        main, tunnel = process(failure=first), process(failure=second)
        service = self.manager(main, tunnel)
        with self.assertRaises(ExceptionGroup) as caught:
            service._cleanup()
        self.assertEqual(caught.exception.exceptions, (first, second))
        self.assertIs(service._process, main)
        self.assertIs(service._tunnel, tunnel)

    def test_stop_failure_cannot_report_stopped_or_forget_remaining_process(self):
        failure = subprocess.TimeoutExpired('original process', 5)
        main, tunnel = process(failure=failure), process()
        service = self.manager(main, tunnel)
        with self.assertRaises(subprocess.TimeoutExpired) as caught:
            service.stop()
        self.assertIs(caught.exception, failure)
        self.assertNotEqual(service._state['status'], 'stopped')
        self.assertEqual(service._state['local_url'], '')
        self.assertIs(service._process, main)
        self.assertIsNone(service._tunnel)
        self.assertTrue(service._cancel.is_set())

    def test_stop_still_joins_startup_thread_after_process_cleanup_failure(self):
        failure = OSError('Original process cleanup')
        service = self.manager(process(failure=failure), process())
        thread = Mock()
        thread.is_alive.return_value = False
        service._thread = thread
        with self.assertRaises(OSError) as caught:
            service.stop()
        self.assertIs(caught.exception, failure)
        thread.join.assert_called_once_with(timeout=3)
        self.assertEqual(service._state['status'], 'stopping')

    def test_stop_preserves_cleanup_and_thread_join_errors(self):
        first, second = OSError('Original process cleanup'), RuntimeError('Original join failure')
        service = self.manager(process(failure=first), process())
        thread = Mock()
        thread.join.side_effect = second
        thread.is_alive.return_value = True
        service._thread = thread
        with self.assertRaises(ExceptionGroup) as caught:
            service.stop()
        self.assertEqual(caught.exception.exceptions, (first, second))
        self.assertEqual(service._state['status'], 'stopping')

    def test_cleanup_waits_for_owned_drain_and_retains_live_thread_for_retry(self):
        from harness.app.mcp_errors import McpServiceError
        service = self.manager(None, None)
        drain = Mock()
        drain.is_alive.return_value = True
        service._drain_thread = drain
        with self.assertRaises(McpServiceError) as caught:
            service._cleanup()
        self.assertEqual(caught.exception.key, 'ui.mcpService.error.drainAlive')
        drain.join.assert_called_once_with(timeout=3)
        self.assertIs(service._drain_thread, drain)
        drain.is_alive.return_value = False
        service._cleanup()
        self.assertIsNone(service._drain_thread)

    def test_cleanup_joins_real_output_thread_before_releasing_ownership(self):
        service = self.manager(None, None)
        entered, release = threading.Event(), threading.Event()
        def output():
            entered.set()
            release.wait(2)
        drain = threading.Thread(target=output)
        drain.start()
        self.assertTrue(entered.wait(1))
        service._drain_thread = drain
        release.set()
        service._cleanup()
        self.assertFalse(drain.is_alive())
        self.assertIsNone(service._drain_thread)

    def test_failed_cleanup_ownership_blocks_new_start_before_project_access(self):
        from harness.app.mcp_errors import McpServiceError
        service = self.manager(None, None)
        service._state['status'] = 'failed'
        service._drain_thread = Mock()
        with self.assertRaises(McpServiceError):
            service.start('/missing-project')
        self.assertEqual(service._state['status'], 'failed')
