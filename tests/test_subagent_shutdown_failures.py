"""成员批量关闭尝试全部对象，失败身份保留供显式重试。"""
import asyncio
import unittest
from unittest.mock import AsyncMock

from harness.core.runs import AgentRun
from harness.subagent.manager import SubagentManager


class SubagentShutdownFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_failures_do_not_skip_other_members_and_remain_retryable(self):
        for owner in (None, 'owner'):
            manager = SubagentManager(adapter=None, tools=None, prompts=None)
            states = [AgentRun(id=name, parent_session_id='owner') for name in ('first', 'second', 'third')]
            other = AgentRun(id='other', parent_session_id='unrelated')
            manager._states = {state.id: state for state in states + [other]}
            manager._by_parent = {'owner': [state.id for state in states], 'unrelated': ['other']}
            first, third = OSError('First 原文'), RuntimeError('Third 原文')
            outcomes = {'first': first, 'second': 0, 'third': third, 'other': 0}
            async def stop(state, reason):
                value = outcomes[state.id]
                if isinstance(value, BaseException):
                    raise value
                return value
            manager._stop_record = AsyncMock(side_effect=stop)
            with self.assertRaises(ExceptionGroup) as error:
                await manager.shutdown(owner)
            self.assertEqual(list(error.exception.exceptions), [first, third])
            self.assertEqual([call.args[0].id for call in manager._stop_record.await_args_list],
                             ['first', 'second', 'third'] + ([] if owner else ['other']))
            self.assertEqual(set(manager._states), {'first', 'third'} | ({'other'} if owner else set()))
            self.assertEqual(manager._by_parent['owner'], ['first', 'third'])
            self.assertEqual(manager._closing, {})
            outcomes.update(first=0, third=0)
            self.assertEqual(await manager.shutdown('owner'), 2)
            self.assertNotIn('owner', manager._by_parent)

    async def test_single_failure_preserves_original_cancellation(self):
        manager = SubagentManager(adapter=None, tools=None, prompts=None)
        states = [AgentRun(id=name, parent_session_id='owner') for name in ('first', 'second')]
        manager._states = {state.id: state for state in states}
        manager._by_parent = {'owner': ['first', 'second']}
        failure = asyncio.CancelledError('Original cancellation')
        manager._stop_record = AsyncMock(side_effect=[failure, 0])
        with self.assertRaises(asyncio.CancelledError) as error:
            await manager._shutdown('owner')
        self.assertIs(error.exception, failure)
        self.assertEqual(manager._stop_record.await_count, 2)
        self.assertEqual(manager._by_parent, {'owner': ['first']})
