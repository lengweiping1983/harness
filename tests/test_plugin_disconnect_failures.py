"""插件断开逐项收尾，单台停用不能吞掉未知进程清理失败。"""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from harness.connectors.manager import ConnectorManager
from harness.connectors.store import ConnectorRecord
from harness.core.language import current_language
from harness.i18n import tr
from harness.plugins.provider import connector_name
from harness.plugins.service import PackageService
from harness.tools.builtin.mcp_client import McpError
from harness.tools.processes import ProcessCleanupError


class PluginDisconnectFailureTests(unittest.IsolatedAsyncioTestCase):
    def service(self, failures):
        attempted = []

        async def disconnect(name):
            attempted.append(name)
            if name in failures:
                raise failures[name]

        service = PackageService.__new__(PackageService)
        service.store = SimpleNamespace(entries=lambda: [(SimpleNamespace(name='fixture', servers={'one': {}, 'two': {}, 'three': {}}), {})])
        service.harness = SimpleNamespace(connectors=SimpleNamespace(disconnect_runtime=disconnect))
        return service, attempted

    async def test_first_failure_does_not_skip_other_connections_and_retains_identity(self):
        failed = connector_name('fixture', 'one')
        failure = ProcessCleanupError('Original cleanup diagnostic 原文', pid=123)
        service, attempted = self.service({failed: failure})
        with self.assertRaises(ProcessCleanupError) as caught:
            await service._disconnect('fixture')
        self.assertIs(caught.exception, failure)
        self.assertEqual(attempted, [connector_name('fixture', name) for name in ('one', 'two', 'three')])

    async def test_replaced_manager_does_not_take_over_old_cleanup(self):
        service, _ = self.service({})
        attempted = []
        replacement = SimpleNamespace(disconnect_runtime=AsyncMock())

        async def disconnect(name):
            attempted.append(name)
            service.harness.connectors = replacement

        service.harness.connectors.disconnect_runtime = disconnect
        await service._disconnect('fixture')
        self.assertEqual(attempted, [connector_name('fixture', name) for name in ('one', 'two', 'three')])
        replacement.disconnect_runtime.assert_not_awaited()

    async def test_failed_disable_and_uninstall_do_not_commit_or_notify(self):
        for mode in ('disable', 'uninstall'):
            with self.subTest(mode=mode):
                failure = ProcessCleanupError('Original cleanup unresolved', pid=123)
                service, attempted = self.service({connector_name('fixture', 'one'): failure})
                service._lock = asyncio.Lock()
                service.store.set_enabled, service.store.uninstall = Mock(), Mock()
                service.harness.refresh_skills = Mock()
                service._changed = AsyncMock()
                with self.assertRaises(ProcessCleanupError) as caught:
                    if mode == 'disable':
                        await service.set_enabled('fixture', False)
                    else:
                        await service.uninstall('fixture')
                self.assertIs(caught.exception, failure)
                self.assertEqual(len(attempted), 3)
                service.store.set_enabled.assert_not_called()
                service.store.uninstall.assert_not_called()
                service.harness.refresh_skills.assert_not_called()
                service._changed.assert_not_awaited()
                self.assertFalse(service._lock.locked())

    async def test_multiple_failures_keep_every_original_and_current_language(self):
        for language in ('en', 'zh-CN', 'en'):
            with self.subTest(language=language), current_language.set(language):
                failures = {connector_name('fixture', name): RuntimeError('Original '+name) for name in ('one', 'three')}
                service, attempted = self.service(failures)
                with self.assertRaises(ExceptionGroup) as caught:
                    await service._disconnect('fixture')
                self.assertEqual(caught.exception.message, tr('cleanup.failed'))
                self.assertEqual(list(caught.exception.exceptions), list(failures.values()))
                self.assertEqual(len(attempted), 3)

    async def test_cancelled_cleanup_still_attempts_other_owned_connections(self):
        cancellation = asyncio.CancelledError()
        failure = RuntimeError('Original third cleanup')
        service, attempted = self.service({connector_name('fixture', 'one'): cancellation, connector_name('fixture', 'three'): failure})
        with self.assertRaises(BaseExceptionGroup) as caught:
            await service._disconnect('fixture')
        self.assertEqual(caught.exception.exceptions, (cancellation, failure))
        self.assertEqual(len(attempted), 3)

    def manager(self):
        manager = ConnectorManager.__new__(ConnectorManager)
        record = ConnectorRecord('fixture', enabled=False)
        manager.store = Mock()
        manager.store.set_enabled.return_value = record
        manager._connection_epochs = {}
        manager._unregister_tools = Mock()
        manager._refresh_prompt = Mock()
        manager._emit_changed = AsyncMock()
        manager.mcp = Mock()
        return manager, record

    async def test_disable_retains_cleanup_error_and_does_not_announce_success(self):
        manager, _ = self.manager()
        failure = ProcessCleanupError('Original process still running', pid=123)
        supervisor = SimpleNamespace(aclose=AsyncMock(side_effect=failure))
        manager.mcp.get.return_value = supervisor
        with self.assertRaises(ProcessCleanupError) as caught:
            await manager.disable('fixture')
        self.assertIs(caught.exception, failure)
        manager.store.set_enabled.assert_called_once_with('fixture', False)
        manager._unregister_tools.assert_called_once_with('fixture')
        self.assertEqual(manager._connection_epochs['fixture'], 1)
        manager._emit_changed.assert_not_awaited()
        supervisor.aclose.assert_awaited_once()
        self.assertIs(manager.mcp.get('fixture'), supervisor)

    async def test_disable_missing_or_closed_connection_remains_valid(self):
        for missing in (False, True):
            with self.subTest(missing=missing):
                manager, _ = self.manager()
                supervisor = SimpleNamespace(aclose=AsyncMock())
                if missing:
                    manager.mcp.get.side_effect = McpError('Not registered')
                else:
                    manager.mcp.get.return_value = supervisor
                self.assertEqual(await manager.disable('fixture'), {'name': 'fixture', **manager.store.set_enabled.return_value.to_dict(), 'connected': False, 'tool_count': 0})
                manager._emit_changed.assert_awaited_once_with('connector/disable', 'fixture')
                manager._refresh_prompt.assert_called_once()
                self.assertEqual(supervisor.aclose.await_count, 0 if missing else 1)
