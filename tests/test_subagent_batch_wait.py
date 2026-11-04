"""批量观察绑定真实成员身份，收尾、取消和模型结果各自保留语义。"""
import asyncio
import json
import unittest
from unittest.mock import AsyncMock, Mock

from harness.core.errors import ToolExecutionError
from harness.core.cleanup import join_cleanup
from harness.core.language import current_language
from harness.core.runs import AgentRun
from harness.i18n import tr
from harness.subagent.manager import SubagentManager
from harness.tools.builtin.subagent_tools import build_subagent_tools
from harness.tools.registry import ToolRunContext


class SubagentBatchWaitTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.manager = SubagentManager(adapter=None, tools=None, prompts=None)
        self.context = ToolRunContext('batch', 'parent', '.', 1, 1)
        self.tool = next(tool for tool in build_subagent_tools(self.manager)
                         if tool.schema.name == 'wait_agents')

    def member(self, identity, **values):
        state = AgentRun(id=identity, parent_session_id='parent', root_session_id='parent',
                         session_id='context-' + identity, **values)
        self.manager._states[identity] = state
        return state

    async def test_terminal_state_still_waits_for_owned_finalization(self):
        entered, release = asyncio.Event(), asyncio.Event()
        state = self.member('settling', status='completed')

        async def deliver(record):
            entered.set()
            await release.wait()

        state._on_finished = deliver
        finalization = self.manager._finalization(state)
        await entered.wait()
        try:
            result = await self.tool.execute({'agent_ids': [state.id], 'timeout': 0}, self.context)
            self.assertEqual(result.data['completed'], 0)
            self.assertEqual(result.data['pending'], 1)
            self.assertFalse(result.data['agents'][0]['settled'])
            self.assertFalse(getattr(state, '_wait_consumed', False))
            with self.assertRaises(TimeoutError):
                async with asyncio.timeout(.02):
                    await self.manager.wait_many([state.id], timeout=10)
            self.assertFalse(finalization.cancelled())
        finally:
            release.set()
            await finalization
        result = await self.tool.execute({'agent_ids': [state.id], 'timeout': 1}, self.context)
        self.assertEqual(result.data['completed'], 1)
        self.assertTrue(state._wait_consumed)

    async def test_single_wait_does_not_consume_a_terminal_member_during_cleanup(self):
        state = self.member('settling', status='completed')
        future = asyncio.get_running_loop().create_future()
        state.finalization_task = future
        tool = next(tool for tool in build_subagent_tools(self.manager) if tool.schema.name == 'wait_agent')
        self.manager.session_for = lambda _: None
        try:
            result = await tool.execute({'agent_id': state.id, 'timeout': 0}, self.context)
            self.assertFalse(result.data['settled'])
            self.assertFalse(getattr(state, '_wait_consumed', False))
        finally:
            future.set_result(None)

    async def test_delivery_failure_is_a_failure_in_the_model_body(self):
        state = self.member('delivery-failed', status='completed')

        async def fail(record):
            raise OSError('Original delivery error 原文')

        state._on_finished = fail
        with self.assertLogs('harness.subagent.manager', level='ERROR'):
            await self.manager._finalization(state)
        result = await self.tool.execute({'agent_ids': [state.id], 'timeout': 0}, self.context)
        self.assertEqual((result.data['completed'], result.data['failed']), (0, 1))
        self.assertIn('Original delivery error 原文', result.content[0].text)
        self.assertEqual(result.data['agents'][0]['status'], 'failed')

    async def test_repeated_observer_cancellation_keeps_member_running(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def work():
            entered.set()
            await release.wait()

        task = asyncio.create_task(work())
        state = self.member('active', status='running', task=task)
        observer = asyncio.create_task(self.manager.wait_many([state.id], timeout=30))
        await entered.wait()
        try:
            observer.cancel()
            await asyncio.sleep(0)
            observer.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await observer
            self.assertFalse(task.done())
            self.assertEqual(state.status, 'running')
            self.assertFalse(any(pending.get_name().startswith('subagent-wait-many-')
                                 for pending in asyncio.all_tasks() if not pending.done()))
        finally:
            release.set()
            await task

    async def test_whole_batch_is_authorized_before_any_wait(self):
        visible = self.member('visible', status='completed')
        hidden = self.member('hidden', status='completed')
        hidden.root_session_id = 'another-parent'
        self.manager.wait_many = AsyncMock()
        with self.assertRaises(ToolExecutionError):
            await self.tool.execute({'agent_ids': [visible.id, hidden.id]}, self.context)
        self.manager.wait_many.assert_not_awaited()
        self.assertFalse(getattr(visible, '_wait_consumed', False))

    async def test_cancelled_member_finalization_is_not_reported_as_success(self):
        finalization = asyncio.create_task(asyncio.sleep(0))
        finalization.cancel()
        await asyncio.gather(finalization, return_exceptions=True)
        state = self.member('cancelled-finalization', status='completed')
        state.finalization_task = finalization
        for timeout in (0, 1):
            with self.subTest(timeout=timeout), self.assertRaises(asyncio.CancelledError):
                await self.tool.execute({'agent_ids': [state.id], 'timeout': timeout}, self.context)
            self.assertFalse(getattr(state, '_wait_consumed', False))

    async def test_finalization_original_errors_are_not_observation_timeouts(self):
        for error in (ValueError('original value'), TimeoutError('original timeout'), KeyError('original lookup')):
            future = asyncio.get_running_loop().create_future()
            future.set_exception(error)
            state = self.member('failed-finalization', status='completed')
            state.finalization_task = future
            for timeout in (0, 1):
                with self.subTest(error=error, timeout=timeout), self.assertRaises(type(error)) as caught:
                    await self.tool.execute({'agent_ids': [state.id], 'timeout': timeout}, self.context)
                self.assertIs(caught.exception, error)
                self.assertFalse(getattr(state, '_wait_consumed', False))

    async def test_single_wait_preserves_cleanup_lookup_error(self):
        original = KeyError('original cleanup lookup')
        future = asyncio.get_running_loop().create_future()
        future.set_exception(original)
        state = self.member('lookup-failed', status='completed')
        state.finalization_task = future
        tool = next(tool for tool in build_subagent_tools(self.manager) if tool.schema.name == 'wait_agent')
        session = Mock()
        session.agent_messages.pending.return_value = []
        session.agent_messages.wait_pending = asyncio.Event().wait
        for inbox in (None, session):
            self.manager.session_for = lambda _: inbox
            for timeout in (0, 1):
                with self.subTest(inbox=inbox is not None, timeout=timeout), self.assertRaises(KeyError) as caught:
                    await tool.execute({'agent_id': state.id, 'timeout': timeout}, self.context)
                self.assertIs(caught.exception, original)
                self.assertFalse(getattr(state, '_wait_consumed', False))

    async def test_missing_targets_still_use_the_localized_unavailable_error(self):
        single = next(tool for tool in build_subagent_tools(self.manager) if tool.schema.name == 'wait_agent')
        self.manager.wait = AsyncMock(wraps=self.manager.wait)
        self.manager.wait_many = AsyncMock(wraps=self.manager.wait_many)
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                for tool, arguments in ((single, {'agent_id': 'missing'}),
                                        (self.tool, {'agent_ids': ['missing']})):
                    with self.subTest(language=language, tool=tool.schema.name), self.assertRaises(ToolExecutionError) as caught:
                        await tool.execute(arguments, self.context)
                    self.assertEqual(caught.exception.message,
                                     tr('python.subagent_tools.aebb26001e55', agent_id='missing'))
        self.manager.wait.assert_not_awaited()
        self.manager.wait_many.assert_not_awaited()

    async def test_interrupted_execution_with_confirmed_cleanup_is_a_failed_snapshot(self):
        task = asyncio.create_task(asyncio.sleep(1))
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        state = self.member('interrupted', status='interrupted', task=task)
        await self.manager._finalization(state)
        result = await self.tool.execute({'agent_ids': [state.id], 'timeout': 0}, self.context)
        self.assertEqual((result.data['completed'], result.data['failed'], result.data['pending']), (0, 1, 0))
        self.assertTrue(state._wait_consumed)

    async def test_cleanup_error_during_zero_timeout_batch_join_never_becomes_success(self):
        finalization = asyncio.get_running_loop().create_future()
        original = OSError('cleanup finished during observer join')
        execution = asyncio.create_task(join_cleanup(finalization))
        state = self.member('joining', status='completed', task=execution)
        state.finalization_task = finalization
        await asyncio.sleep(0)
        asyncio.get_running_loop().call_soon(finalization.set_exception, original)
        try:
            with self.assertRaises(OSError) as caught:
                await self.tool.execute({'agent_ids': [state.id], 'timeout': 0}, self.context)
            self.assertIs(caught.exception, original)
            self.assertFalse(getattr(state, '_wait_consumed', False))
        finally:
            await asyncio.gather(execution, return_exceptions=True)

    async def test_repeated_cancellation_preserves_all_original_cleanup_errors(self):
        originals = [ValueError('first original'), OSError('second original')]
        entered = [asyncio.Event(), asyncio.Event()]
        cleaning = [asyncio.Event(), asyncio.Event()]
        release = asyncio.Event()
        for identity in ('first', 'second'):
            self.member(identity, status='running')

        async def failing_wait(identity, timeout=None):
            index = ('first', 'second').index(identity)
            entered[index].set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning[index].set()
                await release.wait()
                raise originals[index]

        self.manager.wait = failing_wait
        observer = asyncio.create_task(self.manager.wait_many(['first', 'second'], timeout=30))
        await asyncio.gather(*(event.wait() for event in entered))
        observer.cancel('original cancellation')
        await asyncio.gather(*(event.wait() for event in cleaning))
        observer.cancel('later cancellation')
        await asyncio.sleep(0)
        self.assertFalse(observer.done())
        release.set()
        with self.assertRaises(BaseExceptionGroup) as caught:
            await observer
        self.assertIs(caught.exception.exceptions[0], originals[0])
        self.assertIs(caught.exception.exceptions[1], originals[1])
        self.assertIsInstance(caught.exception.exceptions[2], asyncio.CancelledError)
        self.assertEqual(caught.exception.exceptions[2].args, ('original cancellation',))
        self.assertEqual(len(caught.exception.exceptions), 3)

    async def test_sdk_rejects_invalid_batch_before_lookup(self):
        original = self.manager.get
        self.manager.get = Mock(wraps=original)
        for ids in (None, 'one', [], ['one', 'one'], [''], [False], ['one', {}], ['x'] * 33):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                await self.manager.wait_many(ids, timeout=0)
        for timeout in (True, '1', -1, float('nan'), float('inf')):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                await self.manager.wait_many(['one'], timeout=timeout)
        self.manager.get.assert_not_called()

    async def test_same_instance_language_switch_and_bounded_results(self):
        done = self.member('success', status='completed', result={'text': 'Original 原文 ' * 300})
        failed = self.member('failed', status='failed', error={'code': 'fixture', 'message': 'Original failure 原文'})
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                result = await self.tool.execute({'agent_ids': [failed.id, done.id], 'timeout': 0}, self.context)
                text = result.content[0].text
                self.assertTrue(text.startswith(tr('agent.message.wait_many.summary', completed=1, failed=1, pending=0)))
                body = json.loads(text.split('\n', 1)[1])
                self.assertEqual([row['agent_id'] for row in body['agents']], [failed.id, done.id])
                self.assertEqual(body['agents'][0]['error'], 'Original failure 原文')
                self.assertTrue(body['agents'][1]['result_truncated'])
                self.assertEqual(len(body['agents'][1]['result']), 2000)
                self.assertEqual(body, result.data)
                schema = self.tool.schema.resolve_description(language).to_dict()
                self.assertEqual(schema['description'], tr('agent.message.wait_many'))

    async def test_batch_retrieval_unblocks_team_inbox_wait(self):
        state = self.member('success', status='completed', scope='team:current')
        self.manager.scope_resolver = lambda _: state.scope
        session = Mock()
        session.agent_messages.pending.return_value = []
        self.manager.session_for = lambda _: session
        inbox = next(tool for tool in build_subagent_tools(self.manager) if tool.schema.name == 'wait_agent')
        with self.assertRaises(ToolExecutionError):
            await inbox.execute({'timeout': 0}, self.context)
        await self.tool.execute({'agent_ids': [state.id], 'timeout': 0}, self.context)
        result = await inbox.execute({'timeout': 0}, self.context)
        self.assertEqual(result.data['incoming_messages'], 0)
