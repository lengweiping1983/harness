"""以真实宿主和异步屏障验证首次启动、等待取消及关闭归属。"""
import asyncio
import tempfile
import threading
import unittest
from dataclasses import fields
from pathlib import Path
from unittest.mock import AsyncMock

from harness import Harness, HarnessConfig
from harness.core.errors import HarnessError
from harness.tools.registry import ToolDefinition
from tests.web_fixture import FixtureTransport


class HarnessStartupLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        flags = {field.name: False for field in fields(HarnessConfig) if field.name.startswith('enable_')}
        self.host = Harness(HarnessConfig(api_key='fixture-only', home=root/'state', cwd=root,
            model='fixture', **flags), transport=FixtureTransport())
        self.addAsyncCleanup(self.host.close)
        # 只控制本地目录装配，不构造或连接远端服务器。
        self.host.config.enable_mcp = True
        self.host.config.mcp_servers = ['fixture']
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        async def connect():
            self.entered.set()
            await self.release.wait()
            self.host.tools.register(ToolDefinition(
                {'name': 'startup_fixture', 'description': 'fixture', 'parameters': {'type': 'object'}},
                AsyncMock(return_value='fixture'), effect='read'))
        self.host.mcp.connect_all = AsyncMock(side_effect=connect)

    async def test_concurrent_waiters_only_return_after_catalog_is_ready(self):
        first = asyncio.create_task(self.host.ensure_started())
        await self.entered.wait()
        second = asyncio.create_task(self.host.ensure_started())
        try:
            await asyncio.sleep(0)
            self.assertFalse(second.done())
            self.assertFalse(self.host._started)
            self.assertNotIn('startup_fixture', self.host.tools.names())
        finally:
            self.release.set()
            await asyncio.gather(first, second)
        self.assertTrue(self.host._started)
        self.assertIn('startup_fixture', self.host.tools.names())
        await self.host.ensure_started()
        self.host.mcp.connect_all.assert_awaited_once()

    async def test_cancelled_waiter_does_not_cancel_or_replay_shared_startup(self):
        first = asyncio.create_task(self.host.ensure_started())
        await self.entered.wait()
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        self.assertFalse(self.host._start_task.done())
        second = asyncio.create_task(self.host.ensure_started())
        self.release.set()
        await second
        self.host.mcp.connect_all.assert_awaited_once()
        self.assertIn('startup_fixture', self.host.tools.names())

    async def test_failure_is_shared_and_never_reported_as_started_or_retried(self):
        failure = OSError('fixture startup failed')
        self.host.mcp.connect_all.side_effect = failure
        for _ in range(2):
            with self.assertRaises(OSError) as caught:
                await self.host.ensure_started()
            self.assertIs(caught.exception, failure)
        self.assertFalse(self.host._started)
        self.host.mcp.connect_all.assert_awaited_once()

    async def test_close_cancels_and_joins_startup_before_closing_connections(self):
        cleaning, cleanup_release = asyncio.Event(), asyncio.Event()
        async def connect():
            self.entered.set()
            try:
                await asyncio.Future()
            finally:
                cleaning.set()
                await cleanup_release.wait()
        self.host.mcp.connect_all.side_effect = connect
        self.host.mcp.aclose = AsyncMock(wraps=self.host.mcp.aclose)
        waiter = asyncio.create_task(self.host.ensure_started())
        await self.entered.wait()
        close = asyncio.create_task(self.host.close())
        await cleaning.wait()
        try:
            self.assertFalse(close.done())
            self.host.mcp.aclose.assert_not_awaited()
            with self.assertRaises(HarnessError) as caught:
                await self.host.ensure_started()
            self.assertEqual(caught.exception.code, 'run/closed')
        finally:
            cleanup_release.set()
            await close
            await asyncio.gather(waiter, return_exceptions=True)
        self.host.mcp.aclose.assert_awaited_once()
        self.assertFalse(self.host._started)
        with self.assertRaises(HarnessError):
            await self.host.ensure_started()

    async def test_disabled_mcp_starts_once_without_connecting(self):
        self.host.config.enable_mcp = False
        await asyncio.gather(self.host.ensure_started(), self.host.ensure_started())
        self.assertTrue(self.host._started)
        self.host.mcp.connect_all.assert_not_awaited()

    async def test_cross_loop_waiters_use_the_runtime_owner(self):
        owner = asyncio.new_event_loop()
        running, entered, release = threading.Event(), threading.Event(), threading.Event()
        loops = []
        def run():
            asyncio.set_event_loop(owner)
            owner.call_soon(running.set)
            try:
                owner.run_forever()
            finally:
                owner.run_until_complete(owner.shutdown_asyncgens())
                owner.run_until_complete(owner.shutdown_default_executor())
                owner.close()
        async def connect():
            loops.append(asyncio.get_running_loop())
            entered.set()
            if not await asyncio.to_thread(release.wait, 5):
                raise TimeoutError('fixture startup barrier not released')
        self.host.mcp.connect_all.side_effect = connect
        self.host.runtime.loop = owner
        thread = threading.Thread(target=run, name='startup-test-owner')
        thread.start()
        first = second = None
        try:
            self.assertTrue(await asyncio.to_thread(running.wait, 2))
            first = asyncio.create_task(self.host.ensure_started())
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            second = asyncio.create_task(self.host.ensure_started())
            await asyncio.sleep(0)
            self.assertFalse(second.done())
            release.set()
            await asyncio.gather(first, second)
            await self.host.close()
        finally:
            release.set()
            await asyncio.gather(*[task for task in (first, second) if task], return_exceptions=True)
            owner.call_soon_threadsafe(owner.stop)
            await asyncio.to_thread(thread.join, 5)
            self.host.runtime.loop = None
        self.assertFalse(thread.is_alive())
        self.assertEqual(loops, [owner])
        self.host.mcp.connect_all.assert_awaited_once()
