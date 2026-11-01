"""成员协议身份不能把布尔值当作另一成员的整数ID。"""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from harness.core.errors import ToolExecutionError
from harness.core.language import current_language
from harness.core.runs import AgentRun
from harness.i18n import tr
from harness.tools.builtin.workflow_host import WorkflowGuest, WorkflowLimits


class WorkflowChildIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_read_and_dispose_keep_registered_child_and_schema(self):
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                state = AgentRun(id='Original-child', status='completed', result={'text': 'Original 原文😀'})
                manager = SimpleNamespace(spawn=Mock(return_value=state), wait=AsyncMock(), interrupt=AsyncMock())
                guest = WorkflowGuest(manager, script='', meta={'name': 'Original'}, args=None, limits=WorkflowLimits())
                identity = await guest._rpc_start_child({'prompt': 'Original prompt'})
                self.assertEqual(identity['callId'], 1)
                child = guest._children[1]
                await child.wait_task
                schema_marker = {'type': 'object'}
                guest._schemas[1] = schema_marker
                for invalid in (True, False, 0, -1, 1.0, '1', None, []):
                    for method in (guest._rpc_child_result, guest._rpc_dispose_child):
                        with self.subTest(language=language, value=invalid, method=method.__name__):
                            with self.assertRaises(ToolExecutionError) as error:
                                await method({'callId': invalid})
                            self.assertEqual(error.exception.message, tr('workflow.child.call_id'))
                            self.assertIs(guest._children[1], child)
                            self.assertIs(guest._schemas[1], schema_marker)
                guest._schemas.pop(1)
                result = await guest._rpc_child_result({'callId': 1})
                self.assertEqual(result['stopReason'], 'completed')
                self.assertEqual(result['output'], [{'type': 'text', 'text': 'Original 原文😀'}])
                await guest._rpc_dispose_child({'callId': 2})
                self.assertIs(guest._children[1], child)
                await guest._rpc_dispose_child({'callId': 1})
                await guest._rpc_dispose_child({'callId': 1})
                self.assertEqual(guest._children, {})
                self.assertEqual(guest._schemas, {})
                manager.interrupt.assert_not_awaited()
                manager.spawn.assert_called_once()
