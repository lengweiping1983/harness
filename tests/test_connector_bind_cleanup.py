"""绑定探测收尾未确认时不提交绑定，保留连接和清理的原异常。"""
import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

from harness.connectors.manager import ConnectorManager
from harness.connectors.store import ConnectorRecord


class ConnectorBindCleanupTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self):
        manager = object.__new__(ConnectorManager)
        manager.store = Mock()
        manager.store.get.return_value = ConnectorRecord('fixture', transport='streamable-http', url='https://fixture.invalid')
        manager._apply_oauth_header = AsyncMock()
        manager._validate_auth_header = Mock()
        manager._emit_changed = AsyncMock()
        return manager

    async def test_close_failure_never_commits_binding_or_loses_original_errors(self):
        for connect_failure in (None, OSError('Original connection failure'), asyncio.CancelledError('Original cancel')):
            manager = self.fixture()
            close_failure = OSError('Original close failure')
            probe = Mock(connect=AsyncMock(side_effect=connect_failure), aclose=AsyncMock(side_effect=close_failure))
            with patch('harness.tools.builtin.mcp_client.McpServerSupervisor', return_value=probe):
                with self.assertRaises(BaseException) as caught:
                    await manager.bind('fixture')
            if connect_failure is None:
                self.assertIs(caught.exception, close_failure)
            else:
                self.assertIsInstance(caught.exception, BaseExceptionGroup)
                primary, cleanup = caught.exception.exceptions
                self.assertIs(cleanup, close_failure)
                self.assertIs(primary if isinstance(connect_failure, asyncio.CancelledError) else primary.__cause__, connect_failure)
            manager.store.set_bound.assert_not_called()
            manager._emit_changed.assert_not_awaited()
            probe.connect.assert_awaited_once()
            probe.aclose.assert_awaited_once()

    async def test_repeated_cancellation_waits_for_one_probe_cleanup(self):
        manager = self.fixture()
        entered, release = asyncio.Event(), asyncio.Event()
        async def close():
            entered.set()
            await release.wait()
        probe = Mock(connect=AsyncMock(), aclose=AsyncMock(side_effect=close))
        with patch('harness.tools.builtin.mcp_client.McpServerSupervisor', return_value=probe):
            task = asyncio.create_task(manager.bind('fixture'))
            try:
                await entered.wait()
                task.cancel()
                await asyncio.sleep(0)
                task.cancel()
                await asyncio.sleep(0)
                self.assertFalse(task.done())
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)
        probe.aclose.assert_awaited_once()
        manager.store.set_bound.assert_not_called()
