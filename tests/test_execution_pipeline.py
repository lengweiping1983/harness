"""验证真实执行器流水线在断连、生图交接和故障下保留执行事实。"""
import asyncio
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from PIL import Image

from harness.core.language import current_language
from harness.execution.config import ExecutionConfig, ProjectConfig
from harness.execution.runtime import ExecutionRuntime
from harness.mcp_server.server import required_scope


class ExecutionPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.project = self.root / 'project'
        self.project.mkdir()
        self.config = ExecutionConfig(self.root / 'state', {'project': ProjectConfig(self.project,
            writable=True, commands=True, verification={'check': (sys.executable, '-c', "from pathlib import Path; assert Path('done.txt').read_text() == 'done'")})})
        self.runtime = ExecutionRuntime(self.config)
        self.addAsyncCleanup(self.close_runtime)
        self.sid = self.runtime.store.open_session('local', 'project', 'fixture', str(self.project))['session_id']

    async def close_runtime(self):
        await self.runtime.close()

    async def call(self, name, **args):
        return await self.runtime.call('local', name, {'session_id': self.sid, **args})

    def command(self, code):
        return ('& ' if os.name == 'nt' else '') + f'"{sys.executable}" -c "{code}"'

    async def start(self, steps, **kwargs):
        result = await self.call('harness_pipeline_start', request_id='pipeline', steps=steps, **kwargs)
        self.assertFalse(result['is_error'], result)
        return result['data']['pipeline_id']

    async def settled(self, pid):
        task = self.runtime._pipeline_tasks.get(pid)
        if task is not None:
            await asyncio.wait_for(asyncio.shield(task), 15)
        result = await self.call('harness_pipeline_get', pipeline_id=pid)
        self.assertFalse(result['is_error'], result)
        return result['data']['pipelines'][0]

    async def test_command_sequence_and_current_verification_continue_without_client_polling(self):
        pid = await self.start([
            {'kind': 'exec', 'command': self.command("from pathlib import Path; Path('done.txt').write_text('done')")},
            {'kind': 'verify', 'verification_id': 'check'},
        ])
        value = await self.settled(pid)
        self.assertEqual(value['status'], 'completed', value)
        self.assertEqual(value['index'], 2)
        evidence = value['steps'][1]['evidence_id']
        final = await self.call('harness_finalize', request_id='final', evidence_ids=[evidence])
        self.assertFalse(final['is_error'], final)
        self.assertTrue(final['data']['verified'])
        receipt = await self.call('harness_request_get', tool='harness_pipeline_start', request_id='pipeline')
        self.assertEqual(receipt['data']['state'], 'finished')
        self.assertEqual(receipt['data']['result']['data']['pipeline_id'], pid)
        self.assertEqual(required_scope('harness_pipeline_start'), 'command.run')
        self.assertEqual(required_scope('harness_pipeline_control'), 'command.run')

    async def test_real_image_delivery_wakes_following_command_and_reserves_project(self):
        image = await self.call('harness_image_request', request_id='image', prompt='原始提示词', path='asset.png')
        image_id = image['data']['image_id']
        pid = await self.start([
            {'kind': 'image', 'image_id': image_id},
            {'kind': 'exec', 'command': self.command("from PIL import Image; from pathlib import Path; assert Image.open('asset.png').size == (12, 12); Path('done.txt').write_text('done')")},
        ])
        self.assertEqual((await self.settled(pid))['status'], 'waiting_image')
        blocked = await self.call('harness_apply_patch', request_id='blocked', changes=[
            {'path': 'wrong.txt', 'expected_version': 'absent', 'content': 'no'}])
        self.assertEqual(blocked['data']['error']['code'], 'execution/pipeline-busy')
        content = io.BytesIO()
        Image.new('RGB', (12, 12), 'magenta').save(content, format='PNG')
        with patch('harness.execution.runtime.download_image', return_value=content.getvalue()) as download:
            result = await self.call('harness_image_deliver', request_id='delivery', image_id=image_id,
                file={'file_id': 'actual-file', 'download_url': 'https://fixture.invalid/image', 'mime_type': 'image/png'})
        self.assertFalse(result['is_error'], result)
        download.assert_called_once()
        self.assertEqual((await self.settled(pid))['status'], 'completed')
        self.assertEqual((self.project / 'done.txt').read_text(), 'done')

    async def test_failure_stops_later_commands_and_cannot_be_implicitly_retried(self):
        pid = await self.start([
            {'kind': 'exec', 'command': self.command('raise SystemExit(9)')},
            {'kind': 'exec', 'command': self.command("from pathlib import Path; Path('wrong.txt').touch()")},
        ])
        value = await self.settled(pid)
        self.assertEqual(value['status'], 'failed')
        self.assertFalse((self.project / 'wrong.txt').exists())
        denied = await self.call('harness_pipeline_control', request_id='resume', pipeline_id=pid,
            expected_revision=value['revision'], action='resume')
        self.assertEqual(denied['data']['error']['code'], 'execution/pipeline-terminal')

    async def test_pending_image_survives_service_restart_and_session_discovery(self):
        image = await self.call('harness_image_request', request_id='image', prompt='原始提示词', path='asset.png')
        pid = await self.start([{'kind': 'image', 'image_id': image['data']['image_id']}])
        await self.settled(pid)
        await self.runtime.close()
        self.runtime = ExecutionRuntime(self.config)
        context = await self.runtime.call('local', 'harness_context', {})
        self.assertEqual(context['data']['recent_sessions'][0]['session_id'], self.sid)
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                resumed = await self.call('harness_session_resume')
                value = resumed['data']['pipelines'][0]
                self.assertEqual(value['pipeline_id'], pid)
                self.assertEqual(value['next_action'], 'generate_and_deliver_image')
                self.assertEqual(any('\u4e00' <= char <= '\u9fff' for char in value['instruction']), language == 'zh-CN')
        other = await self.runtime.call('other', 'harness_pipeline_get', {'session_id': self.sid, 'pipeline_id': pid})
        self.assertTrue(other['is_error'])

    async def test_recovery_never_replays_an_accepted_step_with_unknown_result(self):
        with patch.object(self.runtime, '_pipeline_schedule'):
            pid = await self.start([{'kind': 'exec', 'command': self.command("from pathlib import Path; Path('wrong.txt').touch() ")}])
        value = self.runtime._pipeline_record(self.sid, pid)
        value['steps'][0]['status'] = 'accepted'
        value['status'] = 'interrupted'
        self.runtime._pipeline_save(value)
        resumed = await self.call('harness_pipeline_control', request_id='resume', pipeline_id=pid,
            expected_revision=value['revision'], action='resume')
        self.assertFalse(resumed['is_error'], resumed)
        result = await self.settled(pid)
        self.assertEqual(result['status'], 'interrupted')
        self.assertEqual(result['error_code'], 'execution/outcome-unknown')
        self.assertFalse((self.project / 'wrong.txt').exists())
        cancelled = await self.call('harness_pipeline_control', request_id='cancel', pipeline_id=pid,
            expected_revision=result['revision'], action='cancel')
        self.assertFalse(cancelled['is_error'], cancelled)
        self.assertEqual(cancelled['data']['status'], 'interrupted')
        self.assertEqual(cancelled['data']['error_code'], 'execution/outcome-unknown')

    async def test_invalid_step_and_corrupt_record_are_rejected_without_execution(self):
        result = await self.call('harness_pipeline_start', request_id='invalid', steps=[{'kind': 'exec', 'image_id': 'img_' + '0' * 32}])
        self.assertEqual(result['data']['error']['code'], 'execution/pipeline-invalid')
        raw = json.dumps({'pipeline_id': 'pl_' + '0' * 32, 'session_id': self.sid})
        self.runtime.store.db.execute("INSERT INTO events(session_id,kind,data,time) VALUES(?,'pipeline/state',?,1)", (self.sid, raw))
        self.runtime.store.db.commit()
        result = await self.call('harness_pipeline_get')
        self.assertEqual(result['data']['error']['code'], 'execution/store-invalid')
        self.assertEqual(self.runtime.store.db.execute("SELECT data FROM events WHERE kind='pipeline/state'").fetchone()[0], raw)

    async def test_cancel_running_job_prevents_following_step(self):
        pid = await self.start([
            {'kind': 'exec', 'command': self.command('import time; time.sleep(20)')},
            {'kind': 'exec', 'command': self.command("from pathlib import Path; Path('wrong.txt').touch()")},
        ])
        async def wait_accepted():
            while True:
                value = self.runtime._pipeline_record(self.sid, pid)
                if value['steps'][0]['job_id']:
                    return value
                await asyncio.sleep(0)
        value = await asyncio.wait_for(wait_accepted(), 5)
        result = await asyncio.wait_for(self.call('harness_pipeline_control', request_id='cancel', pipeline_id=pid,
            expected_revision=value['revision'], action='cancel'), 10)
        self.assertFalse(result['is_error'], result)
        self.assertEqual(result['data']['status'], 'cancelled')
        self.assertFalse((self.project / 'wrong.txt').exists())
        self.assertFalse(self.runtime.jobs.active_owners())

    async def test_restart_reconciles_completed_submission_without_repeating_it(self):
        command = self.command("from pathlib import Path; p=Path('once.txt'); p.write_text(p.read_text()+'x' if p.exists() else 'x')")
        with patch.object(self.runtime, '_pipeline_schedule'):
            pid = await self.start([{'kind': 'exec', 'command': command},
                {'kind': 'exec', 'command': self.command("from pathlib import Path; Path('done.txt').write_text('done')")}])
        value = self.runtime._pipeline_record(self.sid, pid)
        value['steps'][0]['status'] = 'accepted'
        value['status'] = 'running'
        self.runtime._pipeline_save(value)
        from harness.execution.pipeline import CURRENT_PIPELINE
        token = CURRENT_PIPELINE.set(pid)
        try:
            result = await self.call('harness_exec', request_id=f'{pid}:0', command=command, yield_ms=5000)
        finally:
            CURRENT_PIPELINE.reset(token)
        self.assertFalse(result['is_error'], result)
        self.assertEqual(result['data']['job']['status'], 'completed')
        await self.runtime.close()
        self.runtime = ExecutionRuntime(self.config)
        state = (await self.call('harness_pipeline_get', pipeline_id=pid))['data']['pipelines'][0]
        self.assertEqual(state['status'], 'interrupted')
        result = await self.call('harness_pipeline_control', request_id='resume', pipeline_id=pid,
            expected_revision=state['revision'], action='resume')
        self.assertFalse(result['is_error'], result)
        self.assertEqual((await self.settled(pid))['status'], 'completed')
        self.assertEqual((self.project / 'once.txt').read_text(), 'x')

    async def test_skill_content_change_stops_next_step_after_image_handoff(self):
        folder = self.project / '.agents' / 'skills' / 'fixture'
        folder.mkdir(parents=True)
        skill = folder / 'SKILL.md'
        body = '---\nname: fixture\ndescription: fixture skill\n---\nOriginal instructions.\n'
        skill.write_text(body, encoding='utf-8')
        image = await self.call('harness_image_request', request_id='image', prompt='fixture', path='asset.png')
        pid = await self.start([{'kind': 'image', 'image_id': image['data']['image_id']},
            {'kind': 'exec', 'command': self.command("from pathlib import Path; Path('wrong.txt').touch()")}], skills=['fixture'])
        value = await self.settled(pid)
        self.assertEqual(value['status'], 'waiting_image')
        skill.write_text(body + 'Changed instructions.\n', encoding='utf-8')
        result = await self.call('harness_pipeline_control', request_id='resume', pipeline_id=pid,
            expected_revision=value['revision'], action='resume')
        self.assertFalse(result['is_error'], result)
        value = await self.settled(pid)
        self.assertEqual(value['status'], 'failed')
        self.assertEqual(value['error_code'], 'execution/pipeline-skill-changed')
        self.assertFalse((self.project / 'wrong.txt').exists())

    async def test_cancel_during_accepted_submission_waits_and_stops_its_job(self):
        entered, release = asyncio.Event(), asyncio.Event()
        original = self.runtime._start_job

        async def delayed(*args, **kwargs):
            entered.set()
            await release.wait()
            return await original(*args, **kwargs)

        with patch.object(self.runtime, '_start_job', side_effect=delayed):
            pid = await self.start([{'kind': 'exec', 'command': self.command('import time; time.sleep(20)')}])
            await asyncio.wait_for(entered.wait(), 5)
            value = self.runtime._pipeline_record(self.sid, pid)
            control = asyncio.create_task(self.call('harness_pipeline_control', request_id='cancel', pipeline_id=pid,
                expected_revision=value['revision'], action='cancel'))
            async def cancellation_started():
                while not self.runtime._pipeline_tasks[pid].cancelling():
                    await asyncio.sleep(0)
            await asyncio.wait_for(cancellation_started(), 5)
            self.assertFalse(control.done())
            release.set()
            result = await asyncio.wait_for(control, 10)
        self.assertFalse(result['is_error'], result)
        self.assertEqual(result['data']['status'], 'cancelled')
        self.assertFalse(self.runtime.jobs.active_owners())

    async def test_request_lookup_rejects_corrupt_identity_without_rewriting_receipt(self):
        payload = {'session_id': 123, 'call_id': 'call_fixture', 'request_id': 'receipt',
                   'is_error': False, 'status': 'completed', 'data': {}}
        self.runtime.store.begin('local', self.sid, 'harness_exec', 'receipt', 'fingerprint')
        self.runtime.store.finish('local', self.sid, 'harness_exec', 'receipt', payload)
        original = self.runtime.store.db.execute("SELECT result FROM calls WHERE request_id='receipt'").fetchone()[0]
        result = await self.call('harness_request_get', tool='harness_exec', request_id='receipt')
        self.assertEqual(result['data']['error']['code'], 'execution/store-invalid')
        self.assertEqual(self.runtime.store.db.execute("SELECT result FROM calls WHERE request_id='receipt'").fetchone()[0], original)

    async def test_failed_process_cleanup_does_not_claim_pipeline_is_cancelled(self):
        pid = await self.start([{'kind': 'exec', 'command': self.command('import time; time.sleep(20)')}])
        async def accepted():
            while True:
                value = self.runtime._pipeline_record(self.sid, pid)
                if value['steps'][0]['job_id']:
                    return value
                await asyncio.sleep(0)
        value = await asyncio.wait_for(accepted(), 5)
        with patch.object(self.runtime.jobs, 'kill', new=AsyncMock(return_value=('cleanup-failed', {'status': 'cleanup-failed'}))):
            result = await self.call('harness_pipeline_control', request_id='cancel', pipeline_id=pid,
                expected_revision=value['revision'], action='cancel')
        self.assertFalse(result['is_error'], result)
        self.assertEqual(result['data']['status'], 'interrupted')
        self.assertEqual(result['data']['error_code'], 'execution/pipeline-cancel-unconfirmed')
        await self.runtime.jobs.kill(value['steps'][0]['job_id'], self.sid)
