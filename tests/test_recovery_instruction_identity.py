"""恢复回执保存独立指令正文，语言提示装配不参与身份判断。"""
import asyncio
import json
import tempfile
import unittest
from unittest.mock import patch

from harness import Harness, HarnessConfig
from harness.app.recovery import recovery_preview
from harness.core.language import current_language
from harness.web.errors import Conflict
from harness.core.runs import AgentRun
from harness.web.api.v1 import build_router
from harness.web.context import WebContext
from harness.web.router import Request


class RecoveryInstructionIdentityTests(unittest.TestCase):
    def test_route_persists_exact_instructions_separately_from_generated_prompt(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Harness(HarnessConfig(home=directory, cwd=directory, api_key='offline',
                enable_mcp=False, enable_automations=False))
            loop = asyncio.new_event_loop()
            try:
                ctx = WebContext(app, loop)
                session = app.create_session()
                path = f'/api/v1/sessions/{session.id}/continue'
                handler, params = build_router(ctx).resolve('POST', path)
                instructions = ' Original instructions 原文 {id}\n'
                with patch.object(ctx, 'call', side_effect=loop.run_until_complete), patch.object(
                        app.runtime, '_start_record') as start:
                    for index, language in enumerate(('en', 'zh-CN', 'en')):
                        for mode in ('inspect', 'continue'):
                            original = AgentRun(f'original-{index}-{mode}', session_id=session.id,
                                text='Original task 原文\n' * 2000, status='failed')
                            with app.runtime.db() as db:
                                app.runtime.storage.write(db, original, initial=True)
                            app.runtime.records[original.id] = original
                            with current_language.set(language):
                                version = recovery_preview(app, session.id, original.id)['version']
                                body = {'run_id': original.id, 'mode': mode, 'text': instructions,
                                    'recovery_version': version, 'verification_note': ' Verified 原文 '}
                                key = mode + ':' + original.id
                                result = handler(Request('POST', path, {}, {'Idempotency-Key': key},
                                    json.dumps(body).encode('utf-8')), **params)
                            run = json.loads(result.body)
                            self.assertEqual(run['recovery'], {'mode': mode, 'source_version': version,
                                'verification_note': 'Verified 原文', 'instruction_text': instructions})
                            self.assertTrue(run['text'].startswith(instructions))
                            self.assertIn(original.text, run['text'])
                            receipt = app.runtime.submission(session.id, key)
                            self.assertEqual(receipt.id, run['id'])
                            with app.runtime.db() as db:
                                stored = json.loads(db.execute('SELECT data FROM runs WHERE id=?', (run['id'],)).fetchone()[0])
                            self.assertEqual(stored['recovery'], run['recovery'])
                            with current_language.set('zh-CN' if language == 'en' else 'en'):
                                replay = handler(Request('POST', path, {}, {'Idempotency-Key': key},
                                    json.dumps(body).encode('utf-8')), **params)
                            self.assertEqual(json.loads(replay.body)['id'], run['id'])
                            for changes in ({'text': 'Other instructions'}, {'verification_note': 'Other evidence'},
                                            {'recovery_version': 'Other version'}, {'mode': 'continue' if mode == 'inspect' else 'inspect'}):
                                with self.assertRaises(Conflict) as caught:
                                    handler(Request('POST', path, {}, {'Idempotency-Key': key},
                                        json.dumps({**body, **changes}).encode('utf-8')), **params)
                                self.assertEqual(caught.exception.code, 'run/idempotency-conflict')


                    self.assertEqual(start.call_count, 6)
            finally:
                loop.run_until_complete(app.close())
                loop.close()
