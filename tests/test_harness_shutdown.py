"""验证真实 Harness 实例的关闭入口保持清理归属。"""
import asyncio
import tempfile
import threading
import unittest
from dataclasses import fields
from pathlib import Path
from unittest.mock import AsyncMock, patch

from harness import Harness, HarnessConfig
from harness.core.errors import HarnessError
from tests.web_fixture import FixtureTransport


class HarnessShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        flags = {item.name: False for item in fields(HarnessConfig) if item.name.startswith('enable_')}
        self.host = Harness(HarnessConfig(api_key='fixture-only', home=root / 'state', cwd=root,
            model='fixture-model', **flags), transport=FixtureTransport())
        self.addAsyncCleanup(self.host.close)
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        original = self.host.terminal_setup.close

        async def close_terminal():
            self.entered.set()
            await self.release.wait()
            await original()

        self.host.terminal_setup.close = AsyncMock(side_effect=close_terminal)
        self.host.browser.close = AsyncMock(wraps=self.host.browser.close)
        self.host.project_execution.close = AsyncMock(wraps=self.host.project_execution.close)

    async def test_concurrent_and_repeated_close_share_all_resource_cleanup(self):
        first = asyncio.create_task(self.host.close())
        await self.entered.wait()
        second = asyncio.create_task(self.host.close())
        try:
            for _ in range(3):
                await asyncio.sleep(0)
            self.assertFalse(second.done())
        finally:
            self.release.set()
            await asyncio.gather(first, second)
        await self.host.close()
        self.host.terminal_setup.close.assert_awaited_once()
        self.host.browser.close.assert_awaited_once()
        self.assertTrue(self.host.runtime.closed)
        self.host.project_execution.close.assert_awaited_once()

    async def test_cancelled_waiter_waits_until_remaining_resources_are_closed(self):
        close = asyncio.create_task(self.host.close())
        await self.entered.wait()
        try:
            for _ in range(2):
                close.cancel()
                await asyncio.sleep(0)
            self.assertFalse(close.done())
        finally:
            self.release.set()
            result = (await asyncio.gather(close, return_exceptions=True))[0]
        self.assertIsInstance(result, asyncio.CancelledError)
        self.host.browser.close.assert_awaited_once()
        self.assertTrue(self.host.runtime.closed)
        self.host.project_execution.close.assert_awaited_once()

    async def test_cross_loop_cancel_keeps_cleanup_on_the_owner_loop(self):
        owner = asyncio.new_event_loop()
        started, entered, release = threading.Event(), threading.Event(), threading.Event()
        cleanup_loops = []

        def run_loop():
            asyncio.set_event_loop(owner)
            owner.call_soon(started.set)
            try:
                owner.run_forever()
            finally:
                owner.run_until_complete(owner.shutdown_asyncgens())
                owner.run_until_complete(owner.shutdown_default_executor())
                owner.close()

        async def close_terminal():
            cleanup_loops.append(asyncio.get_running_loop())
            entered.set()
            if not await asyncio.to_thread(release.wait, 5):
                raise TimeoutError('test cleanup was not released')
            await type(self.host.terminal_setup).close(self.host.terminal_setup)

        self.host.terminal_setup.close = AsyncMock(side_effect=close_terminal)
        self.host.runtime.loop = owner
        thread = threading.Thread(target=run_loop, name='harness-test-owner')
        thread.start()
        close = second = None
        try:
            self.assertTrue(await asyncio.to_thread(started.wait, 2))
            close = asyncio.create_task(self.host.close())
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            second = asyncio.create_task(self.host.close())
            for _ in range(2):
                close.cancel()
                await asyncio.sleep(0)
            self.assertFalse(close.done())
            self.host.browser.close.assert_not_awaited()
        finally:
            release.set()
            results = await asyncio.gather(*[task for task in (close, second) if task], return_exceptions=True)
            owner.call_soon_threadsafe(owner.stop)
            await asyncio.to_thread(thread.join, 5)
            self.host.runtime.loop = None
        self.assertFalse(thread.is_alive())
        self.assertIsInstance(results[0], asyncio.CancelledError)
        self.assertIsNone(results[1])
        self.assertEqual(cleanup_loops, [owner])
        self.host.terminal_setup.close.assert_awaited_once()
        self.host.browser.close.assert_awaited_once()
        self.host.project_execution.close.assert_awaited_once()
        self.assertTrue(self.host.runtime.closed)

    async def test_close_stops_submission_before_waiting_for_slow_resource_cleanup(self):
        entered, release = threading.Event(), threading.Event()
        session = self.host.create_session()

        def slow_login_cleanup():
            entered.set()
            if not release.wait(5):
                raise TimeoutError('login cleanup barrier was not released')

        self.release.set()
        with patch.object(self.host.chatgpt, 'cancel_login', side_effect=slow_login_cleanup):
            close = asyncio.create_task(self.host.close())
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                self.assertTrue(self.host.runtime.closed)
                with self.assertRaises(HarnessError) as caught:
                    self.host.runtime.submit(session.id, 'must not start during close')
                self.assertEqual(caught.exception.code, 'run/closed')
            finally:
                release.set()
                await asyncio.wait_for(close, 5)


class HarnessShutdownFailureTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        flags = {item.name: False for item in fields(HarnessConfig) if item.name.startswith('enable_')}
        self.host = Harness(HarnessConfig(api_key='fixture-only', home=root / 'state', cwd=root,
            model='fixture-model', **flags), transport=FixtureTransport())
        self.expected_error = None
        self.addAsyncCleanup(self.finish)

    async def finish(self):
        try:
            await self.host.close()
        except BaseException as exc:
            if exc is not self.expected_error:
                raise

    async def test_first_failure_does_not_skip_sessions_or_late_resources(self):
        session = self.host.create_session(title='fixture')
        failure = OSError('injected first cleanup failure')
        original = self.host.terminal_setup.close

        async def fail_after_close():
            await original()
            raise failure

        self.host.terminal_setup.close = AsyncMock(side_effect=fail_after_close)
        operations = [self.host.browser, self.host.project_execution, self.host.terminals]
        self.host.browser.close = AsyncMock(wraps=self.host.browser.close)
        self.host.project_execution.close = AsyncMock(wraps=self.host.project_execution.close)
        self.host.terminals.close_all = AsyncMock(wraps=self.host.terminals.close_all)
        self.host.mcp.aclose = AsyncMock(wraps=self.host.mcp.aclose)
        session.close = AsyncMock(wraps=session.close)
        self.expected_error = failure
        with self.assertRaises(OSError) as caught:
            await self.host.close()
        self.assertIs(caught.exception, failure)
        self.assertTrue(self.host.runtime.closed)
        session.close.assert_awaited_once()
        for resource in operations[:2]:
            resource.close.assert_awaited_once()
        operations[2].close_all.assert_awaited_once()
        self.host.mcp.aclose.assert_awaited_once()
        with self.assertRaises(OSError) as repeated:
            await self.host.close()
        self.assertIs(repeated.exception, failure)
        self.host.terminal_setup.close.assert_awaited_once()

    async def test_multiple_failures_and_cancelled_waiter_preserve_all_errors(self):
        from harness.core.language import current_language
        first_error, second_error = OSError('first fixture failure'), ValueError('second fixture failure')
        entered, release = asyncio.Event(), asyncio.Event()
        original_terminal, original_browser = self.host.terminal_setup.close, self.host.browser.close

        async def first():
            entered.set()
            await release.wait()
            await original_terminal()
            raise first_error

        async def second():
            await original_browser()
            raise second_error

        self.host.terminal_setup.close = AsyncMock(side_effect=first)
        self.host.browser.close = AsyncMock(side_effect=second)
        self.host.mcp.aclose = AsyncMock(wraps=self.host.mcp.aclose)
        with current_language.set('en'):
            cancelled = asyncio.create_task(self.host.close())
            await entered.wait()
            other = asyncio.create_task(self.host.close())
            try:
                for _ in range(2):
                    cancelled.cancel()
                    await asyncio.sleep(0)
                    self.assertFalse(cancelled.done())
            finally:
                release.set()
                results = await asyncio.gather(cancelled, other, return_exceptions=True)
        for result in results:
            self.assertIsInstance(result, ExceptionGroup)
            self.assertEqual(result.message, 'Resource cleanup failed')
            self.assertEqual(result.exceptions, (first_error, second_error))
        self.assertIs(results[0], results[1])
        self.expected_error = results[0]
        self.assertTrue(self.host.runtime.closed)
        self.host.mcp.aclose.assert_awaited_once()
        self.host.terminal_setup.close.assert_awaited_once()
        self.host.browser.close.assert_awaited_once()

    async def test_resource_cancellation_does_not_cancel_remaining_cleanup(self):
        cancelled = asyncio.CancelledError('resource cancelled')
        failure = ValueError('later resource failed')
        original_terminal, original_browser = self.host.terminal_setup.close, self.host.browser.close

        async def terminal():
            await original_terminal()
            raise cancelled

        async def browser():
            await original_browser()
            raise failure

        self.host.terminal_setup.close = AsyncMock(side_effect=terminal)
        self.host.browser.close = AsyncMock(side_effect=browser)
        self.host.terminals.close_all = AsyncMock(wraps=self.host.terminals.close_all)
        with self.assertRaises(BaseExceptionGroup) as caught:
            await self.host.close()
        self.expected_error = caught.exception
        self.assertEqual(caught.exception.exceptions, (cancelled, failure))
        self.assertTrue(self.host.runtime.closed)
        self.host.terminals.close_all.assert_awaited_once()
