"""持有同一组终端工具时按呈现语言生成反馈，终端正文保持原样。"""
import unittest
from harness.core.language import current_language
from harness.tools.builtin.terminal_tools import build_terminal_tools, _session_line


class TerminalFeedbackLanguageTests(unittest.IsolatedAsyncioTestCase):
    def test_registered_renderers_follow_language_without_starting_process(self):
        tools = {tool.schema.name: tool for tool in build_terminal_tools(None)}
        cases = [('terminal_open', {'sessionId': 'fixture', 'type': 'bash', 'status': {'kind': 'running'}}),
            ('terminal_send', {'text': '', 'wait': {'reason': 'timeout'}}),
            ('terminal_list', []),
            ('terminal_list', [{'sessionId': 'fixture', 'type': 'bash', 'pid': 7, 'status': {'kind': 'running'}}]),
            ('terminal_list', [{'sessionId': 'fixture', 'type': 'bash', 'pid': 7, 'status': {'kind': 'exited', 'exit_code': 0}}])]
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                for name, value in cases:
                    text = '\n'.join(block.text for block in tools[name].to_content({}, value))
                    self.assertEqual(any('\u4e00' <= c <= '\u9fff' for c in text), language == 'zh-CN', text)
                for status in ({'kind': 'running'}, {'kind': 'exited', 'exit_code': 3}, {'kind': 'exited', 'signal': 'SIGTERM'}):
                    text = _session_line(status)
                    self.assertEqual(any('\u4e00' <= c <= '\u9fff' for c in text), language == 'zh-CN')
                text = tools['terminal_open'].to_content({}, {'sessionId': 'fixture', 'type': 'bash', 'motd': 'Original 原文', 'status': {'kind': 'running'}})[0].text
                self.assertIn('Original 原文', text)
                self.assertIn('fixture', text)

    async def test_empty_read_feedback_uses_current_language_through_registry(self):
        from types import SimpleNamespace
        from harness.i18n import resources
        from harness.llm.types import ToolUseBlock
        from harness.tools.registry import ToolRegistry, ToolRunContext
        from harness.tools.builtin.terminal_tools import TerminalSessionService, _TerminalSession

        service = TerminalSessionService()
        session = _TerminalSession(session_id='pty-fixture', owner='owner', kind='bash',
            name=None, cwd='/fixture', proc=SimpleNamespace(pid=7, returncode=None), config=service.config)
        service._sessions[session.id] = session
        registry = ToolRegistry()
        with current_language.set('zh-CN'):
            for tool in build_terminal_tools(service):
                registry.register(tool)
        ctx = ToolRunContext('fixture', 'owner', '/fixture', 1, 1)
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                result = await registry.execute_call(ToolUseBlock('read-empty', 'terminal_read',
                    {'session_id': session.id}), ctx)
                self.assertFalse(result.is_error)
                self.assertEqual(result.tool_use_id, 'read-empty')
                self.assertEqual(result.content[0].text,
                    resources(language)['terminal.feedback.no_retained_output'] + '\n[lines: 0-0 of 0]')
                self.assertEqual(result.data['text'], '')
                self.assertEqual(result.data['totalLines'], 0)
                self.assertIsNone(session._reader_task)
        session.buffer.append('Original 用户输出 {name}\n')
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                result = await registry.execute_call(ToolUseBlock('read-original', 'terminal_read',
                    {'session_id': session.id}), ctx)
                self.assertEqual(result.content[0].text,
                    'Original 用户输出 {name}\n[lines: 0-1 of 1]')
                self.assertEqual(result.data['text'], 'Original 用户输出 {name}')

    async def test_background_input_labels_follow_call_language_without_replaying_input(self):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        from harness.i18n import resources
        from harness.llm.types import ToolUseBlock
        from harness.tools.registry import ToolRegistry, ToolRunContext
        from harness.tools.builtin.job_tools import JobRegistry
        from harness.tools.builtin.terminal_tools import TerminalSessionService, TerminalSendOperation, _TerminalSession

        service = TerminalSessionService()
        session = _TerminalSession(session_id='pty-fixture', owner='owner', kind='bash',
            name=None, cwd='/fixture', proc=SimpleNamespace(pid=7, returncode=None), config=service.config)
        service._sessions[session.id] = session
        async def accepted(*args, **kwargs):
            operation = TerminalSendOperation(session.id)
            operation.settle('stdin_read')
            return operation
        session.start_send = AsyncMock(side_effect=accepted)
        jobs = JobRegistry()
        registry = ToolRegistry()
        with current_language.set('zh-CN'):
            for tool in build_terminal_tools(service, registry=jobs):
                registry.register(tool)
        ctx = ToolRunContext('fixture', 'owner', '/fixture', 1, 1)
        try:
            count = 0
            for language in ('en', 'zh-CN', 'en'):
                for text in ('', ' \n ', 'Original 用户输入 {{language}}'):
                    with current_language.set(language):
                        result = await registry.execute_call(ToolUseBlock('background', 'terminal_send', {
                            'session_id': session.id, 'text': text, 'run_in_background': True}), ctx)
                        self.assertFalse(result.is_error)
                        job = jobs._jobs[result.data['jobId']]
                        await asyncio.wait_for(asyncio.shield(job.task), 2)
                        expected = (session.id + ': ' + text.strip() if text.strip() else
                            resources(language)['terminal.feedback.background_input_label'].format(id=session.id))
                        self.assertEqual(job.public()['label'], expected)
                        self.assertEqual(job.owner, ctx.session_id)
                        self.assertEqual(job.kind, 'pty-send')
                        self.assertEqual(job.status, 'completed')
                        count += 1
                        self.assertEqual(len(jobs._jobs), count)
                        self.assertEqual(session.start_send.await_count, count)
                        args, kwargs = session.start_send.await_args
                        self.assertEqual(args, (text,))
                        self.assertTrue(kwargs['submit'])
                        self.assertTrue(kwargs['background'])
                        self.assertEqual(result.display['completionScope'], 'terminal-wait')
        finally:
            await jobs.cancel_all()
