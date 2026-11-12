"""对话监听器收尾保留重复取消后的归属和未开始的工作。"""
import asyncio
import unittest

from harness.session.session import Session


class SessionListenerOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_drain_joins_current_listener_and_retains_the_rest(self):
        session = Session()
        entered, release = asyncio.Event(), asyncio.Event()
        effects = []
        async def first():
            entered.set()
            await release.wait()
            effects.append('first')
        async def second():
            effects.append('second')
        session._pending_awaitables.extend([first(), second()])
        task = asyncio.create_task(session.drain())
        await entered.wait()
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        try:
            await asyncio.sleep(0)
            self.assertFalse(task.done())
            self.assertEqual(effects, [])
        finally:
            release.set()
            try:
                await task
            except asyncio.CancelledError:
                pass
            await session.drain()
        self.assertEqual(effects, ['first', 'second'])
        self.assertEqual(session._pending_awaitables, [])

    async def test_concurrent_drain_cannot_finish_before_the_owned_listener(self):
        session = Session()
        entered, release = asyncio.Event(), asyncio.Event()
        async def listener():
            entered.set()
            await release.wait()
        session._pending_awaitables.append(listener())
        first = asyncio.create_task(session.drain())
        await entered.wait()
        second = asyncio.create_task(session.drain())
        try:
            await asyncio.sleep(0)
            self.assertFalse(second.done())
        finally:
            release.set()
            await asyncio.gather(first, second)

    async def test_listener_error_still_allows_later_listener_once(self):
        session = Session()
        calls = []
        async def broken():
            calls.append('broken')
            raise OSError('original diagnostic')
        async def healthy():
            calls.append('healthy')
        session._pending_awaitables.extend([broken(), healthy()])
        with self.assertLogs('harness.session.session', level='ERROR') as logs:
            await session.drain()
        self.assertIn('original diagnostic', '\n'.join(logs.output))
        await session.drain()
        self.assertEqual(calls, ['broken', 'healthy'])

    async def test_listener_self_cancellation_retains_following_work(self):
        session = Session()
        calls = []
        async def cancelled():
            calls.append('cancelled')
            raise asyncio.CancelledError
        async def healthy():
            calls.append('healthy')
        session._pending_awaitables.extend([cancelled(), healthy()])
        with self.assertRaises(asyncio.CancelledError):
            await session.drain()
        await session.drain()
        self.assertEqual(calls, ['cancelled', 'healthy'])

    async def test_repeated_close_joins_listener_in_first_close(self):
        session = Session()
        entered, release = asyncio.Event(), asyncio.Event()
        async def listener():
            entered.set()
            await release.wait()
        session._pending_awaitables.append(listener())
        first = asyncio.create_task(session.close())
        await entered.wait()
        second = asyncio.create_task(session.close())
        try:
            await asyncio.sleep(0)
            self.assertFalse(second.done())
        finally:
            release.set()
            await asyncio.gather(first, second)
        self.assertEqual(session._pending_awaitables, [])
