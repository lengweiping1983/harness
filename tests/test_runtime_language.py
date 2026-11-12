"""模块导入后切换语言，模型提示与默认标签仍按调用上下文解析。"""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from harness.core.language import current_language
from harness.i18n import tr
from harness.llm.vision import omit_image_data
from harness.tools.command_output import render_command_output
from harness.tools.builtin.ralph_tools import bound_result
from harness.core.agent import AgentLoop
from harness.session.events import AppendOp
from harness.session.session import Session


class RuntimeLanguageTests(unittest.TestCase):
    def test_imported_helpers_follow_language_changes(self):
        image = 'data:image/png;base64,YQ=='
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                self.assertEqual(omit_image_data(image), tr('python.vision.5e8e9746c1f6'))
                label = tr('python.command_output.928f87d4507b')
                result = render_command_output({'exit_code': 0})
                self.assertIn(label, result)
                self.assertIn('custom', render_command_output({'exit_code': 0}, label='custom'))
                notice = tr('python.ralph_tools.e1f057685052')
                self.assertEqual(bound_result('x' * 1000, 200), 'x' * (200 - len(notice)) + notice)
                self.assertEqual(bound_result('x' * 1000, 5), notice[:5])
        self.assertEqual(image, 'data:image/png;base64,YQ==')


class InterruptedToolLanguageTests(unittest.IsolatedAsyncioTestCase):
    async def test_interrupted_unknown_result_uses_call_time_language(self):
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                session = Session()
                await session.start_turn()
                await session.append('assistant/message', {'content': [
                    {'type': 'tool_use', 'id': 'fixture', 'name': 'execute', 'input': {}}]}, surface_op=AppendOp())
                await session.append('tool/call', {'call_id': 'fixture', 'name': 'execute', 'status': 'running'})
                agent = SimpleNamespace(_unstarted_call_ids=set(), _emit=AsyncMock())
                await AgentLoop._settle_unfinished_tools(agent, session)
                result = session.latest('tool/result').data
                self.assertEqual(result['results'][0]['content'], tr('python.agent.7933271745ac'))
                self.assertFalse(result['display']['not_executed'])
                self.assertTrue(result['results'][0]['is_error'])
