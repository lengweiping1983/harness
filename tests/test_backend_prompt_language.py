"""本地协议、CLI 拒绝与退避续接使用调用时语言，不访问在线模型。"""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from harness.app.goal_runtime import GoalRuntime
from harness.core.errors import LlmContextLengthError, LlmResponseError
from harness.core.language import current_language
from harness.i18n import tr
from harness.llm.chatgpt import chat_chunks, responses_body
from harness.session.store import SessionStore
from harness.tools.builtin.goal_tools import GoalStore


class BackendPromptLanguageTests(unittest.IsolatedAsyncioTestCase):
    async def test_chatgpt_request_validation_keeps_codes_and_current_language(self):
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                for message, key, code in (
                    ({'role': 'assistant', 'reasoning_details': [{'type': 'response.reasoning', 'item': {}}]},
                     'reasoning_invalid', 'llm/chatgpt-reasoning'),
                    ({'role': 'user', 'content': [{'type': 'unknown'}]}, 'content_unsupported', 'llm/chatgpt-content'),
                ):
                    with self.subTest(language=language, key=key), self.assertRaises(LlmResponseError) as caught:
                        responses_body({'model': 'chatgpt/fixture', 'messages': [message]})
                    self.assertEqual(caught.exception.message, tr('chatgpt.protocol.' + key))
                    self.assertEqual(caught.exception.code, code)
                original = 'Original 用户内容'
                body = responses_body({'model': 'chatgpt/fixture', 'messages': [{'role': 'user', 'content': original}]})
                self.assertEqual(body['input'][0]['content'][0]['text'], original)

    async def test_chatgpt_stream_failures_close_source_and_localize_owned_errors(self):
        cases = [
            ({'type': 'response.output_item.done', 'item': {'type': 'reasoning', 'encrypted_content': 'opaque'}},
             'reasoning_identity', LlmResponseError, 'llm/chatgpt-reasoning'),
            ({'type': 'response.failed', 'response': {'error': {'code': 'context_length_exceeded'}}},
             'context_full', LlmContextLengthError, 'llm/context-length'),
            ({'type': 'error', 'error': {'code': 'context_window_exceeded'}},
             'context_full', LlmContextLengthError, 'llm/context-length'),
            ({'type': 'response.failed', 'response': {'error': {'message': 'private server detail'}}},
             'request_failed', LlmResponseError, 'llm/chatgpt-response'),
            ({'type': 'error', 'error': 'private server detail'},
             'request_failed', LlmResponseError, 'llm/chatgpt-response'),
        ]
        for language in ('en', 'zh-CN', 'en'):
            for event, key, kind, code in cases:
                closed = []
                async def source():
                    try:
                        packet = ('data: ' + json.dumps(event) + '\n\n').encode()
                        for offset in range(0, len(packet), 3):
                            yield packet[offset:offset + 3]
                    finally:
                        closed.append(True)
                with self.subTest(language=language, key=key), current_language.set(language):
                    with self.assertRaises(kind) as caught:
                        _ = [part async for part in chat_chunks(source())]
                    self.assertEqual(caught.exception.message, tr('chatgpt.protocol.' + key))
                    self.assertEqual(caught.exception.code, code)
                    self.assertNotIn('private', str(caught.exception))
                    self.assertEqual(closed, [True])

    async def test_chatgpt_success_preserves_opaque_state_and_original_content(self):
        item = {'type': 'reasoning', 'id': 'reason-原文', 'encrypted_content': 'opaque-secret'}
        events = [
            {'type': 'response.output_text.delta', 'delta': 'Original 回复😀'},
            {'type': 'response.output_item.done', 'item': item},
            {'type': 'response.completed', 'response': {'output': [item], 'usage': {'total_tokens': 7}}},
        ]
        for language in ('en', 'zh-CN'):
            async def source():
                for event in events:
                    yield ('data: ' + json.dumps(event, ensure_ascii=False) + '\n\n').encode()
            with current_language.set(language):
                packets = [json.loads(part.decode().removeprefix('data: ')) async for part in chat_chunks(source())]
                self.assertEqual(packets[0]['choices'][0]['delta'], {'content': 'Original 回复😀'})
                details = packets[1]['choices'][0]['delta']['reasoning_details']
                self.assertEqual(details, [{'type': 'response.reasoning', 'item': item}])
                self.assertEqual(len(packets), 3)
                self.assertEqual(packets[-1]['choices'][0]['finish_reason'], 'stop')
                self.assertEqual(packets[-1]['usage'], {'total_tokens': 7})
                body = responses_body({'model': 'chatgpt/fixture', 'messages': [
                    {'role': 'assistant', 'reasoning_details': details, 'content': 'Original 回复😀'}]})
                self.assertEqual(body['input'][0], item)

    async def test_delayed_goal_dispatch_uses_latest_host_language_once(self):
        with tempfile.TemporaryDirectory() as folder:
            sessions = SessionStore(folder)
            session = sessions.create()
            goals = GoalStore(sessions=sessions)
            goal = goals.create(session.id, objective='Original 用户目标', max_goal_rounds=None)
            runtime = SimpleNamespace(closed=False, active={}, queue=lambda _: [],
                queue_control=lambda _: {'paused': False}, submit=Mock())
            host = SimpleNamespace(goals=goals, runtime=runtime, config=SimpleNamespace(language='zh-CN'),
                bus=SimpleNamespace(on=Mock(), off=Mock()), plan=SimpleNamespace(is_active_session_id=lambda _: False),
                questions=SimpleNamespace(pending=lambda _: []))
            runner = GoalRuntime(host)
            self.addAsyncCleanup(runner.close)
            for old, new in (('zh-CN', 'en'), ('en', 'zh-CN'), ('zh-CN', 'en')):
                goals.defer_retry(session.id, goal_id=goal.id, delay=3600, error='original error')
                host.config.language = old
                with current_language.set(old):
                    runner.schedule(session.id)
                task = runner._scheduled[session.id]
                async def waiting():
                    while session.id not in runner._retry_wakes:
                        if task.done():
                            await task
                            self.fail('续接任务未进入退避')
                        await asyncio.sleep(0)
                await asyncio.wait_for(waiting(), 2)
                host.config.language = new
                goals.clear_retry(session.id, goal_id=goal.id)
                with current_language.set(new):
                    runner.wake(session.id)
                await asyncio.wait_for(task, 2)
                with current_language.set(new):
                    runtime.submit.assert_called_once_with(session.id, tr('python.goal_runtime.48cf5c7b3b57'), goal_id=goal.id)
                runtime.submit.reset_mock()
                self.assertEqual(goals.get(session.id).objective, 'Original 用户目标')
            for language in ('en', 'zh-CN', 'en'):
                with current_language.set(language):
                    self.assertEqual(await runner.command(session.id, '/goal help'), ('', tr('python.goal_runtime.d56266933350')))

    def test_cli_share_rejections_use_selected_language_before_startup(self):
        with tempfile.TemporaryDirectory() as folder:
            for language in ('en', 'zh-CN'):
                for options, key in (([], 'cli.share_requires_ui'),
                                     (['--ui', '--host', '0.0.0.0'], 'web.lan.loopback_required')):
                    result = subprocess.run([sys.executable, '-m', 'harness.cli', '--language', language,
                        '--share', 'http://192.168.1.2:8766', *options], capture_output=True, text=True, encoding='utf-8', timeout=10,
                        env={**os.environ, 'HARNESS_HOME': folder, 'PYTHONIOENCODING': 'utf-8'})
                    with current_language.set(language):
                        self.assertEqual(result.returncode, 2, result.stderr)
                        self.assertIn(tr(key), result.stderr)
                    self.assertEqual(result.stdout, '')
            self.assertEqual(list(Path(folder).iterdir()), [])
