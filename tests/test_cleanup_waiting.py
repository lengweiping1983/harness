"""验证各资源关闭入口共用的取消等待与失败传播契约。"""
import asyncio
import unittest

from harness.core.cleanup import join_cleanup


class CleanupWaitingTests(unittest.IsolatedAsyncioTestCase):
    async def test_waiters_share_result_without_reexecuting_cleanup(self):
        future = asyncio.get_running_loop().create_future()
        first = asyncio.create_task(join_cleanup(future))
        second = asyncio.create_task(join_cleanup(future))
        value = object()
        future.set_result(value)
        self.assertEqual(await asyncio.gather(first, second), [value, value])
        self.assertIs(await join_cleanup(future), value)

    async def test_repeated_waiter_cancellation_does_not_cancel_cleanup(self):
        future = asyncio.get_running_loop().create_future()
        waiter = asyncio.create_task(join_cleanup(future))
        await asyncio.sleep(0)
        try:
            for _ in range(3):
                waiter.cancel()
                await asyncio.sleep(0)
                self.assertFalse(waiter.done())
                self.assertFalse(future.cancelled())
        finally:
            future.set_result('closed')
            result = (await asyncio.gather(waiter, return_exceptions=True))[0]
        self.assertIsInstance(result, asyncio.CancelledError)
        self.assertEqual(await join_cleanup(future), 'closed')

    async def test_cleanup_cancellation_propagates_without_spinning(self):
        future = asyncio.get_running_loop().create_future()
        waiter = asyncio.create_task(join_cleanup(future))
        await asyncio.sleep(0)
        future.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(waiter, 1)
        self.assertTrue(waiter.cancelled())

    async def test_cleanup_failure_remains_visible_after_waiter_cancellation(self):
        loop = asyncio.get_running_loop()
        diagnostics = []
        previous = loop.get_exception_handler()
        loop.set_exception_handler(lambda loop, context: diagnostics.append(context))
        self.addCleanup(loop.set_exception_handler, previous)
        future = loop.create_future()
        waiter = asyncio.create_task(join_cleanup(future))
        await asyncio.sleep(0)
        waiter.cancel()
        await asyncio.sleep(0)
        failure = OSError('cleanup failed')
        future.set_exception(failure)
        with self.assertRaises(OSError) as caught:
            await waiter
        self.assertIs(caught.exception, failure)
        with self.assertRaises(OSError) as repeated:
            await join_cleanup(future)
        self.assertIs(repeated.exception, failure)
        await asyncio.sleep(0)
        self.assertEqual(diagnostics, [])
