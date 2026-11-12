"""MCP故障收尾保留所属连接，清理失败不能伪装为关闭成功。"""
import asyncio
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from harness.tools.builtin.mcp_client import McpConnection, McpError, McpRegistry, McpRemoteTool, McpServerConfig, McpServerSupervisor, StdioTransport


def supervisor(name='fixture', **options):
    service = McpServerSupervisor(McpServerConfig(name, command='fixture', **options))
    service._install_generation([McpRemoteTool('old', 'Original 原文', {'type': 'object'})])
    return service


class McpCleanupOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def test_exhausted_refresh_budget_closes_original_connection(self):
        service = supervisor(reconnect_max_attempts=0)
        failure = McpError('Original catalog failure')
        connection = SimpleNamespace(list_tools=AsyncMock(side_effect=failure), aclose=AsyncMock(), closed=False)
        service._connection = connection
        with self.assertRaises(McpError) as caught:
            await service.refresh()
        self.assertIs(caught.exception, failure)
        connection.aclose.assert_awaited_once()
        self.assertIsNone(service._connection)
        self.assertEqual(service.public_names(), [])
        self.assertIsNone(service._reconnect_task)
        await service.aclose()
        connection.aclose.assert_awaited_once()

    async def test_close_failure_retains_connection_until_explicit_retry(self):
        service = supervisor()
        failure = OSError('Original cleanup failure')
        connection = SimpleNamespace(aclose=AsyncMock(side_effect=[failure, None]), closed=False)
        service._connection = connection
        with self.assertRaises(OSError) as caught:
            await service.aclose()
        self.assertIs(caught.exception, failure)
        self.assertIs(service._connection, connection)
        self.assertEqual(service.public_names(), [])
        await service.aclose()
        self.assertIsNone(service._connection)
        self.assertEqual(connection.aclose.await_count, 2)

    async def test_failed_close_cannot_publish_replacement(self):
        registry = McpRegistry([McpServerConfig('fixture', command='original')])
        original = registry.get('fixture')
        connection = SimpleNamespace(aclose=AsyncMock(side_effect=OSError('Original cleanup failure')), closed=False)
        original._connection = connection
        with self.assertRaises(OSError):
            await registry.replace(McpServerConfig('fixture', command='replacement'))
        self.assertIs(registry.get('fixture'), original)
        self.assertIs(original._connection, connection)
        connection.aclose.side_effect = None
        replacement = await registry.replace(McpServerConfig('fixture', command='replacement'))
        self.assertIsNot(replacement, original)
        self.assertIsNone(original._connection)
        self.assertEqual(connection.aclose.await_count, 2)
        await registry.aclose()

    async def test_registry_attempts_every_close_and_reports_all_original_errors(self):
        for count in (1, 2):
            with self.subTest(failures=count):
                registry = McpRegistry()
                failures = [OSError('first'), RuntimeError('second')][:count]
                connections = []
                for index in range(3):
                    service = registry.add(McpServerConfig('server' + str(index), command='fixture'))
                    connection = SimpleNamespace(aclose=AsyncMock(side_effect=failures[index] if index < count else None), closed=False)
                    service._connection = connection
                    connections.append(connection)
                if count == 1:
                    with self.assertRaises(OSError) as caught:
                        await registry.aclose()
                    self.assertIs(caught.exception, failures[0])
                else:
                    with self.assertRaises(ExceptionGroup) as caught:
                        await registry.aclose()
                    self.assertEqual(list(caught.exception.exceptions), failures)
                for connection in connections:
                    connection.aclose.assert_awaited_once()
                    connection.aclose.side_effect = None
                await registry.aclose()

    async def test_budget_cleanup_holds_connection_lock_until_finished(self):
        service = supervisor(reconnect_max_attempts=0)
        entered, release = asyncio.Event(), asyncio.Event()
        failure = McpError('Original catalog failure')

        async def close():
            entered.set()
            await release.wait()

        original = SimpleNamespace(list_tools=AsyncMock(side_effect=failure), aclose=AsyncMock(side_effect=close), closed=False)
        service._connection = original
        refresh = asyncio.create_task(service.refresh())
        try:
            await asyncio.wait_for(entered.wait(), 1)
            self.assertTrue(service._lock.locked())
            self.assertFalse(refresh.done())
        finally:
            release.set()
            result = (await asyncio.gather(refresh, return_exceptions=True))[0]
        self.assertIs(result, failure)
        self.assertIsNone(service._connection)
        original.aclose.assert_awaited_once()
        await service.aclose()

    async def test_exhausted_budget_reaps_real_owned_stdio_process(self):
        config = McpServerConfig('fixture', command=sys.executable,
            args=['-u', '-c', 'import time; time.sleep(30)'], reconnect_max_attempts=0)
        transport = StdioTransport(config)
        self.addAsyncCleanup(transport.close)
        await transport.start()
        process = transport._proc
        connection = McpConnection(config, transport=transport)
        connection.list_tools = AsyncMock(side_effect=McpError('Original catalog failure'))
        service = McpServerSupervisor(config)
        self.addAsyncCleanup(service.aclose)
        service._connection = connection
        with self.assertRaises(McpError):
            await service.refresh()
        self.assertIsNotNone(process.returncode)
        self.assertIsNone(transport._proc)
        self.assertTrue(connection.closed)
        self.assertIsNone(service._connection)
        self.assertIsNone(service._reconnect_task)

    async def test_registry_attempts_siblings_when_one_cleanup_is_cancelled(self):
        registry = McpRegistry()
        failures = [asyncio.CancelledError('Original cancellation'), OSError('Original later failure')]
        connections = []
        for index in range(3):
            service = registry.add(McpServerConfig('server' + str(index), command='fixture'))
            connection = SimpleNamespace(aclose=AsyncMock(side_effect=failures[index] if index < 2 else None), closed=False)
            service._connection = connection
            connections.append(connection)
        with self.assertRaises(BaseExceptionGroup) as caught:
            await registry.aclose()
        self.assertEqual(list(caught.exception.exceptions), failures)
        for index, connection in enumerate(connections):
            connection.aclose.assert_awaited_once()
            if index < 2:
                self.assertIs(registry.get('server' + str(index))._connection, connection)
            connection.aclose.side_effect = None
        await registry.aclose()
