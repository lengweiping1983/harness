"""协作列表与当前运行状态契约一致，不放宽对话可见性。"""
import unittest
from types import SimpleNamespace

from harness.core.errors import ToolExecutionError
from harness.core.language import current_language
from harness.core.runs import AgentRun, ACTIVE_STATES, TERMINAL_STATES
from harness.i18n import tr
from harness.llm.types import ToolUseBlock
from harness.tools.builtin.subagent_tools import build_subagent_tools
from harness.tools.registry import ToolRegistry, ToolRunContext


class SubagentListContractTests(unittest.IsolatedAsyncioTestCase):
    def test_only_current_collaboration_tools_are_registered(self):
        expected = {'spawn_agent', 'followup_task', 'list_agents', 'wait_agent',
                    'wait_agents', 'send_message', 'interrupt_agent', 'member_evidence'}
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                tools = build_subagent_tools(SimpleNamespace())
                names = [tool.schema.name for tool in tools]
                self.assertEqual(set(names), expected)
                self.assertEqual(len(names), len(expected))

    def fixture(self):
        statuses = sorted(ACTIVE_STATES | TERMINAL_STATES)
        rows = [AgentRun(id=status, status=status, description='Original 原文') for status in statuses]
        hidden = AgentRun(id='Hidden-other-conversation', status='queued')
        manager = SimpleNamespace(list_states=lambda: rows + [hidden], visible_to=lambda state, owner: state is not hidden)
        tool = next(tool for tool in build_subagent_tools(manager) if tool.schema.name == 'list_agents')
        ctx = ToolRunContext('listing', 'fixture', None, 1, 1)
        return statuses, rows, tool, ctx

    async def test_every_current_status_can_be_filtered_through_registered_tool(self):
        statuses, rows, tool, ctx = self.fixture()
        registry = ToolRegistry()
        registry.register(tool)
        self.assertEqual(tool.schema.parameters['properties']['status']['enum'], statuses)
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                for status in statuses:
                    result = await registry.execute_call(ToolUseBlock(id='listing', name='list_agents', input={'status': status}), ctx)
                    self.assertFalse(result.is_error)
                    self.assertEqual([row['id'] for row in result.data], [status])
                all_rows = await tool.execute({}, ctx)
                self.assertEqual([row['id'] for row in all_rows.data], [row.id for row in rows])
                limited = await tool.execute({'limit': 1}, ctx)
                self.assertEqual(len(limited.data), 1)
                self.assertNotIn('Hidden-other-conversation', str(all_rows.data))

    async def test_invalid_filter_and_limit_return_bilingual_errors_without_mutation(self):
        _, rows, tool, ctx = self.fixture()
        before = [row.to_dict() for row in rows]
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                for value in (None, True, 3, [], '', 'obsolete'):
                    with self.assertRaises(ToolExecutionError) as error:
                        await tool.execute({'status': value}, ctx)
                    self.assertEqual(error.exception.message, tr('subagent.list.invalid_status'))
                for value in (None, True, False, '1', 1.5, float('inf'), []):
                    with self.assertRaises(ToolExecutionError) as error:
                        await tool.execute({'limit': value}, ctx)
                    self.assertEqual(error.exception.message, tr('python.subagent_tools.98d238b75b0f', p0=repr(value)))
                for value in (0, -1):
                    with self.assertRaises(ToolExecutionError) as error:
                        await tool.execute({'limit': value}, ctx)
                    self.assertEqual(error.exception.message, tr('python.subagent_tools.f8e280da49c4'))
        self.assertEqual([row.to_dict() for row in rows], before)
