"""供应商截断后的续写不因固定次数停止，用户取消仍然有效。"""
import json
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path

from harness import Harness, HarnessConfig
from harness.llm.transport import Transport
from harness.llm.types import StreamEvent


class ContinuationTransport(Transport):
    def __init__(self, *, thinking_only=False):
        self.requests = []
        self.thinking_only = thinking_only
        self.cancel = None

    async def post_stream(self, url, *, headers, body, timeout):
        self.requests.append(json.loads(body))
        final = len(self.requests) == 6
        if final and self.cancel is not None:
            self.cancel()
        delta = ({'content': '完成'} if final else
                 {'reasoning_content': '继续思考'} if self.thinking_only else {'content': '相同正文'})
        for chunk in ({'choices': [{'delta': delta, 'finish_reason': None}]},
                      {'choices': [{'delta': {}, 'finish_reason': 'stop' if final else 'length'}]}):
            yield ('data: ' + json.dumps(chunk) + '\n\n').encode()
        yield b'data: [DONE]\n\n'

    async def native_stream(self, messages, **options):
        """原生供应商可返回只有思考的截断事件，直接驱动真实轮次消费者。"""
        self.requests.append(list(messages))
        final = len(self.requests) == 6
        yield StreamEvent(kind='start')
        yield (StreamEvent(kind='text', text='完成') if final else
               StreamEvent(kind='thinking', thinking='继续思考'))
        yield StreamEvent(kind='done', stop_reason='stop' if final else 'max_tokens')


class OutputContinuationTests(unittest.IsolatedAsyncioTestCase):
    async def test_repeated_or_thinking_only_truncations_continue_to_completion(self):
        for thinking_only, native in ((False, False), (True, False), (True, True)):
            with self.subTest(thinking_only=thinking_only, native=native), tempfile.TemporaryDirectory() as folder:
                flags = {f.name: False for f in fields(HarnessConfig) if f.name.startswith('enable_')}
                transport = ContinuationTransport(thinking_only=thinking_only)
                app = Harness(HarnessConfig(api_key='fixture', model='fixture', home=Path(folder)/'state',
                    cwd=folder, max_steps=None, max_retries=0, **flags), transport=transport)
                try:
                    if native:
                        app.adapter.stream = transport.native_stream
                    session = app.create_session()
                    result = await app._agent_for(session).prompt(session, '继续完成任务')
                    self.assertEqual(result.reason, 'completed', result.error)
                    self.assertEqual(len(transport.requests), 6)
                    responses = [e for e in session.events if e.type == 'assistant/message']
                    self.assertEqual(len(responses), 6)
                    notes = [e for e in session.events if e.type == 'note' and e.data.get('kind') == 'output_continuation']
                    self.assertEqual(len(notes), 5)
                    self.assertEqual([e.data['response_seq'] for e in notes], [e.seq for e in responses[:-1]])
                    self.assertIsNone(session.open_turn)
                finally:
                    await app.close()

    async def test_user_can_cancel_after_repeated_truncations(self):
        with tempfile.TemporaryDirectory() as folder:
            flags = {f.name: False for f in fields(HarnessConfig) if f.name.startswith('enable_')}
            transport = ContinuationTransport()
            app = Harness(HarnessConfig(api_key='fixture', model='fixture', home=Path(folder)/'state',
                cwd=folder, max_steps=None, max_retries=0, **flags), transport=transport)
            try:
                session = app.create_session()
                agent = app._agent_for(session)
                transport.cancel = agent.cancel
                result = await agent.prompt(session, '继续完成任务')
                self.assertEqual(result.reason, 'aborted', result.error)
                self.assertEqual(len(transport.requests), 6)
                self.assertIsNone(session.open_turn)
                self.assertEqual(len([e for e in session.events if e.type == 'turn/end']), 1)
            finally:
                await app.close()
