"""执行器共享读取、独占交接、取消与关闭的回归。"""
import asyncio
import gc
import tempfile
import unittest
from pathlib import Path

from harness.execution.config import ExecutionConfig, ProjectConfig
from harness.execution.runtime import ExecutionRuntime
from harness.execution.scheduling import ExecutionGate
from harness.tools.registry import ToolDefinition


class ExecutionGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_writer_precedes_later_reader(self):
        gate = ExecutionGate()
        order = []

        async def enter(label, shared):
            async with gate.hold(shared=shared):
                order.append(label)

        async with gate.hold(shared=True):
            writer = asyncio.create_task(enter('writer', False))
            await asyncio.sleep(0)
            reader = asyncio.create_task(enter('reader', True))
            await asyncio.sleep(0)
            self.assertEqual(order, [])
        await asyncio.gather(writer, reader)
        self.assertEqual(order, ['writer', 'reader'])
        self.assertFalse(gate.locked())

    async def test_exclusive_call_blocks_and_cancelled_waiting_reader_is_removed(self):
        gate = ExecutionGate()
        entered = asyncio.Event()

        async def read():
            async with gate.hold(shared=True):
                entered.set()

        async with gate.hold():
            cancelled = asyncio.create_task(read())
            reader = asyncio.create_task(read())
            await asyncio.sleep(0)
            self.assertFalse(entered.is_set())
            cancelled.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await cancelled
        await reader
        self.assertTrue(entered.is_set())
        self.assertFalse(gate.locked())

    async def test_cancelled_writer_unblocks_readers(self):
        gate = ExecutionGate()
        async with gate.hold(shared=True):
            async def write():
                async with gate.hold():
                    self.fail('写操作不应进入')
            writer = asyncio.create_task(write())
            await asyncio.sleep(0)
            writer.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await writer
            async with gate.hold(shared=True):
                self.assertTrue(gate.locked())
        self.assertFalse(gate.locked())

    async def test_cancelled_active_reader_and_failed_writer_release(self):
        gate = ExecutionGate()
        entered = asyncio.Event()
        async def read():
            async with gate.hold(shared=True):
                entered.set()
                await asyncio.Event().wait()
        reader = asyncio.create_task(read())
        await entered.wait()
        reader.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await reader
        with self.assertRaisesRegex(ValueError, 'fixture'):
            async with gate.hold():
                raise ValueError('fixture')
        self.assertFalse(gate.locked())


class ReadingRuntime(ExecutionRuntime):
    """用可控等待替代内置读取，检查真实入口的调度。"""

    async def _git_status(self, args, ctx):
        return await self._context(args, ctx)

    async def _context(self, args, ctx):
        self.started += 1
        if self.started == 2:
            self.overlap.set()
        await self.release.wait()
        return {'done': True}


class ExecutionReadConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        project = root / 'project'
        project.mkdir()
        self.runtime = ReadingRuntime(ExecutionConfig(root / 'state', {'project': ProjectConfig(project)}))
        self.runtime.started = 0
        self.runtime.overlap = asyncio.Event()
        self.runtime.release = asyncio.Event()
        self.addAsyncCleanup(self.runtime.close)
        self.addCleanup(self.runtime.release.set)

    async def test_shared_reads_overlap_and_close_waits_after_client_cancel(self):
        first = asyncio.create_task(self.runtime.call('local', 'harness_context', {}))
        second = asyncio.create_task(self.runtime.call('local', 'harness_context', {}))
        try:
            await asyncio.wait_for(self.runtime.overlap.wait(), 2)
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await first
            closing = asyncio.create_task(self.runtime.close())
            await asyncio.sleep(0)
            self.assertFalse(closing.done())
            self.runtime.release.set()
            self.assertFalse((await second)['is_error'])
            await closing
        finally:
            self.runtime.release.set()
            await asyncio.gather(first, second, return_exceptions=True)
        gc.collect()
        self.assertEqual(len(self.runtime._locks), 0)

    async def test_same_project_reads_overlap(self):
        opened = await self.runtime.call('local', 'harness_session_open', {
            'project_id': 'project', 'request_id': 'open-concurrency-fixture',
        })
        self.assertFalse(opened['is_error'], opened)
        sid = opened['session_id']
        calls = [asyncio.create_task(self.runtime.call('local', 'harness_git_status', {
            'session_id': sid,
        })) for _ in range(2)]
        try:
            await asyncio.wait_for(self.runtime.overlap.wait(), 2)
        finally:
            self.runtime.release.set()
            results = await asyncio.gather(*calls)
        self.assertTrue(all(not result['is_error'] for result in results), results)
        events = self.runtime.store.events(sid)['events']
        self.assertEqual(sum(event['kind'] == 'tool/result' for event in events), 2)

    async def test_replacement_read_definition_stays_exclusive(self):
        entered = asyncio.Event()
        calls = []
        async def replacement(args, ctx):
            calls.append(1)
            entered.set()
            await self.runtime.release.wait()
            return {}
        self.runtime.registry.register(ToolDefinition({'name': 'harness_context'}, replacement, effect='read'), replace=True)
        first = asyncio.create_task(self.runtime.call('local', 'harness_context', {}))
        await entered.wait()
        second = asyncio.create_task(self.runtime.call('local', 'harness_context', {}))
        try:
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            self.assertEqual(len(calls), 1)
        finally:
            self.runtime.release.set()
            await asyncio.gather(first, second)
        self.assertEqual(len(calls), 2)
