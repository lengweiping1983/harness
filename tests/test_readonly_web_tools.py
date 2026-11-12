"""只读成员可以发现并执行内置联网读取，同时继续拒绝未知副作用工具。"""
import unittest
from unittest.mock import AsyncMock, Mock

from harness.core.agent import AgentLoop
from harness.llm.types import ToolUseBlock
from harness.tools.builtin.web_tools import build_web_tools
from harness.tools.registry import ToolDefinition, ToolRegistry, ToolRunContext


class ReadonlyWebToolsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.registry = ToolRegistry()
        for tool in build_web_tools():
            self.registry.register(tool)
        self.unknown = AsyncMock(return_value='不应执行')
        self.registry.register(ToolDefinition({'name': 'unclassified'}, self.unknown))

    def test_task_and_recovery_readonly_keep_builtin_web_tools_visible(self):
        agent = AgentLoop(Mock(model='fixture'), self.registry, Mock())
        for mode in ('task_read_only', 'recovery_read_only'):
            with self.subTest(mode=mode):
                agent.task_read_only = mode == 'task_read_only'
                agent.recovery_read_only = mode == 'recovery_read_only'
                names = {schema.name for schema in agent._eligible_tool_schemas()}
                self.assertIn('web_fetch', names)
                self.assertIn('web_search', names)
                self.assertNotIn('unclassified', names)

    async def test_execution_gate_allows_builtin_reads_but_denies_unknown(self):
        for name, args in (('web_fetch', {'url': 'https://example.com'}),
                           ('web_search', {'query': '研究问题'})):
            with self.subTest(name=name):
                tool = self.registry.get(name)
                tool.execute = AsyncMock(return_value={})
                tool.to_content = lambda _args, _value: '读取完成'
                tool.describe = None
                result = await self.registry.execute_call(ToolUseBlock(id=name, name=name, input=args),
                    ToolRunContext(call_id=name, session_id='fixture', cwd='', turn=1, step=1,
                                   services={'task_read_only': True}))
                self.assertFalse(result.is_error)
                tool.execute.assert_awaited_once()
        result = await self.registry.execute_call(ToolUseBlock(id='unknown', name='unclassified', input={}),
            ToolRunContext(call_id='unknown', session_id='fixture', cwd='', turn=1, step=1,
                           services={'task_read_only': True}))
        self.assertTrue(result.is_error)
        self.unknown.assert_not_called()

    async def test_same_registry_readonly_denials_follow_language_without_running_tool(self):
        from harness.core.language import current_language
        from harness.i18n import tr
        modes = [({'task_read_only': True}, 'agent.task_read_only.denied', 'task_restricted'),
                 ({'recovery_read_only': True}, 'python.registry.9036ea01262c', 'recovery_restricted'),
                 ({'task_read_only': True, 'recovery_read_only': True},
                  'python.registry.9036ea01262c', 'recovery_restricted')]
        for language in ('en', 'zh-CN', 'en'):
            for services, key, flag in modes:
                with self.subTest(language=language, services=services), current_language.set(language):
                    result = await self.registry.execute_call(ToolUseBlock('blocked', 'unclassified', {}),
                        ToolRunContext('blocked', 'fixture', '', 1, 1, services=services))
                    self.assertTrue(result.is_error)
                    self.assertEqual(result.content[0].text, tr(key))
                    self.assertTrue(result.display[flag])
                    self.assertEqual(result.tool_use_id, 'blocked')
        self.unknown.assert_not_called()
