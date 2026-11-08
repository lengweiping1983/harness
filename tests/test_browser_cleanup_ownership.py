"""浏览器操作与观察收尾在重复取消和失败下仍保留归属。"""
import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from harness.app.browser import BrowserService
from harness.app.browser_observation import BrowserObservation
from harness.core.errors import HarnessError


def originals(error):
    if isinstance(error, BaseExceptionGroup):
        return [item for child in error.exceptions for item in originals(child)]
    return [error]


class Page:
    url = 'https://example.invalid/'
    def __init__(self, context):
        self.context = context
        self.title = AsyncMock(return_value='Original title')
    def is_closed(self):return False
    def remove_listener(self, *args):pass


class BrowserCleanupOwnershipTests(unittest.IsolatedAsyncioTestCase):
    def service(self):
        service = BrowserService(Path('/fixture'))
        service.frames = SimpleNamespace(forget=AsyncMock(), close=AsyncMock())
        context = SimpleNamespace(close=AsyncMock(), pages=[])
        page = Page(context);context.pages.append(page)
        service.contexts['fixture'] = context
        return service, context, page

    def test_number_rejects_overflow_and_non_finite_browser_inputs(self):
        for value in (10 ** 4000, float('nan'), float('inf'), float('-inf'), 101, True, None, '12'):
            with self.subTest(value=type(value).__name__), self.assertRaises(HarnessError) as error:
                BrowserService._number({'x': value}, 'x', 100)
            self.assertEqual(error.exception.code, 'browser/input')
        self.assertEqual(BrowserService._number({'x': 12}, 'x', 100), 12)
        self.assertEqual(BrowserService._number({'x': 12.5}, 'x', 100), 12.5)

    async def test_observation_attempts_all_handles_and_retains_only_failed_for_retry(self):
        failure = OSError('Original disposal failure')
        first = SimpleNamespace(dispose=AsyncMock(side_effect=failure))
        second = SimpleNamespace(dispose=AsyncMock())
        observation = BrowserObservation('page', first, [], False)
        observation._handles = [first, second, first]
        with self.assertRaises(OSError) as error:await observation.dispose()
        self.assertIs(error.exception, failure)
        first.dispose.assert_awaited_once();second.dispose.assert_awaited_once()
        self.assertTrue(observation.disposed)
        first.dispose.side_effect = None
        await observation.dispose()
        self.assertEqual(first.dispose.await_count, 2)
        second.dispose.assert_awaited_once()
        self.assertEqual(observation._handles, [])
        await observation.dispose()
        self.assertEqual(first.dispose.await_count, 2)

    async def test_session_close_attempts_remaining_resources_and_keeps_failed_handles(self):
        service, context, page = self.service()
        failures = [OSError('Original context failure'), OSError('Original observation failure'), OSError('Original frame failure')]
        context.close.side_effect = failures[0]
        observation = SimpleNamespace(dispose=AsyncMock(side_effect=failures[1]))
        service._observations[page] = observation
        service.frames.forget.side_effect = failures[2]
        with self.assertRaises(BaseExceptionGroup) as error:await service.close_session('fixture')
        self.assertEqual({id(value) for value in originals(error.exception)}, {id(value) for value in failures})
        context.close.assert_awaited_once();observation.dispose.assert_awaited_once();service.frames.forget.assert_awaited_once()
        self.assertIn('fixture', service.contexts)
        self.assertIn('fixture', service._closing_sessions)
        self.assertTrue(service._observation_cleanup)
        context.close.side_effect = None;observation.dispose.side_effect = None;service.frames.forget.side_effect = None
        await service.close_session('fixture')
        self.assertNotIn('fixture', service.contexts)
        self.assertNotIn('fixture', service._closing_sessions)
        self.assertFalse(service._observation_cleanup)

    async def test_repeated_cancel_does_not_interrupt_single_session_close(self):
        service, context, _ = self.service()
        entered, release = asyncio.Event(), asyncio.Event()
        async def close():entered.set();await release.wait()
        context.close.side_effect = close
        task = asyncio.create_task(service.close_session('fixture'))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            task.cancel();await asyncio.sleep(0);task.cancel();await asyncio.sleep(0)
            self.assertFalse(task.done());context.close.assert_awaited_once()
        finally:release.set()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertNotIn('fixture', service.contexts)
        self.assertFalse(service._session_closures)

    async def test_execute_cancel_waits_for_operation_finally_and_preserves_failure(self):
        service, _, _ = self.service()
        entered, cleanup, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        failure = OSError('Original operation cleanup failure')
        async def execute(*args, **kwargs):
            entered.set()
            try:await asyncio.Future()
            finally:
                cleanup.set();await release.wait();raise failure
        service._execute = execute
        task = asyncio.create_task(service.execute('fixture', {'action':'observe'}))
        try:
            await asyncio.wait_for(entered.wait(), 1);task.cancel()
            await asyncio.wait_for(cleanup.wait(), 1)
            task.cancel();await asyncio.sleep(0)
            self.assertFalse(task.done())
        finally:release.set()
        with self.assertRaises(BaseExceptionGroup) as error:await task
        values = originals(error.exception)
        self.assertTrue(any(isinstance(value, asyncio.CancelledError) for value in values))
        self.assertTrue(any(value is failure or value.__cause__ is failure for value in values))
        self.assertNotIn('fixture', service._operations)
        self.assertEqual(len(service._history['fixture']), 1)

    async def test_stop_loading_cancel_finishes_detach_and_records_unknown_once(self):
        service, context, page = self.service()
        entered, detaching, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def send(*args):entered.set();await asyncio.Future()
        async def detach():detaching.set();await release.wait()
        client = SimpleNamespace(send=AsyncMock(side_effect=send), detach=AsyncMock(side_effect=detach))
        context.new_cdp_session = AsyncMock(return_value=client)
        service._owners['fixture'] = 'user'
        task = asyncio.create_task(service._stop_loading('fixture', {'control_revision':0,'page_id':service._id(page)}, 'user'))
        try:
            await asyncio.wait_for(entered.wait(), 1);task.cancel()
            await asyncio.wait_for(detaching.wait(), 1);task.cancel();await asyncio.sleep(0)
            self.assertFalse(task.done())
        finally:release.set()
        with self.assertRaises(asyncio.CancelledError):await task
        client.detach.assert_awaited_once()
        self.assertEqual(len(service._history['fixture']), 1)
        self.assertEqual(service._history['fixture'][0]['status'], 'unconfirmed')

    async def test_late_capture_after_control_change_is_disposed_not_registered(self):
        service, _, page = self.service()
        service.page = AsyncMock(return_value=page)
        service.state = AsyncMock(return_value={})
        entered, release = asyncio.Event(), asyncio.Event()
        observation = SimpleNamespace(dispose=AsyncMock(), targets=Mock(return_value=[]))
        async def capture(*args, **kwargs):entered.set();await release.wait();return observation
        with patch('harness.app.browser.BrowserObservation.capture', side_effect=capture):
            task = asyncio.create_task(service._execute('fixture', {'action':'observe'}, actor='agent'))
            try:
                await asyncio.wait_for(entered.wait(), 1)
                service._revisions['fixture'] = 1
            finally:release.set()
            with self.assertRaises(HarnessError) as error:await task
        self.assertEqual(error.exception.code, 'browser/frame-changed')
        observation.dispose.assert_awaited_once()
        observation.targets.assert_not_called()
        self.assertNotIn(page, service._observations)

    async def test_dialog_readback_keeps_origin_failure_without_replaying_action(self):
        service, _, _ = self.service()
        failure = HarnessError('Original operation failure', code='browser/action-unconfirmed')
        service.dialogs.respond = AsyncMock(return_value={'accepted':True})
        service.dialogs.current = Mock(return_value=None)
        service._operations['fixture'] = object()
        service._await_operation = AsyncMock(side_effect=failure)
        service._execute = AsyncMock(return_value={'targets':[]})
        dialog = SimpleNamespace(operation={'status':'waiting_dialog'}, kind='prompt')
        result = await service._respond_dialog('fixture', {}, dialog, 'agent')
        self.assertEqual(result['origin_operation_error'], failure.to_dict())
        service._execute.assert_awaited_once_with('fixture', {'action':'observe'}, actor='agent')
        self.assertTrue(result['dialog_response']['accepted'])

    async def test_global_close_attempts_engine_and_titles_after_context_failure(self):
        service, context, _ = self.service()
        failure = OSError('Original context close failure')
        context.close.side_effect = failure
        engine = service.engine = SimpleNamespace(stop=AsyncMock())
        service.titles = SimpleNamespace(close=AsyncMock(), forget=Mock())
        with self.assertRaises(OSError) as error:await service.close()
        self.assertIs(error.exception, failure)
        engine.stop.assert_awaited_once();service.titles.close.assert_awaited_once()
        context.close.side_effect = None
        await service.close()
        engine.stop.assert_awaited_once()
        self.assertFalse(service.contexts)