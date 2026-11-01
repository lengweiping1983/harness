"""抓取超时后的来源隔离、冷却与取消回归。"""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from harness.core.errors import ToolExecutionError
from harness.tools.builtin.web_tools import build_web_tools
from harness.tools.registry import ToolRunContext


class WebFetchResilienceTests(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_suppresses_same_origin_only_until_expiry(self):
        tool = build_web_tools()[0]
        ctx = ToolRunContext(call_id='x', session_id='a', cwd='', turn=1, step=1)
        other = ToolRunContext(call_id='y', session_id='b', cwd='', turn=1, step=1)
        failure = ToolExecutionError('超时', failure_kind='timeout')
        with patch('harness.tools.builtin.web_tools._http_get', AsyncMock(side_effect=failure)) as get:
            with self.assertRaises(ToolExecutionError):
                await tool.execute({'url': 'https://example.com/a'}, ctx)
            with self.assertRaises(ToolExecutionError) as caught:
                await tool.execute({'url': 'https://example.com/b'}, ctx)
            self.assertEqual(caught.exception.details['failure_kind'], 'source_cooldown')
            self.assertEqual(get.await_count, 1)
            for url, context in [('https://another.example/a', ctx), ('https://example.com/a', other)]:
                with self.assertRaises(ToolExecutionError):
                    await tool.execute({'url': url}, context)
            self.assertEqual(get.await_count, 3)
            with patch('harness.tools.builtin.web_tools.time.monotonic', return_value=float('inf')):
                with self.assertRaises(ToolExecutionError):
                    await tool.execute({'url': 'https://example.com/a'}, ctx)
            self.assertEqual(get.await_count, 4)

    async def test_cancel_and_other_errors_do_not_poison_source(self):
        for error in (asyncio.CancelledError(), ToolExecutionError('权限拒绝')):
            tool = build_web_tools()[0]
            ctx = ToolRunContext(call_id='x', session_id='a', cwd='', turn=1, step=1)
            with patch('harness.tools.builtin.web_tools._http_get', AsyncMock(side_effect=error)) as get:
                for _ in range(2):
                    with self.assertRaises(type(error)):
                        await tool.execute({'url': 'https://example.com/a'}, ctx)
                self.assertEqual(get.await_count, 2)
