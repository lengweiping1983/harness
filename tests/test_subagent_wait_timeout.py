"""零秒等待只读当前状态，不停止成员或转换非法超时。"""
import asyncio
import unittest
from unittest.mock import AsyncMock, Mock

from harness.core.errors import ToolExecutionError
from harness.core.language import current_language
from harness.core.runs import AgentRun
from harness.i18n import tr
from harness.subagent.manager import SubagentManager
from harness.tools.builtin.subagent_tools import build_subagent_tools
from harness.tools.registry import ToolRunContext


class SubagentWaitTimeoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_zero_wait_returns_without_cancelling_active_member(self):
        manager = SubagentManager(adapter=None, tools=None, prompts=None)
        gate = asyncio.Event()
        task = asyncio.create_task(gate.wait())
        state = AgentRun(id='fixture', status='running', task=task)
        manager._states[state.id] = state
        try:
            self.assertIs(await asyncio.wait_for(manager.wait(state.id, timeout=0), .2), state)
            self.assertFalse(task.done())
        finally:
            gate.set();await task

    async def test_sdk_and_tool_reject_invalid_timeout_before_waiting(self):
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                manager = SubagentManager(adapter=None, tools=None, prompts=None)
                manager.get = Mock(return_value=AgentRun(id='fixture'))
                tool = next(t for t in build_subagent_tools(manager) if t.schema.name == 'wait_agent')
                for value in (True, False, '1', [], {}, -1, float('nan'), float('inf'), 10 ** 400):
                    with self.assertRaises(ValueError) as error:
                        await manager.wait('fixture', timeout=value)
                    self.assertEqual(str(error.exception), tr('subagent.wait.invalid_timeout'))
                    with self.assertRaises(ToolExecutionError) as error:
                        await tool.execute({'agent_id': 'fixture', 'timeout': value}, ToolRunContext('call', 'owner', '.', 1, 1))
                    self.assertEqual(error.exception.message, tr('subagent.wait.invalid_timeout'))
                manager.get.assert_not_called()

    async def test_tool_zero_and_bounded_positive_values_keep_wait_only_semantics(self):
        manager = SubagentManager(adapter=None, tools=None, prompts=None)
        state = AgentRun(id='fixture', status='running')
        manager.target_for = Mock(return_value=state)
        manager.session_for = Mock(return_value=None)
        manager.wait = AsyncMock(return_value=state)
        manager.interrupt = Mock()
        tool = next(t for t in build_subagent_tools(manager) if t.schema.name == 'wait_agent')
        for value, expected in [(0, 0), (.5, .5), (900, 600)]:
            await tool.execute({'agent_id': 'fixture', 'timeout': value}, ToolRunContext('call', 'owner', '.', 1, 1))
            manager.wait.assert_awaited_with('fixture', timeout=expected)
        manager.interrupt.assert_not_called()

    async def test_batch_wait_returns_completed_and_still_running_members(self):
        manager = Mock()
        completed = AgentRun(id='done', status='completed', parent_session_id='owner')
        pending = AgentRun(id='pending', status='running', parent_session_id='owner')
        manager.target_for.return_value = pending
        manager.wait_many = AsyncMock(return_value=[completed, pending])
        tool = next(t for t in build_subagent_tools(manager) if t.schema.name == 'wait_agents')
        result = await tool.execute({'agent_ids': ['done', 'pending'], 'timeout': 0},
                                    ToolRunContext('call', 'owner', '.', 1, 1))
        self.assertEqual(result.data['completed'], 1)
        self.assertEqual(result.data['pending'], 1)
        self.assertEqual(result.data['timed_out'], ['pending'])
        manager.wait_many.assert_awaited_once_with(['done', 'pending'], timeout=0)

    async def test_sdk_batch_timeout_does_not_cancel_members(self):
        manager = SubagentManager(adapter=None, tools=None, prompts=None)
        gates = [asyncio.Event(), asyncio.Event()]
        tasks = [asyncio.create_task(gate.wait()) for gate in gates]
        states = [AgentRun(id=f'agent-{index}', status='running', task=task)
                  for index, task in enumerate(tasks)]
        manager._states.update({state.id: state for state in states})
        try:
            result = await manager.wait_many([state.id for state in states], timeout=.01)
            self.assertEqual([state.id for state in result], ['agent-0', 'agent-1'])
            self.assertTrue(all(not task.done() for task in tasks))
        finally:
            for gate in gates:
                gate.set()
            await asyncio.gather(*tasks)
