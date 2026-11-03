"""团队关闭共用一次收尾，取消与失败不跳过其余成员。"""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from harness.app.prompt_team_run import PromptTeamRun


class PromptTeamCloseOwnershipTests(unittest.IsolatedAsyncioTestCase):
    def run_fixture(self, interrupt):
        run = object.__new__(PromptTeamRun)
        run.closed = False
        run._close_task = None
        run.states = {name: SimpleNamespace(id=name, _prompt_member=name, status='completed', error_message='') for name in ('one', 'two')}
        run.members = {name: {} for name in run.states}
        run.harness = SimpleNamespace(subagents=SimpleNamespace(interrupt=interrupt), bus=SimpleNamespace(off=Mock()), _active_team_runs={('owner', 'run')})
        run.session, run.run_id = SimpleNamespace(id='owner'), 'run'
        run.parent = SimpleNamespace(_pre_step_hooks=[run.step])
        run.previous_schema_transform, run.previous_coordinator, run.previous_reply_max_chars = None, False, None
        run.streams, run.emit = SimpleNamespace(close=AsyncMock()), AsyncMock()
        run.steering, run.goal = None, None
        return run

    async def test_concurrent_close_and_repeated_cancel_wait_for_single_cleanup(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def interrupt(*args, **kwargs):
            entered.set()
            await release.wait()
        action = AsyncMock(side_effect=interrupt)
        run = self.run_fixture(action)
        first = asyncio.create_task(run.close('completed'))
        second = None
        try:
            await entered.wait()
            second = asyncio.create_task(run.close('aborted'))
            first.cancel()
            await asyncio.sleep(0)
            first.cancel()
            await asyncio.sleep(0)
            self.assertFalse(first.done())
            self.assertFalse(run.closed)
            release.set()
            await asyncio.gather(first, second, return_exceptions=True)
            self.assertEqual(action.await_count, 2)
            run.streams.close.assert_awaited_once()
            run.emit.assert_awaited_once_with('done')
        finally:
            release.set()
            await asyncio.gather(*[task for task in (first, second) if task], return_exceptions=True)

    async def test_member_cancel_and_failure_are_preserved_and_repeat_close_does_not_replay(self):
        cancelled, failure = asyncio.CancelledError('original'), OSError('original cleanup')
        action = AsyncMock(side_effect=[cancelled, failure])
        run = self.run_fixture(action)
        for _ in range(2):
            with self.assertRaises(BaseExceptionGroup) as caught:
                await run.close('aborted')
            self.assertEqual(caught.exception.exceptions, (cancelled, failure))
        self.assertEqual(action.await_count, 2)
        run.streams.close.assert_awaited_once()
