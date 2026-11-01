"""读取失败保留原文与进程状态，前台及后台等待均报告失败。"""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from harness.core.language import current_language
from harness.tools.builtin.terminal_tools import (
    TerminalError, TerminalSendOperation, TerminalSessionService, build_terminal_tools,
    _TerminalConfig, _TerminalSession, _SendProcess,
)


class TerminalOutputFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_reader_keeps_output_and_fails_both_waiters_without_stopping(self):
        failure = OSError('Original {diagnostic}')
        process = SimpleNamespace(pid=7, returncode=None,
            stdout=SimpleNamespace(read=AsyncMock(side_effect=[b'Original output\n', failure])),
            stop_owned=AsyncMock(), stdin=SimpleNamespace(write=lambda data: None, drain=AsyncMock()))
        session = _TerminalSession(session_id='fixture', owner='owner', kind='fixture',
            name=None, cwd='.', proc=process, config=_TerminalConfig())
        foreground, background = TerminalSendOperation(session.id), TerminalSendOperation(session.id)
        session._ops.extend([foreground, background])
        session._foreground = foreground
        session.start_reader()
        await session._reader_task
        for operation in (foreground, background):
            with self.assertRaises(OSError) as caught:
                await operation.done()
            self.assertIs(caught.exception, failure)
            self.assertEqual(operation.read_output()[0], 'Original output\n')
        adapter = _SendProcess(background, session)
        with self.assertRaises(OSError) as caught:
            await adapter.wait()
        self.assertIs(caught.exception, failure)
        self.assertEqual(adapter.returncode, 1)
        self.assertEqual(session.status(), {'kind': 'running'})
        service = TerminalSessionService()
        service._sessions[session.id] = session
        tool = next(tool for tool in build_terminal_tools(service) if tool.schema.name == 'terminal_read')
        from harness.i18n import tr
        from harness.tools.registry import ToolRunContext
        context = ToolRunContext('read', 'owner', '.', 1, 1)
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                stream = await session.read_stream(0, 0)
                self.assertEqual(stream['text'], 'Original output\n')
                self.assertEqual(stream['terminal']['output_error'],
                    {'key': 'ui.terminal.outputFailed', 'params': {'error': str(failure)}})
                value = await tool.execute({'session_id': session.id}, context)
                rendered = tool.to_content({}, value)[0].text
                self.assertIn(tr('ui.terminal.outputFailed', error=str(failure)), rendered)
                self.assertIn('Original output', rendered)
                self.assertEqual(value['output_error'], stream['terminal']['output_error'])
                with self.assertRaises(TerminalError) as caught:
                    await session.start_send('must not execute', submit=True)
                self.assertEqual(any('\u4e00' <= char <= '\u9fff' for char in str(caught.exception)), language == 'zh-CN')
                with self.assertRaises(TerminalError):
                    await session.write_input('must not execute')
        process.stdin.drain.assert_not_awaited()
        process.stop_owned.assert_not_awaited()


class TerminalTranscriptBoundaryTests(unittest.TestCase):
    def test_control_characters_are_removed_only_from_model_transcript(self):
        """控制字节不进入模型记录；原始终端重放仍保持原文。"""
        from harness.tools.terminal_transcript import TerminalTranscript
        from harness.tools.terminal_stream import TerminalStream
        raw = 'Original 中文😀\x7f\x80\x81\x84\x85\x86\x8f\x91\x9c\nNext\trow\r\b'
        expected = 'Original 中文😀\nNext\trow\r\b'
        for cut in range(len(raw) + 1):
            transcript, stream = TerminalTranscript(), TerminalStream()
            chunks = [raw[:cut], raw[cut:]]
            self.assertEqual(''.join(transcript.feed(chunk) for chunk in chunks), expected)
            for chunk in chunks:
                stream.append(chunk)
            self.assertEqual(stream.read(0)['text'], raw)

    def test_split_escape_sequences_do_not_swallow_following_unicode(self):
        """跨块颜色与标题控制序列移除后，后续中英文正文完整保留。"""
        from harness.tools.terminal_transcript import TerminalTranscript
        raw = 'Before\x1b[31m中文\x1b[0m\x1b]0;hidden title\x1b\\After😀'
        for cut in range(len(raw) + 1):
            transcript = TerminalTranscript()
            self.assertEqual(transcript.feed(raw[:cut]) + transcript.feed(raw[cut:]), 'Before中文After😀')
