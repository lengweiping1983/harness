"""Ralph取消只收尾当前成员，等待所属回收后才传播取消。"""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from harness.tools.builtin.ralph_tools import build_ralph_tools


class RalphCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_repeated_cancel_waits_for_one_member_interrupt(self):
        entered, cleaning, release = (asyncio.Event() for _ in range(3))

        async def wait(*args, **kwargs):
            entered.set()
            await asyncio.Future()

        async def interrupt(*args, **kwargs):
            cleaning.set()
            await release.wait()

        manager = SimpleNamespace(spawn=Mock(return_value=SimpleNamespace(id='owned')),
                                  wait=AsyncMock(side_effect=wait), interrupt=AsyncMock(side_effect=interrupt))
        task = asyncio.create_task(build_ralph_tools(manager)[0].execute({'objective': 'Original objective'}, SimpleNamespace(session_id='owner')))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            task.cancel()
            await asyncio.wait_for(cleaning.wait(), 1)
            task.cancel()
            await asyncio.sleep(0)
            self.assertFalse(task.done())
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
            manager.spawn.assert_called_once()
            manager.interrupt.assert_awaited_once()
            self.assertEqual(manager.interrupt.call_args.args, ('owned',))
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)

    async def test_cancel_and_cleanup_failure_preserve_both_originals_without_replay(self):
        entered = asyncio.Event()
        failure = OSError('Original cleanup 原文')
        async def wait(*args, **kwargs):
            entered.set()
            await asyncio.Future()
        manager = SimpleNamespace(spawn=Mock(return_value=SimpleNamespace(id='owned')),
            wait=AsyncMock(side_effect=wait), interrupt=AsyncMock(side_effect=failure))
        task = asyncio.create_task(build_ralph_tools(manager)[0].execute({'objective': 'Original'}, SimpleNamespace(session_id='owner')))
        await entered.wait()
        task.cancel('Original cancellation')
        with self.assertRaises(BaseExceptionGroup) as caught:
            await task
        self.assertIsInstance(caught.exception.exceptions[0], asyncio.CancelledError)
        self.assertEqual(caught.exception.exceptions[0].args, ('Original cancellation',))
        self.assertIs(caught.exception.exceptions[1], failure)
        manager.interrupt.assert_awaited_once()
        manager.spawn.assert_called_once()
