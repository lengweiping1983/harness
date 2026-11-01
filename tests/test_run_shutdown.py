"""验证运行控制器关闭只取消一次，并等待已开始的收尾。"""
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from harness.app.runtime import RunController
from harness.core.runs import AgentRun


class RunShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.controller = RunController(SimpleNamespace(config=SimpleNamespace(home=Path(directory.name)), _agents={}))

    async def running_task(self):
        started, finishing, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def run():
            try:
                started.set()
                await asyncio.Event().wait()
            finally:
                finishing.set()
                await release.wait()

        task = asyncio.create_task(run())
        self.controller.active['session'] = task
        await started.wait()
        return task, finishing, release

    async def test_concurrent_close_does_not_cancel_the_running_finalizer_twice(self):
        task, finishing, release = await self.running_task()
        first = asyncio.create_task(self.controller.close())
        await finishing.wait()
        second = asyncio.create_task(self.controller.close())
        try:
            for _ in range(3):
                await asyncio.sleep(0)
            self.assertFalse(task.done())
            self.assertFalse(second.done())
        finally:
            release.set()
            await asyncio.gather(first, second, task, return_exceptions=True)

    async def test_cancelled_close_waiter_still_waits_for_running_finalizer(self):
        task, finishing, release = await self.running_task()
        close = asyncio.create_task(self.controller.close())
        await finishing.wait()
        try:
            for _ in range(2):
                close.cancel()
                await asyncio.sleep(0)
            self.assertFalse(close.done())
            self.assertFalse(task.done())
        finally:
            release.set()
            result = (await asyncio.gather(close, task, return_exceptions=True))[0]
        self.assertIsInstance(result, asyncio.CancelledError)

    async def test_repeat_close_keeps_the_original_paused_queue_snapshot(self):
        record = AgentRun(id='paused', session_id='session')
        self.controller._pending['session'] = {record.id: record}
        self.controller._queue_controls['session'] = {'paused': True}
        await self.controller.close()
        self.controller._pending.clear()
        await self.controller.close()
        self.assertEqual(self.controller._preserved_on_close, {'paused'})

    async def test_driver_and_owned_operation_receive_only_one_cancellation(self):
        operation, finishing, release = await self.running_task()
        entered = asyncio.Event()

        async def driver():
            entered.set()
            await operation

        task = asyncio.create_task(driver())
        record = AgentRun(id='owned', session_id='session', task=task)
        self.controller._task_records[task] = record
        self.controller._executions[operation] = record
        await entered.wait()
        close = asyncio.create_task(self.controller.close())
        try:
            await asyncio.wait_for(finishing.wait(), 2)
            self.assertEqual(operation.cancelling(), 1)
            self.assertFalse(close.done())
        finally:
            release.set()
            await asyncio.gather(close, task, operation, return_exceptions=True)

    async def test_prior_user_cancel_does_not_reach_operation_finalizer_again_through_driver(self):
        operation, finishing, release = await self.running_task()
        entered = asyncio.Event()

        async def driver():
            entered.set()
            await operation

        task = asyncio.create_task(driver())
        record = AgentRun(id='owned', session_id='session', task=task)
        self.controller._task_records[task] = record
        self.controller._executions[operation] = record
        await entered.wait()
        operation.cancel()
        await asyncio.wait_for(finishing.wait(), 2)
        close = asyncio.create_task(self.controller.close())
        try:
            await asyncio.sleep(0)
            self.assertEqual(operation.cancelling(), 1)
            self.assertFalse(operation.done())
            self.assertFalse(close.done())
        finally:
            release.set()
            await asyncio.gather(close, task, operation, return_exceptions=True)

    async def test_driver_and_operation_report_same_original_failure_only_once(self):
        entered = asyncio.Event()
        original = OSError('owned operation cleanup failed')

        async def operation():
            try:
                entered.set()
                await asyncio.Event().wait()
            finally:
                raise original

        owned = asyncio.create_task(operation())

        async def driver():
            await owned

        task = asyncio.create_task(driver())
        record = AgentRun(id='owned', session_id='session', task=task)
        self.controller.active['session'] = owned
        self.controller._task_records[task] = record
        self.controller._executions[owned] = record
        await entered.wait()
        with self.assertRaises(OSError) as caught:
            await self.controller.close()
        self.assertIs(caught.exception, original)
