"""协作消息正文在目标读取前校验，SDK和工具不隐式转换文本。"""
import unittest
from unittest.mock import AsyncMock, Mock

from harness.core.errors import ToolExecutionError
from harness.core.language import current_language
from harness.i18n import tr
from harness.session.agent_messages import MAX_MESSAGE_BYTES
from harness.subagent.manager import SubagentManager
from harness.tools.builtin.subagent_tools import build_subagent_tools
from harness.tools.registry import ToolRunContext


class SubagentMessageTextTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_messages_never_resolve_or_deliver_to_targets(self):
        manager = SubagentManager(adapter=None, tools=None, prompts=None)
        manager.runs.current = Mock()
        manager.target_for = Mock()
        tool = next(t for t in build_subagent_tools(manager) if t.schema.name == 'send_message')
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                for value in (None, True, False, 1, 1.5, [], {}, '', '  ', '\ud800', 'x' * (MAX_MESSAGE_BYTES + 1)):
                    with self.subTest(language=language, value_type=type(value).__name__):
                        with self.assertRaises(ValueError) as error:
                            await manager.send_message('fixture', value, sender_session_id='owner', call_id='call', turn=1)
                        self.assertEqual(str(error.exception), tr('agent.message.invalid'))
                        with self.assertRaises(ToolExecutionError) as error:
                            await tool.execute({'agent_id': 'fixture', 'message': value}, ToolRunContext('call', 'owner', '.', 1, 1))
                        self.assertEqual(error.exception.message, tr('agent.message.invalid'))
        manager.runs.current.assert_not_called()
        manager.target_for.assert_not_called()

    async def test_valid_tool_message_preserves_original_body_and_receipt(self):
        manager = Mock()
        manager.get.return_value = None
        manager.send_message = AsyncMock(return_value={'message_id': 'original-receipt', 'status': 'accepted'})
        tool = next(t for t in build_subagent_tools(manager) if t.schema.name == 'send_message')
        for text in (' Original 原文 {error}\nSecond line ', 'x' * MAX_MESSAGE_BYTES):
            result = await tool.execute({'agent_id': 'parent', 'message': text}, ToolRunContext('call', 'owner', '.', 1, 1))
            manager.send_message.assert_awaited_with('parent', text.strip(), sender_session_id='owner', call_id='call', turn=1)
            self.assertEqual(result.data['receipt']['message_id'], 'original-receipt')
