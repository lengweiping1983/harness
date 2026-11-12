"""CLI、当前配置和统一账本的实际生产入口回归。"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from harness.app.superagent_service import SuperagentService
from harness.app.superagent_config import read_config
from harness.app.superagent_local import submit_local, read_status
from tests.superagent_product_fixture import product_config, ProductTransport


class ProductEntryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config, self.spec = product_config(self.root)
        self.environment = patch.dict(os.environ, {'HARNESS_API_KEY': 'fixture-only'})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    async def _release_gate(self, gate):
        gate.set()

    async def open(self, roles, work):
        service = await SuperagentService.open(self.config, coordinator_transport=roles, worker_transport=work)
        self.addAsyncCleanup(service.aclose)
        return service

    async def test_production_entry_assesses_plans_executes_and_restarts_without_legacy_ledgers(self):
        submitted = submit_local(self.config, 'Fix the existing typo.', request_id='original')
        self.assertTrue(submitted['queued'])
        roles, work = ProductTransport(), ProductTransport()
        service = await self.open(roles, work)
        result = await service.serve(asyncio.Event(), once=True)
        self.assertEqual(result, 0, service.coordinator_status)
        self.assertEqual(len(roles.requests), 2)
        self.assertEqual(len(work.requests), 1)
        self.assertTrue(all(not request.get('tools') for request in roles.requests))
        status = read_status(self.config)
        self.assertEqual(status['missions']['recent'][0]['state'], 'completed')
        self.assertEqual(status['outbox'], {'sent': 1})
        await service.aclose()
        self.assertEqual(read_status(self.config)['runtime_state'], 'stopped')
        repeated = submit_local(self.config, 'Fix the existing typo.', request_id='original')
        self.assertEqual(repeated['trigger_id'], submitted['trigger_id'])
        restarted = await self.open(roles, work)
        self.assertEqual(await restarted.serve(asyncio.Event(), once=True), 0)
        self.assertEqual((len(roles.requests), len(work.requests)), (2, 1))
        self.assertEqual(len(list((self.root / 'state' / 'replies').glob('*.json'))), 1)
        self.assertFalse(any((self.root / 'state' / name).exists() for name in
            ('inbox.sqlite3', 'service.sqlite3', 'missions.sqlite3', 'control.sqlite3', 'guidance', 'incoming')))

    async def test_legacy_configuration_is_refused_before_credentials_or_state_writes(self):
        self.spec['version'] = 1
        self.spec['autonomy'] = {'enabled': True}
        self.config.write_text(json.dumps(self.spec))
        with self.assertRaises(Exception) as caught:
            await self.open(ProductTransport(), ProductTransport())
        self.assertEqual(caught.exception.code, 'superagent/invalid')
        self.assertFalse((self.root / 'state').exists())

    async def test_current_example_and_configuration_bounds_are_validated_without_writes(self):
        example = Path(__file__).resolve().parents[1] / 'examples' / 'superagent.json'
        spec = read_config(example)
        self.assertEqual(spec['version'], 2)
        self.assertEqual(spec['profiles']['builder']['repo'], str(example.parent.parent))
        self.assertEqual(spec['policy']['sources']['local']['scope']['thread_ids'], ['$current'])
        for changes in ({'autonomy':{}}, {'once_timeout':.5}, {'interval':0}, {'max_workers':True}):
            with self.subTest(changes=changes):
                self.config.write_text(json.dumps({**self.spec, **changes}), encoding='utf-8')
                with self.assertRaises(Exception) as caught:
                    read_config(self.config)
                self.assertEqual(caught.exception.code, 'superagent/invalid')
        self.assertFalse((self.root / 'state').exists())

    async def test_readonly_status_without_state_does_not_construct_harness_or_create_database(self):
        with patch('harness.app.superagent_bootstrap.Harness', side_effect=AssertionError('不得构造宿主')):
            with self.assertRaises(Exception) as caught:
                read_status(self.config)
        self.assertEqual(caught.exception.code, 'superagent/status-unavailable')
        self.assertFalse((self.root / 'state').exists())

    async def test_runtime_lease_rejects_second_owner_before_constructing_model_hosts(self):
        first = await self.open(ProductTransport(), ProductTransport())
        with patch('harness.app.superagent_bootstrap.Harness', side_effect=AssertionError('竞争宿主不得构造')):
            with self.assertRaises(Exception) as caught:
                await self.open(ProductTransport(), ProductTransport())
        self.assertEqual(caught.exception.code, 'superagent/runtime-busy')
        self.assertFalse(first.closed)

    async def test_concurrent_local_submissions_keep_one_trigger_and_conflicting_body_is_preserved(self):
        from concurrent.futures import ThreadPoolExecutor
        submit_local(self.config, 'Initial request.', request_id='initialize')
        def submit():
            return submit_local(self.config, 'Fix the existing typo.', request_id='concurrent')
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = await asyncio.gather(*(asyncio.wrap_future(pool.submit(submit)) for _ in range(4)))
        self.assertEqual(len({row['trigger_id'] for row in results}), 1)
        with self.assertRaises(Exception):
            submit_local(self.config, 'Different body', request_id='concurrent')
        from harness.app.superagent_state import open_state
        with open_state(read_config(self.config), read_only=True).transaction() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM triggers').fetchone()[0], 2)
            self.assertEqual(db.execute('SELECT count(*) FROM coordinator_local_events').fetchone()[0], 2)

    async def test_pause_resume_and_instruction_queue_use_original_native_run(self):
        from harness.app.superagent_local import control_local, read_control_receipt, steer_local, read_guidance_receipt
        submit_local(self.config, 'Fix the existing typo.', request_id='original')
        work = ProductTransport()
        work.entered, work.gate = asyncio.Event(), asyncio.Event()
        self.addAsyncCleanup(self._release_gate, work.gate)
        service = await self.open(ProductTransport(), work)
        self.addAsyncCleanup(self._release_gate, work.gate)
        await service.tick()
        await asyncio.wait_for(work.entered.wait(), 10)
        await service.tick()
        mission = read_status(self.config)['missions']['recent'][0]
        job = mission['jobs'][0]
        with patch('harness.app.superagent_bootstrap.Harness', side_effect=AssertionError('本机控制不得构造模型')):
            paused = control_local(self.config, 'pause', mission['mission_id'], request_id='pause')
            self.assertEqual(paused['state'], 'applied')
            self.assertEqual(read_control_receipt(self.config, 'pause'), paused)
            self.assertEqual(control_local(self.config, 'resume', mission['mission_id'], request_id='resume')['state'], 'applied')
            queued = steer_local(self.config, 'Preserve the public name.', job_id=job['job_id'], request_id='steer')
            self.assertTrue(queued['queued'])
        try:
            await service.tick()
            receipt = read_guidance_receipt(self.config, 'steer')
            self.assertEqual(receipt['state'], 'accepted', receipt)
            self.assertEqual(receipt['instruction_state'], 'accepted')
            original = service.coordinator.execution.instruction_receipt('steer')
            self.assertEqual(original['run_id'], job['assignment']['run_id'])
            self.assertEqual(original['turn_id'], job['run']['turn'])
            repeated = steer_local(self.config, 'Preserve the public name.', job_id=job['job_id'], request_id='steer')
            self.assertEqual(repeated['state'], 'accepted')
            with self.assertRaises(Exception):
                steer_local(self.config, 'Another instruction.', job_id=job['job_id'], request_id='steer')
            self.assertEqual(len(work.requests), 1)
        finally:
            work.gate.set()
        await service.serve(asyncio.Event(), once=True)
        await service.aclose()
        self.assertEqual(read_guidance_receipt(self.config, 'steer')['request_id'], 'steer')

    async def test_planning_await_releases_database_and_rechecks_revocation_before_workers(self):
        submit_local(self.config, 'Fix the existing typo.', request_id='original')
        entered, release = asyncio.Event(), asyncio.Event()
        class Roles(ProductTransport):
            async def post_stream(self, url, **kwargs):
                request = json.loads(kwargs['body'])
                text = next(m['content'] for m in reversed(request['messages']) if m.get('role') == 'user')
                if '"default_profile"' in str(text):
                    entered.set()
                    await release.wait()
                async for chunk in super().post_stream(url, **kwargs):
                    yield chunk
        work = ProductTransport()
        service = await self.open(Roles(), work)
        task = asyncio.create_task(service.tick())
        try:
            await asyncio.wait_for(entered.wait(), 10)
            service.coordinator.sources.update_instance('local', expected_revision=1, enabled=False)
        finally:
            release.set()
        await task
        self.assertFalse(work.requests)
        with service.coordinator._store.transaction() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM missions').fetchone()[0], 0)

    async def test_lost_coordinator_submit_ack_finds_original_run_and_never_replays(self):
        submit_local(self.config, 'Fix the existing typo.', request_id='original')
        roles, work = ProductTransport(), ProductTransport()
        service = await self.open(roles, work)
        runtime = service._owned_harnesses[1].runtime
        submit = runtime.submit
        calls = []
        def lose(*args, **kwargs):
            calls.append(kwargs['key'])
            submit(*args, **kwargs)
            raise RuntimeError('accepted; acknowledgement lost')
        with patch.object(runtime, 'submit', side_effect=lose):
            self.assertEqual(await service.serve(asyncio.Event(), once=True), 0)
            await service.tick()
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(set(calls)), 2)
        self.assertEqual((len(roles.requests), len(work.requests)), (2, 1))

    async def test_absent_unknown_coordinator_submission_is_not_resubmitted(self):
        submit_local(self.config, 'Fix the existing typo.', request_id='original')
        service = await self.open(ProductTransport(), ProductTransport())
        roles = service._owned_harnesses[1]
        with patch.object(roles.runtime, 'submit', side_effect=RuntimeError('unknown outcome')) as submit:
            self.assertEqual(await service.serve(asyncio.Event(), once=True), 2)
            with service.coordinator._store.transaction() as db:
                row = db.execute('SELECT * FROM coordinator_model_calls').fetchone()
            with self.assertRaises(Exception):
                await service._coordinator_assessor._call('assess', row['call_key'], {})
            self.assertEqual(submit.call_count, 1)
        self.assertFalse(roles.runtime.records.active)

    async def test_real_followup_receipt_binds_original_run_and_is_applied_without_extra_mission(self):
        submit_local(self.config, 'Fix the existing typo.', request_id='original')
        work = ProductTransport()
        work.entered, work.gate = asyncio.Event(), asyncio.Event()
        roles = ProductTransport()
        service = await self.open(roles, work)
        self.addAsyncCleanup(self._release_gate, work.gate)
        await service.tick()
        await asyncio.wait_for(work.entered.wait(), 10)
        followup = submit_local(self.config, 'Preserve the public name.', request_id='followup')
        status = await service.tick()
        deliveries = status['routes'][0]['receipt']['deliveries']
        self.assertEqual(len(deliveries), 1)
        self.assertIn(deliveries[0]['state'], {'accepted', 'applied'})
        with service.coordinator._store.transaction() as db:
            before = db.execute('SELECT * FROM assignments').fetchone()
            self.assertEqual(db.execute('SELECT count(*) FROM missions').fetchone()[0], 1)
        await service.tick()
        self.assertEqual(len(work.requests), 1)
        work.gate.set()
        self.assertEqual(await service.serve(asyncio.Event(), once=True), 0, service.coordinator_status)
        with service.coordinator._store.transaction() as db:
            target = db.execute('SELECT d.* FROM input_deliveries d JOIN ticket_inputs i ON i.input_id=d.input_id WHERE i.trigger_id=?', (followup['trigger_id'],)).fetchone()
            self.assertEqual(target['state'], 'applied')
            self.assertEqual(target['assignment_id'], before['assignment_id'])
            self.assertEqual(db.execute('SELECT count(*) FROM missions').fetchone()[0], 1)
        self.assertEqual(len(roles.requests), 2)

    def artifact_transports(self):
        class Roles(ProductTransport):
            async def post_stream(self, url, **kwargs):
                request = json.loads(kwargs['body'])
                text = next(m['content'] for m in reversed(request['messages']) if m.get('role') == 'user')
                if '"default_profile"' not in str(text):
                    async for chunk in super().post_stream(url, **kwargs):
                        yield chunk
                    return
                self.requests.append(request)
                answer = {'plan': {'nodes':[{'node_id':'work', 'role':'work', 'profile_id':'work', 'instruction':'Write result.txt with verified content.', 'dependencies':[]}]},
                    'acceptance': {'kind':'artifact_review', 'schema_version':1, 'criteria':[{'id':'file', 'kind':'inspection'}], 'max_repair_rounds':1}}
                yield ('data: '+json.dumps({'choices':[{'delta':{'content':json.dumps(answer)}, 'finish_reason':'stop'}]})+'\n\ndata: [DONE]\n\n').encode()
        class Work(ProductTransport):
            async def post_stream(self, url, **kwargs):
                request = json.loads(kwargs['body'])
                self.requests.append(request)
                messages = [m.get('content','') for m in request['messages'] if isinstance(m.get('content'), str)]
                review = next((m for m in messages if 'Host-owned independent Reviewer:' in m), None)
                integrate = any('Host-owned Integrator contract:' in m for m in messages)
                has_results = any(m.get('role') == 'tool' for m in request['messages'])
                if not has_results:
                    name = 'read' if review or integrate else 'write'
                    args = {'path':'result.txt'} if name == 'read' else {'path':'result.txt', 'content':'verified content\n'}
                    delta = {'tool_calls':[{'index':0, 'id':'file-evidence', 'type':'function', 'function':{'name':name, 'arguments':json.dumps(args)}}]}
                    finish = 'tool_calls'
                else:
                    if review:
                        contract, _ = json.JSONDecoder().raw_decode(review.split('Host-owned independent Reviewer:',1)[1].split('\n',1)[1].lstrip())
                        evidence = contract['available_evidence'][0]['evidence_id']
                        answer = {'schema_version':1, 'candidate_artifact_id':contract['candidate_artifact_id'],
                            'acceptance_hash':contract['acceptance_hash'], 'verdict':'passed',
                            'criteria':[{'id':item['id'], 'status':'passed', 'reason':'Inspected frozen file.', 'evidence_ids':[evidence]} for item in contract['criteria']]}
                    else:
                        answer = {'summary':'Verified file delivered.'}
                    delta, finish = {'content':json.dumps(answer)}, 'stop'
                yield ('data: '+json.dumps({'choices':[{'delta':delta, 'finish_reason':finish}]})+'\n\ndata: [DONE]\n\n').encode()
        return Roles(), Work()

    async def test_readonly_policy_refuses_a_visible_write_in_the_native_worker(self):
        self.spec['permission_policy'] = 'read_only'
        self.spec['profiles']['work']['tools'] = ['read', 'write']
        self.config.write_text(json.dumps(self.spec), encoding='utf-8')
        roles, work = self.artifact_transports()
        submit_local(self.config, 'Create result.txt with verified content.', request_id='readonly')
        service = await self.open(roles, work)
        await service.serve(asyncio.Event(), once=True)
        tool_results = [message for request in work.requests for message in request['messages'] if message.get('role') == 'tool']
        self.assertTrue(tool_results)
        self.assertIn('read-only', str(tool_results).lower())
        self.assertFalse(any(path.name == 'result.txt' for path in (self.root/'state').rglob('result.txt')))
        self.assertFalse((self.root/'repo'/'result.txt').exists())

    async def test_production_file_task_uses_integrator_independent_review_and_frozen_result(self):
        self.spec['profiles']['work']['tools'] = ['read', 'write']
        self.config.write_text(json.dumps(self.spec))
        roles, work = self.artifact_transports()
        submit_local(self.config, 'Create result.txt with verified content.', request_id='artifact')
        service = await self.open(roles, work)
        self.assertEqual(await service.serve(asyncio.Event(), once=True), 0, service.coordinator_status)
        with service.coordinator._store.transaction() as db:
            mission = db.execute('SELECT * FROM missions').fetchone()
            review = db.execute('SELECT * FROM reviews').fetchone()
            assignments = db.execute('SELECT * FROM assignments').fetchall()
            outbox = db.execute('SELECT * FROM outbox_messages').fetchall()
        self.assertEqual((mission['state'], review['state']), ('completed', 'passed'))
        self.assertEqual(len(assignments), 3)
        self.assertEqual(len({row['session_id'] for row in assignments}), 3)
        self.assertEqual(len({row['workspace_id'] for row in assignments}), 3)
        self.assertEqual(len(outbox), 1)
        self.assertEqual(outbox[0]['state'], 'sent')
        from harness.app.superagent_local import query_local, mission_local
        detail = mission_local(self.config, mission['mission_id'])
        self.assertEqual(detail['counts']['assignments'], 3)
        self.assertEqual(detail['counts']['reviews'], 1)
        reviews = query_local(self.config, 'reviews', mission_id=mission['mission_id'])
        artifacts = query_local(self.config, 'artifacts', mission_id=mission['mission_id'])
        self.assertEqual(reviews['items'][0]['state'], 'passed')
        self.assertEqual(reviews['items'][0]['review_id'], review['review_id'])
        self.assertIn(review['candidate_artifact_id'], {item['artifact_id'] for item in artifacts['items']})
        self.assertNotIn('verdict_json', reviews['items'][0])
        self.assertIn(review['candidate_artifact_id'], next((self.root/'state'/'replies').glob('*.json')).read_text())
        self.assertFalse((self.root/'repo'/'result.txt').exists())
        before = len(work.requests)
        await service.tick()
        self.assertEqual(len(work.requests), before)

    async def test_close_during_planning_waits_original_model_and_starts_no_worker(self):
        submit_local(self.config, 'Fix the existing typo.', request_id='closing')
        entered, release = asyncio.Event(), asyncio.Event()
        class Roles(ProductTransport):
            async def post_stream(self, url, **kwargs):
                request = json.loads(kwargs['body'])
                if any('"default_profile"' in str(m.get('content')) for m in request['messages']):
                    entered.set()
                    await release.wait()
                async for chunk in super().post_stream(url, **kwargs):
                    yield chunk
        work = ProductTransport()
        service = await self.open(Roles(), work)
        self.addAsyncCleanup(self._release_gate, release)
        tick = asyncio.create_task(service.tick())
        await asyncio.wait_for(entered.wait(), 10)
        close = asyncio.create_task(service.aclose())
        await asyncio.sleep(0)
        self.assertFalse(close.done())
        release.set()
        await tick
        await close
        self.assertFalse(work.requests)
        self.assertEqual(read_status(self.config)['runtime_state'], 'stopped')
        with service._coordinator_host._store.transaction() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM workers').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM assignments').fetchone()[0], 0)

    async def test_native_model_prompts_consume_language_roundtrip_and_freeze_original_body(self):
        roles = ProductTransport()
        service = await self.open(roles, ProductTransport())
        prompts = []
        models = service._coordinator_assessor
        frame = {'trigger_id':'language', 'default_profile':'work', 'request':'原文 remains unchanged', 'profiles':{}}
        for index, language in enumerate(('en', 'zh-CN', 'en')):
            models.harness.config.language = language
            await models._call('plan', 'language-' + str(index), frame)
            request = roles.requests[-1]
            prompts.append(next(m['content'] for m in reversed(request['messages']) if m.get('role') == 'user'))
        self.assertEqual(prompts[0], prompts[2])
        self.assertNotEqual(prompts[0], prompts[1])
        self.assertTrue(all('原文 remains unchanged' in text for text in prompts))
        models.harness.config.language = 'zh-CN'
        await models._call('plan', 'language-0', frame)
        self.assertEqual(len(roles.requests), 3)

    async def test_changed_configuration_rejects_before_constructing_or_mutating_native_hosts(self):
        service = await self.open(ProductTransport(), ProductTransport())
        await service.aclose()
        self.spec['model'] = 'different-model'
        self.config.write_text(json.dumps(self.spec))
        with patch('harness.app.superagent_bootstrap.Harness', side_effect=AssertionError('身份冲突不得打开原生宿主')):
            with self.assertRaises(Exception) as caught:
                await self.open(ProductTransport(), ProductTransport())
        self.assertEqual(caught.exception.code, 'superagent/instance-conflict')

    async def test_coordinator_model_refuses_a_native_receipt_from_another_session(self):
        from types import SimpleNamespace
        service = await self.open(ProductTransport(), ProductTransport())
        models = service._coordinator_assessor
        def foreign(session_id, key):
            with service.coordinator._store.transaction() as db:
                row = db.execute('SELECT * FROM coordinator_model_calls WHERE call_key=?', (key,)).fetchone()
            return SimpleNamespace(session_id='foreign', id='foreign-run', text=row['prompt'], request_id=key)
        with patch.object(models.harness.runtime, 'find_submission', side_effect=foreign), patch.object(models.harness.runtime, 'submit', side_effect=AssertionError('外来回执不能触发执行')):
            with self.assertRaises(Exception) as caught:
                await models._call('assess', 'original-request', {})
        self.assertEqual(caught.exception.code, 'superagent/conflict')
        with service.coordinator._store.transaction() as db:
            row = db.execute('SELECT state,run_id FROM coordinator_model_calls').fetchone()
        self.assertEqual(tuple(row), ('prepared', None))

    async def test_instance_lease_is_held_until_owned_native_harnesses_finish_closing(self):
        service = await self.open(ProductTransport(), ProductTransport())
        roles = service._owned_harnesses[1]
        close = roles.close
        entered, release = asyncio.Event(), asyncio.Event()
        async def delayed():
            entered.set()
            await release.wait()
            await close()
        self.addAsyncCleanup(self._release_gate, release)
        with patch.object(roles, 'close', side_effect=delayed):
            closing = asyncio.create_task(service.aclose())
            await asyncio.wait_for(entered.wait(), 10)
            try:
                with patch('harness.app.superagent_bootstrap.Harness', side_effect=AssertionError('旧宿主尚未收尾')):
                    with self.assertRaises(Exception) as caught:
                        await self.open(ProductTransport(), ProductTransport())
                self.assertEqual(caught.exception.code, 'superagent/runtime-busy')
            finally:
                release.set()
            await closing
        self.assertEqual(read_status(self.config)['runtime_state'], 'stopped')
