"""团队主理人遗漏目标时应纠正调用，避免把收件箱等待误当全员等待。"""
from types import SimpleNamespace
import unittest

from harness.core.runs import AgentRun, COMPLETED
from harness.core.errors import ToolExecutionError
from harness.subagent.manager import SubagentManager
from harness.tools.builtin.subagent_tools import build_subagent_tools
from harness.tools.registry import ToolRunContext


class TeamWaitTargetTests(unittest.IsolatedAsyncioTestCase):
    def setup_wait(self, *, scope='prompt:current', consumed=False, incoming=False):
        session = SimpleNamespace(agent_messages=SimpleNamespace(
            pending=lambda: [SimpleNamespace(data={'message_id': 'msg'})] if incoming else [],
            receipt=lambda _: {'message_id': 'msg'}))
        manager = SubagentManager(adapter=None, tools=None, prompts=None)
        manager.session_for = lambda _: session
        manager.scope_resolver = lambda _: scope
        state = AgentRun(id='actual-member', session_id='child', parent_session_id='parent',
            root_session_id='parent', scope='prompt:current', status=COMPLETED)
        state._wait_consumed = consumed
        manager._states[state.id] = state
        tool = next(t for t in build_subagent_tools(manager) if t.schema.name == 'wait_agent')
        context = ToolRunContext(call_id='wait', session_id='parent', cwd=None, turn=1, step=1)
        return tool, context

    async def test_missing_target_lists_uncollected_team_member(self):
        tool, context = self.setup_wait()
        with self.assertRaises(ToolExecutionError) as failure:
            await tool.execute({'timeout': 0}, context)
        self.assertIn('actual-member', str(failure.exception))

    async def test_ordinary_inbox_wait_is_unchanged(self):
        tool, context = self.setup_wait(scope=None)
        result = await tool.execute({'timeout': 0}, context)
        self.assertEqual(result.data['incoming_messages'], 0)

    async def test_consumed_members_do_not_block_inbox_wait(self):
        tool, context = self.setup_wait(consumed=True)
        result = await tool.execute({'timeout': 0}, context)
        self.assertEqual(result.data['incoming_messages'], 0)

    async def test_pending_messages_remain_readable(self):
        tool, context = self.setup_wait(incoming=True)
        result = await tool.execute({'timeout': 0}, context)
        self.assertEqual(result.data['incoming_messages'], 1)
