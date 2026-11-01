"""验证无模型执行器关闭时保留已接受工作的归属。"""
import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from harness.execution.config import ExecutionConfig, ProjectConfig
from harness.execution.runtime import ExecutionRuntime
from harness.tools.registry import ToolDefinition


class ExecutionShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        project = root / 'project'
        project.mkdir()
        self.runtime = ExecutionRuntime(ExecutionConfig(home=root/'state', projects={'project': ProjectConfig(project)}))
        self.addAsyncCleanup(self.runtime.close)

    async def pending_call(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def operation(args, ctx):
            entered.set()
            await release.wait()
            return {'finished': True}

        self.runtime.registry.register(ToolDefinition({'name': 'shutdown_probe', 'parameters': {'type': 'object'}},
            operation, effect='read'))
        call = asyncio.create_task(self.runtime.call('local', 'shutdown_probe', {}))
        await entered.wait()
        return call, release

    async def test_concurrent_close_waits_for_the_same_accepted_work(self):
        call, release = await self.pending_call()
        first = asyncio.create_task(self.runtime.close())
        await asyncio.sleep(0)
        second = asyncio.create_task(self.runtime.close())
        await asyncio.sleep(0)
        try:
            self.assertFalse(second.done())
            denied = await self.runtime.call('local', 'shutdown_probe', {})
            self.assertEqual(denied['data']['error']['code'], 'execution/closed')
        finally:
            release.set()
            await asyncio.gather(first, second, call, return_exceptions=True)
        self.assertFalse((await call)['is_error'])
        with self.assertRaises(sqlite3.ProgrammingError):
            self.runtime.store.db.execute('SELECT 1')

    async def test_repeated_waiter_cancellation_does_not_cancel_accepted_work(self):
        call, release = await self.pending_call()
        closing = asyncio.create_task(self.runtime.close())
        await asyncio.sleep(0)
        closing.cancel()
        await asyncio.sleep(0)
        closing.cancel()
        await asyncio.sleep(0)
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await closing
        result = (await asyncio.gather(call, return_exceptions=True))[0]
        self.assertIsInstance(result, dict)
        self.assertFalse(result['is_error'])
        self.assertEqual(result['data'], {'finished': True})
        with self.assertRaises(sqlite3.ProgrammingError):
            self.runtime.store.db.execute('SELECT 1')

    async def test_cleanup_failure_is_not_hidden_by_a_later_close(self):
        runtime = object.__new__(ExecutionRuntime)
        runtime.closed = False
        runtime._close_task = None
        runtime._tasks, runtime._monitors = set(), set()
        runtime.jobs = SimpleNamespace(cancel_all=AsyncMock(side_effect=RuntimeError('cleanup failed')))
        runtime.store = Mock()
        for _ in range(2):
            with self.assertRaisesRegex(RuntimeError, 'cleanup failed'):
                await runtime.close()
        runtime.jobs.cancel_all.assert_awaited_once()
        runtime.store.close.assert_not_called()
