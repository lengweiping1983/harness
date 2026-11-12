"""审批说明的来源、契约、版本及广播边界。"""
import asyncio
import copy
import unittest

from harness.core.events import EventBus
from harness.interaction.questions import QuestionBroker, QuestionError
from harness.interaction.permission_operation import normalized_operation
from harness.llm.types import ToolUseBlock
from harness.tools.builtin.interaction_tools import build_ask_user_question_tools
from harness.tools.registry import ToolRegistry, ToolRunContext
from harness.web.shared_projection import frame


class PermissionOperationMetadataTests(unittest.IsolatedAsyncioTestCase):
    def operation(self):
        return {'tool': 'OriginalTool', 'cwd': '/Original/原文', 'effect': 'execute',
                'approval': {'policy': 'request_approval', 'reasons': ['command_execution']}}

    async def test_invalid_approval_never_publishes_or_waits(self):
        variants = [None, {}, {'policy': 'full_access', 'reasons': ['command_execution']},
            {'policy': 'request_approval', 'reasons': []}, {'policy': 'request_approval', 'reasons': ['invented']},
            {'policy': 'request_approval', 'reasons': ['command_execution'] * 2},
            {'policy': 'request_approval', 'reasons': ['command_execution'], 'review': 'unavailable'},
            {'policy': 'auto_review', 'reasons': ['command_execution']},
            {'policy': 'auto_review', 'reasons': ['command_execution'], 'review': 'invented'},
            {'policy': 'request_approval', 'reasons': ['command_execution'], 'extra': 'invalid'}]
        for approval in variants:
            with self.subTest(approval=approval):
                bus, published = EventBus(), []
                bus.on('user_question/ask', lambda payload: published.append(payload))
                broker = QuestionBroker(bus)
                operation = {**self.operation(), 'approval': approval}
                with self.assertRaises(QuestionError):
                    await broker.ask(session_id='owner', questions=[{'id': 'permission', 'question': 'Original question', 'operation': operation}])
                self.assertEqual(published, [])
                self.assertEqual(broker.pending(), [])

    async def test_operation_is_copied_and_revision_binds_reasons_in_pending_and_shared_frames(self):
        broker = QuestionBroker()
        operation = self.operation()
        question = {'id': 'permission', 'question': 'Original question 原文', 'operation': operation}
        task = asyncio.create_task(broker.ask(session_id='child', parent_session_id='parent', questions=[question]))
        try:
            await asyncio.sleep(0)
            record = broker.pending('parent')[0]
            snapshot = copy.deepcopy(record)
            operation['approval']['reasons'].append('dangerous_tool')
            self.assertEqual(broker.pending('parent')[0], snapshot)
            projected = frame({'type': 'user_question/ask', **record})
            self.assertEqual(projected['questions'][0]['operation'], snapshot['questions'][0]['operation'])
            original_version = record['revision']
            stored = broker._pending[record['question_id']]
            stored.questions[0]['operation']['approval']['reasons'].append('dangerous_tool')
            self.assertNotEqual(stored.revision, original_version)
            self.assertEqual(stored.route_session_id, 'parent')
            self.assertEqual(stored.session_id, 'child')
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_model_cannot_supply_host_approval_metadata(self):
        bus, published = EventBus(), []
        bus.on('user_question/ask', lambda payload: published.append(payload))
        registry = ToolRegistry()
        registry.register(build_ask_user_question_tools(QuestionBroker(bus))[0])
        result = await registry.execute_call(ToolUseBlock(id='model-call', name='ask_user_question', input={
            'questions': [{'id': 'permission', 'question': 'Original question', 'operation': self.operation(),
                           'options': [{'label': 'One'}, {'label': 'Two'}]}]}),
            ToolRunContext(call_id='model-call', session_id='owner', cwd=None, turn=1, step=1))
        self.assertTrue(result.is_error)
        self.assertEqual(published, [])

    def test_existing_operation_without_approval_is_not_invented_or_modified(self):
        operation = {'tool': 'Original', 'cwd': '/Original', 'effect': 'unknown'}
        self.assertEqual(normalized_operation(operation), operation)
