"""Agent 名称是原始身份；只有当前协议的空名称执行退场。"""
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from harness.agents.parser import AgentDefinition
from harness.agents.registry import AgentRegistry
from harness.agents.tool import build_agent_tool
from harness.core.events import EventBus
from harness.core.language import current_language
from harness.tools.registry import ToolRunContext


class AgentExactNameTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.registry = AgentRegistry()
        self.session = SimpleNamespace(id='fixture')
        self.mount = SimpleNamespace(set=AsyncMock(), session_of=lambda _: self.session)
        self.bus = EventBus()
        self.events = []
        self.bus.on('agent/loaded', lambda sid, value: self.events.append((sid, value)))
        self.tool = build_agent_tool(self.registry, self.mount, bus=self.bus)[0]
        self.context = ToolRunContext(call_id='fixture', session_id='fixture', cwd=None, turn=1, step=1,
                                      services={'session': self.session})

    async def test_registered_off_and_none_are_mounted_by_exact_name_in_each_language(self):
        for name in ('off', 'none'):
            self.registry.register(AgentDefinition(name=name, description='Original 描述'))
        for language in ('en', 'zh-CN', 'en'):
            for name in ('off', 'none'):
                with self.subTest(language=language, name=name), current_language.set(language):
                    self.mount.set.reset_mock()
                    result = await self.tool.execute({'name': name}, self.context)
                    self.mount.set.assert_awaited_once_with(self.session, name)
                    self.assertFalse(result.is_error)
                    self.assertEqual(result.display['agent'], name)
                    self.assertEqual(result.display['action'], 'mount')
                    self.assertEqual(self.events[-1][1]['name'], name)
                    self.assertIn('Original 描述', result.content[0].text)

    async def test_unknown_nonempty_exit_aliases_do_not_change_current_selection(self):
        self.registry.register(AgentDefinition(name='reviewer', description='Original 描述'))
        for language in ('en', 'zh-CN', 'en'):
            for name in ('off', 'none', 'Exit', '退出', '退场'):
                with self.subTest(language=language, name=name), current_language.set(language):
                    result = await self.tool.execute({'name': name}, self.context)
                    self.assertTrue(result.is_error)
                    self.assertEqual(result.display['requested'], name)
                    self.mount.set.assert_not_awaited()
                    self.assertEqual(self.events, [])

    async def test_empty_name_still_unmounts_once_and_emits_current_machine_event(self):
        for language in ('en', 'zh-CN', 'en'):
            for name in ('', '   '):
                with self.subTest(language=language, name=name), current_language.set(language):
                    self.mount.set.reset_mock()
                    self.events.clear()
                    result = await self.tool.execute({'name': name}, self.context)
                    self.mount.set.assert_awaited_once_with(self.session, None)
                    self.assertEqual(result.display, {'agent': None, 'action': 'off'})
                    self.assertEqual(self.events, [('fixture', {'action': 'off'})])


    def command_host(self):
        """仅构造当前命令所需上下文，不创建运行目录、模型或网络连接。"""
        from harness.app.harness import Harness
        host = object.__new__(Harness)
        host.agents = self.registry
        host.agent_mount = self.mount
        host.agents_for = lambda _: self.registry
        host.bus = self.bus
        self.mount.select_once = Mock()
        return host

    async def test_slash_command_with_task_uses_literal_agent_name(self):
        host = self.command_host()
        for name in ('off', 'exit', 'end'):
            self.registry.register(AgentDefinition(name=name, description='Original 描述'))
        for language in ('en', 'zh-CN', 'en'):
            for name in ('off', 'exit', 'end'):
                with self.subTest(language=language, name=name), current_language.set(language):
                    self.mount.select_once.reset_mock()
                    result = await host._apply_agent_command(self.session, '/agent ' + name + ' User task 原文')
                    self.assertEqual(result, ('User task 原文', ''))
                    self.mount.select_once.assert_called_once_with('fixture', self.registry.get(name))
                    self.mount.set.assert_not_awaited()

    async def test_bare_explicit_slash_off_retains_current_unmount_command(self):
        host = self.command_host()
        self.registry.register(AgentDefinition(name='off', description='Original 描述'))
        result = await host._apply_agent_command(self.session, '/agent off')
        self.assertEqual(result[0], '')
        self.mount.set.assert_awaited_once_with(self.session, None)
        self.mount.select_once.assert_not_called()
        self.assertEqual(self.events, [('fixture', {'action': 'off'})])

    async def test_undeclared_slash_exit_aliases_preserve_current_mount(self):
        host = self.command_host()
        for language in ('en', 'zh-CN', 'en'):
            for name in ('exit', 'end', '退出', '退场'):
                with self.subTest(language=language, name=name), current_language.set(language):
                    result = await host._apply_agent_command(self.session, '/agent ' + name)
                    self.assertEqual(result[0], '')
                    self.assertTrue(result[1])
                    self.mount.set.assert_not_awaited()
                    self.mount.select_once.assert_not_called()
                    self.assertEqual(self.events, [])
