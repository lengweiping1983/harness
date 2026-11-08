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
        self.assertEqual(first['prompt'], '继续')
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

    async def test_every_phase_sends_only_continue_across_language_changes(self):
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                for phase, kind in (('continue', 'continue'), ('generate', 'image'), ('deliver', 'image'),
                                    ('next_task', 'complete'), ('resume', 'interrupted')):
                    binding = self.bridge.bind(self.sid, self.url)['binding_id']
                    state = self.bridge.state(binding)
                    state = {**state, 'state': kind}
                    with patch.object(self.bridge, 'state', return_value=state):
                        intent = self.bridge.claim(binding, state['milestone'], phase)
                    self.assertEqual(intent['prompt'], '继续')
                    self.assertEqual(Companion(self.config).state(binding)['pending'], intent)

    async def test_completed_tasks_allow_one_next_task_intent_per_verified_completion(self):
        def complete(verified=True):
            self.runtime.store.event(self.sid, 'tool/result', {
                'name': 'harness_finalize', 'is_error': False, 'data': {'verified': verified}})

        complete()
        state = self.bridge.state(self.binding)
        self.assertEqual(state['state'], 'complete')
        with self.assertRaises(ValueError):
            self.bridge.claim(self.binding, state['milestone'], 'continue')
        first = self.bridge.claim(self.binding, state['milestone'], 'next_task')
        self.assertEqual(first['prompt'], '继续')
        self.bridge.receipt(self.binding, first['id'], 'confirmed')
        recreated = Companion(self.config)
        self.assertEqual(recreated.state(self.binding)['sent_phases'], ['next_task'])
        with self.assertRaises(ValueError):
            recreated.claim(self.binding, state['milestone'], 'next_task')
        complete(False)
        await self.runtime.call('local', 'harness_session_resume', {'session_id': self.sid})
        self.assertEqual(recreated.state(self.binding)['milestone'], state['milestone'])
        complete()
        next_state = recreated.state(self.binding)
        self.assertNotEqual(next_state['milestone'], state['milestone'])
        with self.assertRaises(ValueError):
            recreated.claim(self.binding, state['milestone'], 'next_task')
        second = recreated.claim(self.binding, next_state['milestone'], 'next_task')
        self.assertNotEqual(first['id'], second['id'])
        self.assertEqual(second['prompt'], '继续')

    def _interrupted_pipeline(self, error_code=''):
        """构造无已受理命令的中断事实，不启动真实命令。"""
        pid = 'pl_' + '1' * 32
        value = {'pipeline_id': pid, 'session_id': self.sid, 'project_root': str(self.config.projects['project'].root),
                 'revision': 0, 'status': 'interrupted', 'index': 0, 'skills': [], 'error_code': error_code,
                 'steps': [{'input': {'kind': 'exec', 'command': 'fixture'}, 'request_id': pid + ':0',
                            'job_id': None, 'evidence_id': None, 'status': 'pending'}]}
        self.runtime.store.event(self.sid, 'pipeline/state', value)

    async def test_confirmed_interruption_allows_one_resume_message_without_resuming_commands(self):
        self._interrupted_pipeline()
        state = self.bridge.state(self.binding)
        self.assertEqual(state['state'], 'interrupted')
        before = self.runtime.store.db.total_changes
        first = self.bridge.claim(self.binding, state['milestone'], 'resume')
        self.assertEqual(first['prompt'], '继续')
        self.bridge.receipt(self.binding, first['id'], 'not_sent')
        self.assertEqual(self.bridge.claim(self.binding, state['milestone'], 'resume'), first)
        self.bridge.receipt(self.binding, first['id'], 'confirmed')
        self.assertEqual(Companion(self.config).state(self.binding)['sent_phases'], ['resume'])
        with self.assertRaises(ValueError):
            self.bridge.claim(self.binding, state['milestone'], 'resume')
        self.assertEqual(self.runtime.store.db.total_changes, before)
        self.assertFalse(self.runtime.jobs.list_all_jobs())
        self.assertEqual(self.runtime._pipeline_record(self.sid, 'pl_' + '1' * 32)['status'], 'interrupted')

    async def test_unknown_interruption_and_running_or_unknown_calls_cannot_claim_resume(self):
        self._interrupted_pipeline('execution/outcome-unknown')
        state = self.bridge.state(self.binding)
        self.assertEqual(state['state'], 'blocked')
        with self.assertRaises(ValueError):
            self.bridge.claim(self.binding, state['milestone'], 'resume')
        value = self.runtime._pipeline_record(self.sid, 'pl_' + '1' * 32)
        value['error_code'] = 'execution/service-interrupted'
        self.runtime._pipeline_save(value)
        self.assertEqual(self.bridge.state(self.binding)['state'], 'interrupted')
        self.runtime.store.begin('local', self.sid, 'harness_exec', 'uncertain', 'fingerprint')
        self.assertEqual(self.bridge.state(self.binding)['state'], 'working')
        with self.runtime.store.db:
            self.runtime.store.db.execute("UPDATE calls SET status='outcome_unknown' WHERE scope=?", (self.sid,))
        state = self.bridge.state(self.binding)
        self.assertEqual(state['state'], 'blocked')
        with self.assertRaises(ValueError):
            self.bridge.claim(self.binding, state['milestone'], 'resume')

    async def test_reply_identity_allows_new_replies_but_never_bypasses_pending_dispatch(self):
        self.runtime.store.event(self.sid, 'tool/result', {
            'name': 'harness_finalize', 'is_error': False, 'data': {'verified': True}})
        first_key, second_key = 'a' * 16, 'b' * 16
        first_state = self.bridge.state(self.binding, first_key)
        first = self.bridge.claim(self.binding, first_state['milestone'], 'next_task', first_key)
        second_state = self.bridge.state(self.binding, second_key)
        self.assertEqual(second_state['pending'], first)
        with self.assertRaises(ValueError):
            self.bridge.claim(self.binding, second_state['milestone'], 'next_task', second_key)
        self.bridge.receipt(self.binding, first['id'], 'confirmed')
        recreated = Companion(self.config)
        self.assertEqual(recreated.state(self.binding, first_key)['sent_phases'], ['next_task'])
        second_state = recreated.state(self.binding, second_key)
        self.assertNotEqual(first_state['milestone'], second_state['milestone'])
        self.assertEqual(second_state['sent_phases'], [])
        second = recreated.claim(self.binding, second_state['milestone'], 'next_task', second_key)
        self.assertNotEqual(first['id'], second['id'])
        for invalid in ('', 'a', 'g' * 16, True, 123):
            with self.assertRaises(ValueError):
                recreated.state(self.binding, invalid)

    async def test_proven_unsent_receipt_can_reuse_same_intent_and_preserves_record(self):
        state = self.bridge.state(self.binding)
        first = self.bridge.claim(self.binding, state['milestone'], 'continue')
        self.bridge.receipt(self.binding, first['id'], 'not_sent')
        second = self.bridge.claim(self.binding, state['milestone'], 'continue')
        self.assertEqual(second, first)
        with self.bridge.database() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM dispatches').fetchone()[0], 1)

    async def test_http_terminal_claim_requires_matching_reply_identity_and_current_protocol(self):
        self.runtime.store.event(self.sid, 'tool/result', {
            'name': 'harness_finalize', 'is_error': False, 'data': {'verified': True}})
        with current_language.set('en'):
            server = create_server(self.config, 0)
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
        thread.start()
        address = f'http://127.0.0.1:{server.server_port}'

        def request(path, body=None):
            data = json.dumps(body).encode('utf-8') if body is not None else None
            headers = {'Authorization': 'Bearer ' + self.bridge.token, 'Content-Type': 'application/json'}
            try:
                with urlopen(Request(address + path, data=data, headers=headers), timeout=3) as response:
                    return response.status, json.loads(response.read())
            except HTTPError as error:
                with error:
                    return error.code, json.loads(error.read())

        try:
            status, health = await asyncio.to_thread(request, '/health')
            self.assertEqual((status, health['protocol_version']), (200, 2))
            path = '/state?binding_id=' + self.binding + '&reply_key=' + 'a' * 16
            status, state = await asyncio.to_thread(request, path)
            self.assertEqual((status, state['state']), (200, 'complete'))
            invalid = {'binding_id': self.binding, 'milestone': state['milestone'], 'phase': 'next_task', 'reply_key': None}
            self.assertEqual((await asyncio.to_thread(request, '/claim', invalid))[0], 409)
            self.assertEqual((await asyncio.to_thread(request, path + '&reply_key=' + 'b' * 16))[0], 409)
            valid = {**invalid, 'reply_key': 'a' * 16}
            status, dispatch = await asyncio.to_thread(request, '/claim', valid)
            self.assertEqual((status, dispatch['prompt']), (200, '继续'))
            self.assertEqual((await asyncio.to_thread(request, '/claim', valid))[0], 409)
            await asyncio.to_thread(request, '/receipt', {'binding_id': self.binding, 'id': dispatch['id'], 'outcome': 'confirmed'})
            status, state = await asyncio.to_thread(request, path)
            self.assertEqual((status, state['sent_phases']), (200, ['next_task']))
        finally:
            await asyncio.to_thread(server.shutdown)
            server.server_close()
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive())

    async def test_explicit_retry_of_proven_unsent_intent_uses_the_current_short_message(self):
        state = self.bridge.state(self.binding)
        intent = self.bridge.claim(self.binding, state['milestone'], 'continue')
        self.bridge.receipt(self.binding, intent['id'], 'not_sent')
        with self.bridge.database() as db, db:
            db.execute('UPDATE dispatches SET prompt=? WHERE id=?', ('Saved original instructions ' + intent['id'], intent['id']))
        retried = self.bridge.claim(self.binding, state['milestone'], 'continue')
        self.assertEqual(retried, {'id': intent['id'], 'prompt': '继续'})
        self.assertEqual(self.bridge.state(self.binding)['pending'], retried)

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

    async def test_continuous_reply_identity_does_not_repeat_image_generation(self):
        image = await self.runtime.call('local', 'harness_image_request', {'session_id': self.sid, 'request_id': 'image',
            'prompt': 'fixture', 'path': 'asset.png'})
        self.assertFalse(image['is_error'])
        first = self.bridge.state(self.binding, 'a' * 16)
        self.assertEqual(first['state'], 'image')
        dispatch = self.bridge.claim(self.binding, first['milestone'], 'generate', 'a' * 16)
        self.bridge.receipt(self.binding, dispatch['id'], 'confirmed')
        second = self.bridge.state(self.binding, 'b' * 16)
        self.assertEqual(first['milestone'], second['milestone'])
        self.assertEqual(second['sent_phases'], ['generate'])
        with self.assertRaises(ValueError):
            self.bridge.claim(self.binding, second['milestone'], 'generate', 'b' * 16)
        self.assertEqual(self.bridge.claim(self.binding, second['milestone'], 'deliver', 'b' * 16)['prompt'], '继续')

    async def test_interrupted_image_recovery_is_per_reply_and_does_not_repeat_generation(self):
        await self.runtime.call('local', 'harness_image_request', {'session_id': self.sid, 'request_id': 'image',
            'prompt': 'fixture', 'path': 'asset.png'})
        state = self.bridge.state(self.binding, 'a' * 16)
        with self.assertRaises(ValueError):
            self.bridge.claim(self.binding, state['milestone'], 'resume', 'a' * 16)
        generate = self.bridge.claim(self.binding, state['milestone'], 'generate', 'a' * 16)
        self.bridge.receipt(self.binding, generate['id'], 'confirmed')
        state = self.bridge.state(self.binding, 'b' * 16)
        with self.assertRaises(ValueError):
            self.bridge.claim(self.binding, state['milestone'], 'resume')
        recovery = self.bridge.claim(self.binding, state['milestone'], 'resume', 'b' * 16)
        self.bridge.receipt(self.binding, recovery['id'], 'confirmed')
        self.assertEqual(Companion(self.config).state(self.binding, 'b' * 16)['sent_phases'], ['generate', 'resume'])
        with self.assertRaises(ValueError):
            self.bridge.claim(self.binding, state['milestone'], 'resume', 'b' * 16)
        fresh = self.bridge.state(self.binding, 'c' * 16)
        self.assertEqual(fresh['sent_phases'], ['generate'])
        self.assertEqual(self.bridge.claim(self.binding, fresh['milestone'], 'resume', 'c' * 16)['prompt'], '继续')

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
        self.assertEqual(ready, {'protocol': 'harness-chat-companion', 'protocol_version': 2,
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
