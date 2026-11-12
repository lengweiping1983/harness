"""Web收尾逐项尝试，保留原始错误和仍在运行的循环。"""
import asyncio
import unittest
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from harness.core.language import current_language
from harness.i18n import tr
from harness.web.server import stop


class WebShutdownFailureTests(unittest.TestCase):
    def fixture(self, failures):
        names = ('mcp', 'shutdown', 'socket', 'bridge', 'hub')
        calls = []
        def action(name):
            def invoke():
                calls.append(name)
                if name in failures:
                    raise failures[name]
            return invoke
        context = SimpleNamespace(mcp_service=SimpleNamespace(stop=action('mcp')),
            stop_event_bridge=action('bridge'), hub=SimpleNamespace(close_all=action('hub')))
        server = SimpleNamespace(_harness_ctx=context, shutdown=action('shutdown'), server_close=action('socket'))
        return server, calls, names

    def test_each_failure_still_attempts_remaining_resources(self):
        for failed in ('mcp', 'shutdown', 'socket', 'bridge', 'hub'):
            failure = OSError('Original ' + failed)
            server, calls, expected = self.fixture({failed:failure})
            with self.subTest(failed=failed), self.assertRaises(OSError) as caught:
                stop(server)
            self.assertIs(caught.exception, failure)
            self.assertEqual(calls, list(expected))
            self.assertTrue(server._harness_ctx.shutting_down)

    def test_multiple_failures_keep_cancel_and_original_error_objects(self):
        cancelled = asyncio.CancelledError('Original cancellation')
        failure = OSError('Original socket failure')
        server, calls, expected = self.fixture({'mcp':cancelled, 'socket':failure})
        for language in ('en', 'zh-CN', 'en'):
            calls.clear()
            with current_language.set(language), self.assertRaises(BaseExceptionGroup) as caught:
                stop(server)
            self.assertEqual(caught.exception.exceptions, (cancelled, failure))
            with current_language.set(language):
                self.assertEqual(caught.exception.message, tr('cleanup.failed'))
            self.assertEqual(calls, list(expected))

    def test_success_still_closes_each_resource_once(self):
        server, calls, expected = self.fixture({})
        stop(server)
        self.assertEqual(calls, list(expected))

    def test_live_owned_loop_is_not_drained_after_thread_join_timeout(self):
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                server, calls, expected = self.fixture({})
                loop = Mock()
                loop.is_closed.return_value = False
                loop.is_running.return_value = True
                thread = Mock()
                thread.is_alive.return_value = True
                server._harness_loop = loop
                server._harness_loop_thread = thread
                with patch('harness.web.server._drain_loop') as drain, self.assertRaises(RuntimeError) as caught:
                    stop(server, timeout=0.01)
                self.assertEqual(str(caught.exception), tr('web.shutdown.loop_alive'))
                thread.join.assert_called_once_with(timeout=0.01)
                drain.assert_not_called()
                self.assertIs(server._harness_loop, loop)
                self.assertEqual(calls[:4], list(expected[:4]))

    def test_owned_loop_closes_services_after_mcp_failure(self):
        loop = asyncio.new_event_loop()
        started = threading.Event()
        loop.call_soon(started.set)
        thread = threading.Thread(target=loop.run_forever)
        thread.start()
        failure = OSError('Original MCP failure')
        server, calls, expected = self.fixture({'mcp':failure})
        services = [SimpleNamespace(close=AsyncMock()) for _ in range(4)]
        server._harness_ctx.harness = SimpleNamespace(_superagent_workbench=services[0], runtime=services[1], automations=services[2], browser=services[3])
        server._harness_loop = loop
        server._harness_loop_thread = thread
        try:
            self.assertTrue(started.wait(2))
            with self.assertRaises(OSError) as caught:
                stop(server)
            self.assertIs(caught.exception, failure)
            self.assertEqual(calls, list(expected))
            for service in services:
                service.close.assert_awaited_once()
            self.assertFalse(thread.is_alive())
            self.assertTrue(loop.is_closed())
        finally:
            if loop.is_running():
                loop.call_soon_threadsafe(loop.stop)
            thread.join(2)
            if not loop.is_closed() and not loop.is_running():
                loop.close()
