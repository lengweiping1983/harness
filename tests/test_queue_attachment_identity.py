"""队列编辑只提交原文，保留结构化附件及完整版本保护。"""
import asyncio
import json
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from harness import Harness, HarnessConfig
from harness.app.queue_edit import queued_view
from harness.core.errors import HarnessError
from harness.core.language import current_language
from harness.web.api.v1 import build_router
from harness.web.context import WebContext
from harness.web.errors import Conflict
from harness.web.prompt import AttachmentPart, compose_prompt
from harness.web.router import Request


class QueueAttachmentIdentityTests(unittest.TestCase):
    def test_edit_readback_conflict_and_withdrawal_keep_original_and_references(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Harness(HarnessConfig(home=Path(directory)/'state', cwd=directory, api_key='offline',
                enable_mcp=False, enable_automations=False))
            loop = asyncio.new_event_loop()
            try:
                ctx = WebContext(app, loop)
                router = build_router(ctx)
                session = app.create_session()

                def invoke(method, run_id, body=None, version=None):
                    path = f'/api/v1/sessions/{session.id}/runs/{run_id}'
                    endpoint, params = router.resolve(method, path)
                    result = endpoint(Request(method, path, {'expected_version': [version]} if version else {}, {},
                        json.dumps(body).encode('utf-8') if body is not None else b''), **params)
                    return json.loads(result.body)

                with patch.object(ctx, 'call', side_effect=loop.run_until_complete), patch.object(app.runtime, '_start_record'):
                    for language in ('en', 'zh-CN', 'en'):
                        with current_language.set(language):
                            original = 'Original\n\n---\nThis message includes user text\n本条消息附带 Original'
                            part = AttachmentPart('Original.txt', 'text/plain', 7, text='Private')
                            refs = [{'attachmentId': 'a'*64, 'name': part.name, 'mediaType': part.media_type,
                                'size': part.size, 'isImage': False}]
                            record = app.runtime.submit(session.id, compose_prompt(original, [part]),
                                prompt_input={'text': original, 'parts': [asdict(part)], 'attachment_refs': refs})
                            before = queued_view(record)
                            self.assertEqual(before['text'], original)
                            self.assertEqual(invoke('GET', record.id)['text'], original)
                            for text in ('Edited user text\n\n---\n本条消息附带 preserved', ''):
                                result = invoke('PATCH', record.id, {'text': text, 'expected_version': before['edit_version']})
                                self.assertEqual(result['text'], text)
                                self.assertEqual(result['attachment_refs'], refs)
                                self.assertEqual(record.prompt_input['parts'], [asdict(part)])
                                self.assertEqual(record.prompt_input['text'], text)
                                self.assertEqual(record.text, compose_prompt(text, [part]))
                                self.assertEqual(invoke('GET', record.id)['text'], text)
                                with self.assertRaises(Conflict) as conflict:
                                    invoke('PATCH', record.id, {'text': 'Stale edit', 'expected_version': before['edit_version']})
                                self.assertEqual(conflict.exception.details['current']['text'], text)
                                self.assertEqual(conflict.exception.code, 'run/queue-changed')
                                before = result
                            invoke('DELETE', record.id, version=before['edit_version'])
                            withdrawn = invoke('GET', record.id)
                            self.assertEqual(withdrawn['text'], '')
                            self.assertEqual(withdrawn['attachment_refs'], refs)
                            self.assertEqual(withdrawn['error']['code'], 'run/removed')
                            plain = app.runtime.submit(session.id, 'No attachment')
                            with self.assertRaises(HarnessError) as empty:
                                invoke('PATCH', plain.id, {'text': '', 'expected_version': queued_view(plain)['edit_version']})
                            self.assertEqual(empty.exception.code, 'run/empty')
                            self.assertEqual(plain.text, 'No attachment')
            finally:
                loop.run_until_complete(app.close())
                loop.close()
