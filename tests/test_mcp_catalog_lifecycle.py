"""受控异步屏障保证旧连接与旧目录结果不能覆盖当前 MCP 状态。"""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from harness.tools.builtin.mcp_client import McpConnectionLost, McpError, McpRemoteTool, McpServerConfig, McpServerSupervisor, public_tool_name, remote_tool_contract


def tool(name):
    return McpRemoteTool(name, 'original description', {'type': 'object'})


class McpCatalogLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def supervisor(self):
        return McpServerSupervisor(McpServerConfig('fixture', command='fixture', reconnect_enabled=False))

    async def test_close_rejects_late_catalog_without_republishing_tools(self):
        supervisor = self.supervisor()
        ready, release = asyncio.Event(), asyncio.Event()
        async def listed():
            ready.set()
            await release.wait()
            return [tool('obsolete')]
        connection = SimpleNamespace(list_tools=listed, aclose=AsyncMock(), closed=False)
        supervisor._connection = connection
        task = asyncio.create_task(supervisor.refresh())
        await ready.wait()
        await supervisor.aclose()
        release.set()
        with self.assertRaises(McpError):
            await task
        self.assertEqual(supervisor.public_names(), [])
        connection.aclose.assert_awaited_once()

    async def test_newer_refresh_wins_when_same_connection_returns_out_of_order(self):
        supervisor = self.supervisor()
        ready, release = asyncio.Event(), asyncio.Event()
        count = 0
        async def listed():
            nonlocal count
            count += 1
            if count == 1:
                ready.set()
                await release.wait()
                return [tool('obsolete')]
            return [tool('current')]
        supervisor._connection = SimpleNamespace(list_tools=listed, aclose=AsyncMock(), closed=False)
        old = asyncio.create_task(supervisor.refresh())
        await ready.wait()
        await supervisor.refresh()
        release.set()
        with self.assertRaises(McpError):
            await old
        self.assertEqual(supervisor.public_names(), [public_tool_name('fixture', 'current')])
        await supervisor.aclose()

    async def test_old_call_failure_does_not_drop_replacement_connection(self):
        for error in (McpConnectionLost('old connection failed'), McpError('old remote error')):
            supervisor = self.supervisor()
            declared = tool('operation')
            supervisor._install_generation([declared])
            ready, release = asyncio.Event(), asyncio.Event()
            async def call(*args, before_send=None, **kwargs):
                before_send()
                ready.set()
                await release.wait()
                raise error
            old = SimpleNamespace(call_tool=call, aclose=AsyncMock(), closed=False)
            current = SimpleNamespace(aclose=AsyncMock(), closed=False)
            supervisor._connection = old
            task = asyncio.create_task(supervisor.call(public_tool_name('fixture', 'operation'), {}, expected_contract=remote_tool_contract(declared)))
            await ready.wait()
            supervisor._connection = current
            supervisor._last_error = ''
            release.set()
            with self.assertRaises(type(error)):
                await task
            self.assertIs(supervisor._connection, current)
            self.assertEqual(supervisor._last_error, '')
            self.assertEqual(supervisor.public_names(), [public_tool_name('fixture', 'operation')])
            current.aclose.assert_not_awaited()
            self.assertEqual(supervisor._attempt, 0)
            await supervisor.aclose()

    async def test_late_read_error_cannot_change_closed_or_replacement_status(self):
        for closed in (True, False):
            supervisor = self.supervisor()
            ready, release = asyncio.Event(), asyncio.Event()
            async def listed():
                ready.set()
                await release.wait()
                raise McpError('obsolete read failed')
            old = SimpleNamespace(list_tools=listed, aclose=AsyncMock(), closed=False)
            supervisor._connection = old
            task = asyncio.create_task(supervisor.refresh())
            await ready.wait()
            if closed:
                await supervisor.aclose()
            else:
                supervisor._connection = SimpleNamespace(aclose=AsyncMock(), closed=False)
                supervisor._install_generation([tool('current')])
            release.set()
            with self.assertRaises(McpError):
                await task
            self.assertEqual(supervisor._last_error, '')
            self.assertEqual(supervisor._attempt, 0)
            await supervisor.aclose()

    async def test_owned_errors_are_complete_bilingual_sentences(self):
        from unittest.mock import patch
        from harness.core.language import current_language
        from harness.tools.builtin.mcp_client import McpRegistry

        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                supervisor = self.supervisor()
                errors = []
                for collision in (False, True):
                    items = [tool('same'), tool('same')] if not collision else [tool('first'), tool('second')]
                    with patch('harness.tools.builtin.mcp_client.public_tool_name', return_value='collision'):
                        with self.assertRaises(McpError) as caught:
                            supervisor._install_generation(items)
                    errors.append(str(caught.exception))
                    self.assertEqual(supervisor.public_names(), [])
                with self.assertRaises(McpError) as caught:
                    await supervisor.call('unknown-{tool}', {}, expected_contract='unused')
                errors.append(str(caught.exception))
                self.assertIn('unknown-{tool}', errors[-1])
                declared = tool('operation')
                supervisor._install_generation([declared])
                with self.assertRaises(McpError) as caught:
                    await supervisor.call(public_tool_name('fixture', 'operation'), {}, expected_contract=remote_tool_contract(declared))
                errors.append(str(caught.exception))
                with self.assertRaises(McpError) as caught:
                    await supervisor.request_resource('resources/list')
                errors.append(str(caught.exception))
                registry = McpRegistry([McpServerConfig(name, command='fixture', reconnect_enabled=False) for name in ('other', 'fixture')])
                with self.assertRaises(McpError) as caught:
                    registry.get('unknown-{server}')
                errors.append(str(caught.exception))
                self.assertIn('unknown-{server}', errors[-1])
                self.assertIn('fixture', errors[-1])
                from harness.i18n import tr
                self.assertIn(tr('ui.common.list_separator').join(('fixture', 'other')), errors[-1])
                for text in errors:
                    if language == 'en':
                        self.assertFalse(any('\u4e00' <= ch <= '\u9fff' for ch in text))
                        for punctuation in '（），；：':
                            self.assertNotIn(punctuation, text)
                    else:
                        self.assertTrue(any('\u4e00' <= ch <= '\u9fff' for ch in text))
                self.assertIsNone(supervisor._connection)
                self.assertIsNone(supervisor._reconnect_task)
                await supervisor.aclose()
