"""执行器对未核准工具保持同域串行、请求生命周期独立，并回收空闲锁。"""
import asyncio
import gc
import tempfile
import unittest
from pathlib import Path

from harness.execution.config import ExecutionConfig, ProjectConfig
from harness.execution.runtime import ExecutionRuntime
from harness.tools.registry import ToolDefinition


class ExecutionLockLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        project = root / 'project'
        project.mkdir()
        self.runtime = ExecutionRuntime(ExecutionConfig(root / 'state', {'project': ProjectConfig(project)}))
        self.addAsyncCleanup(self.runtime.close)

    async def test_waiters_share_lock_and_idle_principal_locks_are_reclaimed(self):
        entered, release = asyncio.Event(), asyncio.Event()
        started = []

        async def execute(args, ctx):
            started.append(ctx.services['principal'])
            entered.set()
            await release.wait()
            return {'done': True}

        self.runtime.registry.register(ToolDefinition({'name': 'probe'}, execute, effect='read'))
        first = asyncio.create_task(self.runtime.call('principal', 'probe', {}))
        await entered.wait()
        lock = self.runtime._locks['principal:principal']
        second = asyncio.create_task(self.runtime.call('principal', 'probe', {}))
        try:
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            self.assertEqual(started, ['principal'])
            self.assertIs(self.runtime._locks['principal:principal'], lock)
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await first
            self.assertTrue(lock.locked())
        finally:
            release.set()
            await second
            await asyncio.gather(*tuple(self.runtime._tasks), return_exceptions=True)
        self.assertEqual(started, ['principal', 'principal'])
        del lock
        gc.collect()
        self.assertEqual(len(self.runtime._locks), 0)
        for index in range(20):
            result = await self.runtime.call(str(index), 'probe', {})
            self.assertFalse(result['is_error'])
        gc.collect()
        self.assertEqual(len(self.runtime._locks), 0)
