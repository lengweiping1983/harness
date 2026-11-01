"""模型附件正文与用户原文独立，事件、投影和引导回执保持原文。"""
import asyncio
import json
import tempfile
import unittest
from dataclasses import asdict, fields
from pathlib import Path

from harness import Harness, HarnessConfig
from harness.core.errors import HarnessError
from harness.core.language import current_language
from harness.session.events import user_message_text
from harness.web.prompt import AttachmentPart, compose_prompt
from harness.web.views import session_detail
from tests.agent_configuration_fixture import RecordingTransport


class AttachmentOriginalTextTests(unittest.IsolatedAsyncioTestCase):
    async def test_normal_input_preserves_original_and_attachment_only_input(self):
        for language in ('en', 'zh-CN', 'en'):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                flags = {item.name: False for item in fields(HarnessConfig) if item.name.startswith('enable_')}
                transport = RecordingTransport()
                app = Harness(HarnessConfig(home=root/'state', cwd=root, api_key='offline', language=language,
                    model='fixture', max_steps=2, max_retries=0, **flags), transport=transport)
                try:
                    for original in ('Original\n\n---\n本条消息附带 User 原文', ''):
                        session = app.create_session()
                        part = AttachmentPart('Original.txt', 'text/plain', len(b'Attached private body'), text='Attached private body')
                        refs = [{'attachmentId': 'a'*64, 'name': part.name, 'mediaType': part.media_type,
                            'size': part.size, 'isImage': False}]
                        with current_language.set(language):
                            prompt = compose_prompt(original, [part])
                        record = app.runtime.submit(session.id, prompt, prompt_input={
                            'text': original, 'parts': [asdict(part)], 'attachment_refs': refs})
                        await asyncio.wait_for(record.task, 5)
                        self.assertEqual(record.status, 'completed')
                        event = session.latest('user/message')
                        self.assertEqual(user_message_text(event.data), original)
                        self.assertEqual(event.data['text'], prompt)
                        self.assertEqual(event.data['original_text'], original)
                        messages = [item for item in session_detail(app, session.id)['messages'] if item.get('role') == 'user']
                        self.assertEqual(messages[0]['text'], original)
                        self.assertEqual([dict(item) for item in event.data['attachments']], refs)
                        self.assertIn('Attached private body', json.dumps(transport.requests[-1], ensure_ascii=False))
                finally:
                    await app.close()

    async def test_steering_keeps_model_prompt_and_original_receipt_separate(self):
        entered, release = asyncio.Event(), asyncio.Event()

        class PausedTransport(RecordingTransport):
            async def post_stream(self, *args, **kwargs):
                entered.set()
                await release.wait()
                async for chunk in super().post_stream(*args, **kwargs):
                    yield chunk

        with tempfile.TemporaryDirectory() as directory:
            flags = {item.name: False for item in fields(HarnessConfig) if item.name.startswith('enable_')}
            app = Harness(HarnessConfig(home=Path(directory)/'state', cwd=directory, api_key='offline',
                model='fixture', max_steps=3, max_retries=0, **flags), transport=PausedTransport())
            try:
                session = app.create_session()
                record = app.runtime.submit(session.id, 'Initial original')
                await asyncio.wait_for(entered.wait(), 5)
                raw = 'Original steering\n\n---\n本条消息附带 User text'
                prompt = raw + '\nAttached private body'
                accepted = await app.steer(session.id, prompt, original_text=raw, expected_turn=session.open_turn,
                    request_id='original-steering')
                self.assertEqual(accepted['receipt']['text'], raw)
                self.assertNotIn('original_text', accepted['receipt'])
                self.assertEqual(session.steering.records['original-steering']['text'], prompt)
                with self.assertRaises(HarnessError) as caught:
                    await app.steer(session.id, prompt, original_text='Different original', expected_turn=session.open_turn,
                        request_id='original-steering')
                self.assertEqual(caught.exception.code, 'run/idempotency-conflict')
                release.set()
                await asyncio.wait_for(record.task, 5)
                events = [event for event in session.events_of_type('user/message') if event.data.get('source') == 'steer']
                self.assertEqual(len(events), 1)
                self.assertEqual(user_message_text(events[0].data), raw)
                self.assertEqual(events[0].data['text'], prompt)
            finally:
                release.set()
                await app.close()
