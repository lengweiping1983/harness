"""完成式与真实智能体循环保留相同思考边界、空签名和分片签名。"""
from dataclasses import fields
from pathlib import Path
import tempfile
import unittest

from harness import Harness, HarnessConfig
from harness.core.jsonutil import to_jsonable
from tests.web_fixture import FixtureTransport
from harness.llm.types import StreamEvent, TextBlock, ThinkingBlock


class StreamMergeTests(unittest.IsolatedAsyncioTestCase):
    async def test_complete_and_agent_keep_identical_signed_blocks_and_reasoning_details(self):
        events = [
            StreamEvent('signature', signature='orphan'),
            StreamEvent('thinking', thinking='甲'),
            StreamEvent('thinking', thinking='乙', signature='signed'),
            StreamEvent('thinking', thinking='丙'),
            StreamEvent('signature', signature=None),
            StreamEvent('signature', signature=''),
            StreamEvent('thinking', thinking='丁'),
            StreamEvent('signature', signature='part-'),
            StreamEvent('signature', signature='two'),
            StreamEvent('text', text='Original 正文'),
            StreamEvent('signature', signature='after-text'),
            StreamEvent('thinking', thinking='tail'),
            StreamEvent('reasoning_details', reasoning_details=({'type': 'original', 'data': '原文😀'},)),
            StreamEvent('done', stop_reason='stop'),
        ]
        expected = [ThinkingBlock('', 'orphan'), ThinkingBlock('甲乙', 'signed'), ThinkingBlock('丙', ''),
                    ThinkingBlock('丁', 'part-two'), TextBlock('Original 正文'), ThinkingBlock('', 'after-text'),
                    ThinkingBlock('tail')]
        closed = []
        async def stream(messages, **options):
            try:
                for event in events:
                    yield event
                raise AssertionError('不能消费 done 后的事件')
            finally:
                closed.append(True)
        with tempfile.TemporaryDirectory() as folder:
            flags = {field.name: False for field in fields(HarnessConfig) if field.name.startswith('enable_')}
            app = Harness(HarnessConfig(api_key='fixture', model='fixture', home=Path(folder) / 'state',
                                        cwd=folder, max_retries=0, **flags), transport=FixtureTransport())
            try:
                app.adapter.stream = stream
                completed = await app.adapter.complete([])
                self.assertEqual(completed.content, tuple(expected))
                self.assertEqual(completed.reasoning_details, events[-2].reasoning_details)
                session = app.create_session()
                result = await app._agent_for(session).prompt(session, 'Original request')
                self.assertEqual(result.reason, 'completed', result.error)
                response = session.latest('assistant/message').data
                self.assertEqual(to_jsonable(response['content']), [block.to_dict() for block in expected])
                self.assertEqual(to_jsonable(response['reasoning_details']), list(events[-2].reasoning_details))
                self.assertIsNone(session.open_turn)
                self.assertEqual(closed, [True, True])
            finally:
                await app.close()
