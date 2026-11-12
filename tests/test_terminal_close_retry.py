"""终端停止已确认后的读取收尾允许重试，不重复投递终止。"""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from harness.core.language import current_language
from harness.tools.builtin.terminal_tools import _TerminalConfig, _TerminalSession


class TerminalCloseRetryTests(unittest.IsolatedAsyncioTestCase):
    def make_session(self):
        process = SimpleNamespace(pid=123, returncode=None, _transport=Mock())

        async def stop():
            process.returncode = 0

        process.stop_owned = AsyncMock(side_effect=stop)
        session = _TerminalSession(session_id='fixture', owner='owner', kind='fixture',
                                   name='fixture', cwd='.', proc=process, config=_TerminalConfig())
        return session, process

    async def test_reader_timeout_can_finish_without_stopping_process_again(self):
        session, process = self.make_session()
        release = asyncio.Event()
        session._reader_task = asyncio.create_task(release.wait())
        try:
            self.assertEqual(await session.close(), 'cleanup-failed')
            self.assertFalse(session.is_closed)
            self.assertFalse(session._reader_task.done())
            process._transport.close.assert_not_called()
            release.set()
            await session._reader_task
            self.assertEqual(await session.close(), 'closed')
            self.assertIsNone(session._cleanup_error)
            self.assertEqual(await session.close(), 'already-closing')
            process.stop_owned.assert_awaited_once()
            process._transport.close.assert_called_once()
        finally:
            release.set()
            if session._reader_task is not None:
                await session._reader_task

    async def test_cancel_after_confirmed_stop_can_retry_remaining_cleanup(self):
        session, process = self.make_session()
        release = asyncio.Event()
        session._reader_task = asyncio.create_task(release.wait())
        closing = asyncio.create_task(session.close())
        while process.returncode is None:
            await asyncio.sleep(0)
        closing.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await closing
        release.set()
        await session._reader_task
        self.assertEqual(await session.close(), 'closed')
        process.stop_owned.assert_awaited_once()
        process._transport.close.assert_called_once()

    async def test_unconfirmed_stop_then_exited_retains_manual_verification_guard(self):
        for language in ('en', 'zh-CN', 'en'):
            session, process = self.make_session()
            failure = OSError('unconfirmed stop')

            async def stop():
                process.returncode = 0
                raise failure

            process.stop_owned.side_effect = stop
            with current_language.set(language):
                self.assertEqual(await session.close(), 'cleanup-failed')
                self.assertEqual(await session.close(), 'cleanup-failed')
            self.assertFalse(session.is_closed)
            self.assertEqual(any('\u4e00' <= char <= '\u9fff' for char in session._cleanup_error),
                             language == 'zh-CN')
            process.stop_owned.assert_awaited_once()
            process._transport.close.assert_not_called()
