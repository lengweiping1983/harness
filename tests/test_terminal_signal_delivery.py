"""信号投递失败不得生成成功回执，不启动实际进程。"""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from harness.core.language import current_language
from harness.tools.builtin.terminal_tools import TerminalError, _TerminalConfig, _TerminalSession


class TerminalSignalDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def session(self, **methods):
        process = SimpleNamespace(pid=123, returncode=None, **methods)
        return _TerminalSession(session_id='fixture', owner='owner', kind='fixture',
                                name=None, cwd='.', proc=process, config=_TerminalConfig())

    async def test_posix_group_failures_preserve_cause_and_language(self):
        for language in ('en', 'zh-CN', 'en'):
            for failure in (PermissionError('Original denied'), ProcessLookupError('Original gone')):
                with self.subTest(language=language, failure=type(failure).__name__):
                    session = self.session()
                    send = Mock(side_effect=failure)
                    posix = SimpleNamespace(getpgid=Mock(return_value=456), killpg=send)
                    with current_language.set(language), patch('harness.tools.builtin.terminal_tools.sys.platform', 'linux'), patch('harness.tools.builtin.terminal_tools.os', posix):
                        with self.assertRaises(TerminalError) as caught:
                            await session.signal('SIGTERM')
                    self.assertIs(caught.exception.__cause__, failure)
                    self.assertIn(str(failure), str(caught.exception))
                    self.assertEqual(any('\u4e00' <= c <= '\u9fff' for c in str(caught.exception)), language == 'zh-CN')
                    send.assert_called_once()

    async def test_windows_failed_final_delivery_is_not_suppressed(self):
        for name, method in (('SIGKILL', 'kill'), ('SIGTERM', 'terminate')):
            failure = OSError('Original Windows failure')
            session = self.session(**{method: Mock(side_effect=failure)})
            with patch('harness.tools.builtin.terminal_tools.sys.platform', 'win32'):
                with self.assertRaises(TerminalError) as caught:
                    await session.signal(name)
            self.assertIs(caught.exception.__cause__, failure)
            getattr(session.proc, method).assert_called_once()

    async def test_success_uses_actual_group_and_foreground_transport_once(self):
        session = self.session()
        send = Mock()
        posix = SimpleNamespace(getpgid=Mock(return_value=456), killpg=send)
        with patch('harness.tools.builtin.terminal_tools.sys.platform', 'linux'), patch('harness.tools.builtin.terminal_tools.os', posix):
            self.assertEqual(await session.signal('SIGTERM'), {'delivered': True, 'targetPgid':456})
            self.assertEqual(send.call_args.args[0],456)
            send.assert_called_once()
        foreground = Mock()
        session = self.session(signal_foreground=foreground)
        with patch('harness.tools.builtin.terminal_tools.sys.platform', 'linux'):
            self.assertTrue((await session.signal('SIGINT'))['delivered'])
        foreground.assert_called_once()

    async def test_conpty_interrupt_waits_for_drain_and_reports_failure(self):
        import asyncio
        from unittest.mock import AsyncMock
        for fails in (False, True):
            entered, release = asyncio.Event(), asyncio.Event()
            failure = OSError('Original delayed write failure')

            async def drain():
                entered.set()
                await release.wait()
                if fails:
                    raise failure

            stdin = SimpleNamespace(write=Mock(), drain=AsyncMock(side_effect=drain))
            session = self.session(stdin=stdin, resize=Mock())
            with patch('harness.tools.builtin.terminal_tools.sys.platform', 'win32'):
                task = asyncio.create_task(session.signal('SIGINT'))
                await entered.wait()
                self.assertFalse(task.done())
                stdin.write.assert_called_once_with(b'\x03')
                release.set()
                if fails:
                    with self.assertRaises(TerminalError) as caught:
                        await task
                    self.assertIs(caught.exception.__cause__, failure)
                else:
                    self.assertTrue((await task)['delivered'])
            stdin.drain.assert_awaited_once()

    async def test_background_stop_keeps_operation_pending_until_delivery(self):
        import asyncio
        from unittest.mock import AsyncMock
        from harness.tools.builtin.job_tools import JobRegistry
        from harness.tools.builtin.terminal_tools import TerminalSendOperation, _SendProcess
        for fails in (False, True):
            entered, release = asyncio.Event(), asyncio.Event()
            failure = TerminalError('Original signal failure')

            async def deliver(name):
                entered.set()
                await release.wait()
                if fails:
                    raise failure
                return {'delivered': True}

            session = SimpleNamespace(signal=AsyncMock(side_effect=deliver))
            operation = TerminalSendOperation('fixture')
            process = _SendProcess(operation, session)
            task = asyncio.create_task(JobRegistry()._stop_owned_process(process))
            await entered.wait()
            self.assertFalse(operation.is_done)
            self.assertIsNone(process.returncode)
            release.set()
            if fails:
                with self.assertRaises(TerminalError) as caught:
                    await task
                self.assertIs(caught.exception, failure)
                self.assertFalse(operation.is_done)
                self.assertIsNone(process.returncode)
                operation.settle('inferred_idle')
            else:
                await task
                self.assertTrue(operation.cancel_requested)
                self.assertEqual((await operation.done())['reason'], 'session_exit')
            session.signal.assert_awaited_once_with('SIGINT')

    async def test_completed_background_wait_never_signals_live_terminal(self):
        from unittest.mock import AsyncMock
        from harness.tools.builtin.job_tools import JobRegistry
        from harness.tools.builtin.terminal_tools import TerminalSendOperation, _SendProcess
        for failed in (False, True):
            session = SimpleNamespace(signal=AsyncMock())
            operation = TerminalSendOperation('fixture')
            if failed:
                operation.fail(OSError('Original read failure'))
            else:
                operation.settle('inferred_idle')
            process = _SendProcess(operation, session)
            await JobRegistry()._stop_owned_process(process)
            session.signal.assert_not_awaited()
            if failed:
                with self.assertRaises(OSError):
                    await process.wait()
            else:
                self.assertEqual(await process.wait(), 0)
