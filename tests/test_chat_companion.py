"""只读桥在普通聊天接续时保留归属、进展和未知发送事实。"""
import asyncio
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from harness.execution.config import ExecutionConfig, ProjectConfig
from harness.execution.runtime import ExecutionRuntime
from harness.mcp_server.companion import Companion, chat_url, create_server
from harness.core.language import current_language
from harness.i18n import tr


class ChatCompanionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name).resolve()
        project = root / 'project'
        project.mkdir()
        self.config = ExecutionConfig(root / 'state', {'project': ProjectConfig(project, writable=True, commands=True)})
        self.runtime = ExecutionRuntime(self.config)
        self.addAsyncCleanup(self.runtime.close)
        self.sid = self.runtime.store.open_session('local', 'project', 'fixture', str(project))['session_id']
        self.bridge = Companion(self.config)
        self.url = 'https://chatgpt.com/c/00000000-0000-0000-0000-000000000001'
        self.binding = self.bridge.bind(self.sid, self.url)['binding_id']

    async def test_binding_lists_only_authorized_sessions_and_never_claims_another_chat(self):
        self.assertEqual(self.bridge.sessions()['sessions'][0]['session_id'], self.sid)
        self.assertEqual(self.bridge.state(self.binding)['url'], self.url)
        foreign = self.runtime.store.open_session('other', 'project', 'private', str(self.config.projects['project'].root))['session_id']
        with self.assertRaises(Exception):
            self.bridge.bind(foreign, self.url)
        for url in ('https://evil.example/c/00000000-0000-0000-0000-000000000001', 'https://chatgpt.com/', self.url + '/other'):
            with self.assertRaises(ValueError):
                chat_url(url)

    async def test_claim_is_durable_and_unknown_result_never_creates_a_second_message(self):
        state = self.bridge.state(self.binding)
        self.assertEqual(state['state'], 'continue')
        first = self.bridge.claim(self.binding, state['milestone'], 'continue')
        self.assertIn(self.sid, first['prompt'])
        recreated = Companion(self.config)
        pending = recreated.state(self.binding)['pending']
        self.assertEqual(pending['id'], first['id'])
        recreated.receipt(self.binding, first['id'], 'unknown')
        with self.assertRaises(ValueError):
            recreated.claim(self.binding, state['milestone'], 'continue')
        recreated.receipt(self.binding, first['id'], 'confirmed')
        self.assertEqual(recreated.state(self.binding)['sent_phases'], ['continue'])
        with self.assertRaises((ValueError, sqlite3.IntegrityError)):
            recreated.claim(self.binding, state['milestone'], 'continue')

    async def test_proven_unsent_receipt_can_reuse_same_intent_and_preserves_record(self):
        state = self.bridge.state(self.binding)
        first = self.bridge.claim(self.binding, state['milestone'], 'continue')
        self.bridge.receipt(self.binding, first['id'], 'not_sent')
        second = self.bridge.claim(self.binding, state['milestone'], 'continue')
        self.assertEqual(second, first)
        with self.bridge.database() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM dispatches').fetchone()[0], 1)

    async def test_reading_resume_does_not_create_new_continuation_milestones(self):
        original = self.bridge.state(self.binding)['milestone']
        for _ in range(3):
            await self.runtime.call('local', 'harness_session_resume', {'session_id': self.sid})
        self.assertEqual(self.bridge.state(self.binding)['milestone'], original)
        await self.runtime.call('local', 'harness_checkpoint', {'session_id': self.sid, 'request_id': 'checkpoint',
            'expected_revision': 0, 'summary': 'fixture', 'next_steps': ['next']})
        self.assertNotEqual(self.bridge.state(self.binding)['milestone'], original)

    async def test_working_is_based_on_open_job_and_call_receipts_not_service_availability(self):
        idle = self.bridge.state(self.binding)
        self.assertEqual(idle['state'], 'continue')
        self.assertEqual(idle['activity'], {'running_jobs': 0, 'running_calls': 0, 'pipeline_status': ''})
        self.runtime.store.event(self.sid, 'job/started', {'job_id': 'receipt-job'})
        running = self.bridge.state(self.binding)
        self.assertEqual(running['state'], 'working')
        self.assertEqual(running['activity']['running_jobs'], 1)
        self.runtime.store.event(self.sid, 'job/finished', {'job_id': 'receipt-job'})
        self.assertEqual(self.bridge.state(self.binding)['state'], 'continue')
        self.runtime.store.begin('local', self.sid, 'harness_checkpoint', 'active-call', 'fingerprint')
        self.assertEqual(self.bridge.state(self.binding)['activity']['running_calls'], 1)
        self.assertEqual(self.bridge.state(self.binding)['state'], 'working')

    async def test_image_pipeline_is_detected_without_changing_executor_database(self):
        image = await self.runtime.call('local', 'harness_image_request', {'session_id': self.sid, 'request_id': 'image',
            'prompt': 'fixture', 'path': 'asset.png'})
        result = await self.runtime.call('local', 'harness_pipeline_start', {'session_id': self.sid, 'request_id': 'pipeline',
            'steps': [{'kind': 'image', 'image_id': image['data']['image_id']}]})
        task = self.runtime._pipeline_tasks.get(result['data']['pipeline_id'])
        if task:
            await asyncio.wait_for(task, 5)
        before = self.runtime.store.db.total_changes
        state = self.bridge.state(self.binding)
        self.assertEqual(state['state'], 'image')
        self.bridge.claim(self.binding, state['milestone'], 'generate')
        self.assertEqual(self.runtime.store.db.total_changes, before)
        with patch.object(self.runtime.store, 'event', side_effect=AssertionError('read-only')):
            self.bridge.state(self.binding)

    async def test_offline_and_unknown_commands_are_not_automatically_replayed(self):
        self.assertTrue(self.bridge.executor_alive())
        self.runtime.store.begin('local', self.sid, 'harness_exec', 'uncertain', 'fingerprint')
        await self.runtime.close()
        self.assertFalse(self.bridge.executor_alive())
        self.assertEqual(self.bridge.state(self.binding)['state'], 'offline')
        self.runtime = ExecutionRuntime(self.config)
        self.addAsyncCleanup(self.runtime.close)
        self.assertEqual(self.bridge.state(self.binding)['state'], 'blocked')

    async def test_pairing_key_is_reused_but_corrupt_key_is_not_replaced(self):
        self.assertEqual(Companion(self.config).token, self.bridge.token)
        path = self.config.home / 'companion-key.json'
        path.write_text('{"token":false}', encoding='utf-8')
        original = path.read_bytes()
        with self.assertRaises(ValueError):
            Companion(self.config)
        self.assertEqual(path.read_bytes(), original)

    async def test_health_separates_missing_offline_ready_and_invalid_without_rewriting_executor(self):
        before = self.runtime.store.db.total_changes
        ready = self.bridge.health()
        self.assertEqual(ready, {'protocol': 'harness-chat-companion', 'protocol_version': 1,
                                'executor': 'ready', 'session_count': 1})
        self.assertEqual(self.runtime.store.db.total_changes, before)
        await self.runtime.close()
        self.assertEqual(self.bridge.health()['executor'], 'offline')
        with tempfile.TemporaryDirectory() as folder:
            config = ExecutionConfig(Path(folder) / 'state', self.config.projects)
            bridge = Companion(config)
            self.assertEqual(bridge.health()['executor'], 'missing')
            path = config.home / 'executor.sqlite3'
            self.assertFalse(path.exists())
            path.write_bytes(b'original corrupt database')
            self.assertEqual(bridge.health()['executor'], 'invalid')
            self.assertEqual(path.read_bytes(), b'original corrupt database')

    async def test_corrupt_dispatch_is_preserved_and_cannot_be_replaced(self):
        state = self.bridge.state(self.binding)
        intent = self.bridge.claim(self.binding, state['milestone'], 'continue')
        with closing(sqlite3.connect(self.config.home / 'companion.sqlite3')) as db, db:
            db.execute("UPDATE dispatches SET status='corrupt' WHERE id=?", (intent['id'],))
        with self.assertRaises(ValueError):
            self.bridge.state(self.binding)
        with closing(sqlite3.connect(self.config.home / 'companion.sqlite3')) as db:
            self.assertEqual(db.execute('SELECT status FROM dispatches').fetchone()[0], 'corrupt')

    async def test_http_listener_requires_pairing_and_actual_host_and_keeps_configuration_language(self):
        with current_language.set('en'):
            server = create_server(self.config, 0)
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
        thread.start()
        try:
            address = f'http://127.0.0.1:{server.server_port}'
            def request(path, headers):
                try:
                    with urlopen(Request(address + path, headers=headers), timeout=3) as response:
                        return response.status, response.read(), dict(response.headers)
                except HTTPError as error:
                    return error.code, error.read(), dict(error.headers)
            with current_language.set('zh-CN'):
                denied = await asyncio.to_thread(request, '/sessions', {})
                allowed = await asyncio.to_thread(request, '/sessions', {'Authorization': 'Bearer ' + self.bridge.token})
                host = await asyncio.to_thread(request, '/sessions', {'Authorization': 'Bearer ' + self.bridge.token, 'Host': 'evil.example'})
                health_denied = await asyncio.to_thread(request, '/health', {})
                health = await asyncio.to_thread(request, '/health', {'Authorization': 'Bearer ' + self.bridge.token})
            self.assertEqual(denied[0], 403)
            with current_language.set('en'):
                self.assertIn(tr('execution.companion.denied').encode(), denied[1])
            self.assertEqual(allowed[0], 200)
            self.assertIn(self.sid.encode(), allowed[1])
            self.assertEqual(host[0], 403)
            self.assertEqual(health_denied[0], 403)
            self.assertEqual(json.loads(health[1])['executor'], 'ready')
            self.assertNotIn(self.bridge.token, health[1].decode())
            self.assertNotIn('Access-Control-Allow-Origin', allowed[2])
            self.assertEqual(allowed[2]['Cache-Control'], 'no-store')
        finally:
            await asyncio.to_thread(server.shutdown)
            server.server_close()
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive())
