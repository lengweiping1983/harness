"""派工正文拒绝隐式字符串转换，非法输入不得创建上下文。"""
import unittest
from unittest.mock import AsyncMock, Mock

from harness.core.errors import ToolExecutionError
from harness.core.language import current_language
from harness.core.runs import AgentRun
from harness.i18n import tr
from harness.subagent.manager import SubagentManager
from harness.tools.builtin.subagent_tools import build_subagent_tools
from harness.tools.registry import ToolRunContext


class SubagentSpawnTextTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_text_rejected_before_sdk_or_tool_side_effects(self):
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                contexts = Mock()
                manager = SubagentManager(adapter=None, tools=None, prompts=None, contexts=contexts)
                manager.session_resolver = Mock(return_value=None)
                configured = AsyncMock()
                tool = next(t for t in build_subagent_tools(manager, configured_spawn=configured) if t.schema.name == 'spawn_agent')
                for field, values, key in [('prompt', (None, True, 1, 1.5, [], {}, '', '  '), 'subagent.spawn.invalid_prompt'),
                                           ('description', (None, True, 1, [], {}), 'subagent.spawn.invalid_description')]:
                    for value in values:
                        args = {'prompt': 'Original 原文 {x}', 'description': 'Description'}
                        args[field] = value
                        with self.subTest(language=language, field=field, value=value):
                            with self.assertRaises(ValueError) as error:
                                manager.spawn(**args, parent_session_id='owner')
                            self.assertEqual(str(error.exception), tr(key))
                            with self.assertRaises(ToolExecutionError) as error:
                                await tool.execute(args, ToolRunContext('fixture', 'owner', '.', 1, 1))
                            self.assertEqual(error.exception.message, tr(key))
                self.assertEqual(manager.list_states(), [])
                self.assertEqual(contexts.mock_calls, [])
                manager.session_resolver.assert_not_called()
                configured.assert_not_awaited()

    async def test_valid_text_and_omitted_description_preserve_original_content(self):
        manager = Mock()
        manager.spawn.return_value = AgentRun(id='fixture', description='Original')
        tool = next(t for t in build_subagent_tools(manager) if t.schema.name == 'spawn_agent')
        for description in ('  Description 原文 {x}  ', ''):
            await tool.execute({'prompt': '  Original 原文 {x}\nSecond line  ', 'description': description}, ToolRunContext('fixture', 'owner', '.', 1, 1))
            self.assertEqual(manager.spawn.call_args.kwargs['prompt'], 'Original 原文 {x}\nSecond line')
            self.assertEqual(manager.spawn.call_args.kwargs['description'], description.strip())
