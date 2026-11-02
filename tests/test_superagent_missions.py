"""隔离真实 Git、运行和工具的目标图、交付、复核及有限修正回归。"""
import asyncio
import json
import os
import shlex
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from harness.app.git_process import run_git
from harness.app.superagent_mission_store import digest, packed, validate_plan
from harness.app.superagent_service import SuperagentService
from harness.core.errors import HarnessError
from harness.core.language import current_language
from harness.i18n import tr
from tests.agent_configuration_fixture import RecordingTransport


def frame_of(request):
    """只读取模型实际收到的用户消息，不窥探运行或事件序号。"""
    for message in reversed(request['messages']):
        if message['role'] != 'user':
            continue
        content = message.get('content', '')
        if isinstance(content, list):
            content = ''.join(part.get('text', '') for part in content)
        if '\n\n{' in content:
            return json.loads(content[content.rindex('\n\n{') + 2:])
    raise AssertionError('缺少结构化目标输入。')


def tool_messages(request):
    index = max(index for index, message in enumerate(request['messages']) if message['role'] == 'user')
    return [message for message in request['messages'][index + 1:] if message['role'] == 'tool']


def stream_delta(delta, reason):
    return ('data: ' + json.dumps({'choices': [{'delta': delta, 'finish_reason': reason}]}) + '\n\n').encode('utf-8')


class PlannerTransport(RecordingTransport):
    def __init__(self):
        super().__init__()
        self.plan = {'steps': [
            {'id': 'first', 'profile_id': 'coding', 'instruction': 'Create first.txt with alpha.', 'depends_on': []},
            {'id': 'second', 'profile_id': 'coding', 'instruction': 'Create second.txt with beta.', 'depends_on': []},
            {'id': 'combine', 'profile_id': 'coding', 'instruction': 'Read both predecessors and create combined.txt.',
             'depends_on': ['first', 'second']}],
            'acceptance': [{'id': 'contents', 'criterion': 'combined.txt contains alpha and beta on separate lines.', 'kind': 'command'}]}
        self.responses = []

    async def post_stream(self, url, *, headers, body, timeout):
        request = json.loads(body)
        self.requests.append(request)
        frame_of(request)
        yield stream_delta({'content': json.dumps(self.responses.pop(0) if self.responses else self.plan)}, 'stop')
        yield b'data: [DONE]\n\n'


class MissionWorkerTransport(RecordingTransport):
    def __init__(self):
        super().__init__()
        self.frames = []
        self.repair = False
        self.forged = False
        self.review_cwd = None
        self.review_modified = False
        self.background_marker = None
        self.work_gate = None
        self.gate_request = None
        self.work_entered = asyncio.Event()

    async def post_stream(self, url, *, headers, body, timeout):
        request = json.loads(body)
        self.requests.append(request)
        frame = frame_of(request)
        self.frames.append(frame)
        role = frame['role']
        messages = tool_messages(request)
        if (role == 'work' and self.work_gate is not None and 'work:first:' in frame['job_id']
                and (self.gate_request is None or self.gate_request == frame['request'])):
            self.work_entered.set()
            await self.work_gate.wait()
        call_id = 'evidence-' + role + '-' + str(frame['attempt'])
        if not messages:
            if role == 'work':
                if 'work:first:' in frame['job_id']:
                    if self.background_marker is None:
                        tool, args = 'write', {'path': 'first.txt', 'content': 'alpha\n'}
                    else:
                        marker = shlex.quote(str(self.background_marker))
                        tool, args = 'bash', {'command': f"while [ ! -f {marker} ]; do sleep 0.01; done; printf 'alpha\\n' > first.txt",
                                              'run_in_background': True}
                elif 'work:second:' in frame['job_id']:
                    tool, args = 'write', {'path': 'second.txt', 'content': 'beta\n'}
                else:
                    tool, args = 'bash', {'command': 'test "$(cat first.txt)" = alpha && test "$(cat second.txt)" = beta && cat first.txt second.txt > combined.txt'}
            elif role == 'integrate':
                tool, args = ('bash', {'command': "printf 'alpha\\nbeta\\n' > combined.txt"}) if frame['attempt'] > 1 else ('read', {'path': 'combined.txt'})
            else:
                command = 'test "$(cat combined.txt)" = "$(printf \'alpha\\nbeta\')"'
                if self.repair and frame['attempt'] == 1:
                    command = 'test -f repaired.txt'
                if self.review_modified:
                    command = "printf 'changed\\n' > combined.txt"
                args = {'command': command}
                if self.review_cwd is not None:
                    args['cwd'] = self.review_cwd
                tool = 'bash'
            yield stream_delta({'tool_calls': [{'index': 0, 'id': call_id, 'type': 'function',
                'function': {'name': tool, 'arguments': json.dumps(args)}}]}, 'tool_calls')
        elif role == 'review':
            passed = not (self.repair and frame['attempt'] == 1)
            value = {'checks': [{'id': 'contents', 'passed': passed,
                     'evidence': ['invented-call' if self.forged else call_id],
                     'reason': 'The foreground command checked both expected lines.' if passed else 'Repair the integration candidate.'}],
                     'summary': 'Candidate checked independently.' if passed else 'Explicit repair required.',
                     'reply': 'The combined candidate passed the recorded independent check.' if passed else None}
            yield stream_delta({'content': json.dumps(value)}, 'stop')
        else:
            yield stream_delta({'content': 'Candidate work is ready for independent review.'}, 'stop')
        yield b'data: [DONE]\n\n'


class SuperagentMissionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        environment = patch.dict(os.environ, {'HARNESS_API_KEY': 'fixture-only', 'OPENROUTER_API_KEY': 'fixture-only'})
        environment.start()
        self.addCleanup(environment.stop)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        for args in [('init', '--initial-branch=main'), ('config', 'user.name', 'Fixture'),
                     ('config', 'user.email', 'fixture@example.invalid')]:
            self.git(*args)
        (self.repo / 'main.txt').write_text('baseline\n', encoding='utf-8')
        self.git('add', '.')
        self.git('commit', '-m', 'baseline')
        self.path = self.root / 'config.json'
        self.spec = {'version': 1, 'instance_id': 'team', 'state_dir': 'state', 'language': 'en',
            'router_model': 'planner-model', 'worker_model': 'worker-model', 'base_url': 'https://fixture.invalid/v1',
            'permission_policy': 'full_access', 'interval': 0.01, 'once_timeout': 30,
            'sources': {'local': {'mode': 'requests', 'actors': ['operator'], 'threads': ['project']}},
            'profiles': {'coding': {'repo': 'repo', 'description': 'Implement the requested objective.',
                         'sources': ['local'], 'tools': ['read', 'write', 'bash']}},
            'autonomy': {'max_steps': 8, 'max_attempts': 2, 'max_missions': 8,
                         'integrator_profile': 'coding', 'reviewer_profile': 'coding'}}
        self.planner, self.worker = PlannerTransport(), MissionWorkerTransport()
        self.service = None

    def git(self, *args):
        result = run_git(self.repo, *args)
        if result.returncode:
            raise RuntimeError(result.stderr.decode('utf-8'))
        return result.stdout.decode('utf-8').strip()

    async def open(self):
        self.path.write_text(json.dumps(self.spec), encoding='utf-8')
        service = await SuperagentService.open(self.path, worker_transport=self.worker, router_transport=self.planner)
        self.addAsyncCleanup(service.aclose)
        self.service = service
        return service

    def receive(self, identity='one', text='Create the combined candidate and verify both lines.'):
        return self.service.inbox.receive(source_id='local', event_id=identity, actor_id='operator',
            text=text, thread_ids=['project'], human=True, explicit=True)

    async def settle(self):
        for host in self.service.hosts:
            tasks = [record.task for record in tuple(host.runtime.records.active.values()) if record.task is not None]
            if tasks:
                await asyncio.wait_for(asyncio.gather(*tasks), 10)

    async def advance_until(self, predicate, *, settle=True):
        for _ in range(250):
            status = await self.service.tick()
            if predicate(status):
                return status
            if settle:
                await self.settle()
            else:
                await asyncio.sleep(0)
        self.fail('目标在有界推进内没有到达预期状态。')

    async def test_dependency_files_are_imported_and_candidate_is_independently_verified(self):
        service = await self.open()
        receipt = self.receive()
        self.assertEqual(await service.serve(asyncio.Event(), once=True), 0)
        mission = service.router.store.mission(service.router.store.mission_id(receipt['id']))
        self.assertEqual(mission['state'], 'completed')
        jobs = service.router.store.jobs(mission['id'])
        self.assertEqual(len(jobs), 6)
        work = {job['node']: job for job in jobs if job['role'] == 'work'}
        combined = Path(service.worker_host.get_session(work['combine']['data']['session_id']).header.cwd)
        self.assertEqual((combined / 'combined.txt').read_text(encoding='utf-8'), 'alpha\nbeta\n')
        review = next(job for job in jobs if job['role'] == 'review')
        integration = next(job for job in jobs if job['role'] == 'integrate')
        self.assertNotEqual(review['data']['session_id'], integration['data']['session_id'])
        self.assertNotEqual(review['data']['workspace_id'], integration['data']['workspace_id'])
        self.assertEqual(review['data']['delivery']['tree'], integration['data']['delivery']['tree'])
        evidence = review['data']['verdict']['checks'][0]['evidence'][0]
        self.assertEqual(evidence['run_id'], review['data']['run_id'])
        self.assertEqual(evidence['tool'], 'bash')
        self.assertGreater(evidence['result_seq'], 0)
        self.assertEqual(sorted(path.name for path in self.repo.iterdir()), ['.git', 'main.txt'])
        self.assertEqual(len(list((service.root / 'drafts').glob('*.json'))), 1)
        followup = service.inbox.receive(source_id='local', event_id='followup', actor_id='operator', text='unassigned context',
            thread_ids=['project'], human=True, explicit=False)
        self.assertFalse(any(route['kind'] == 'worker' for route in followup['routes']))

    async def test_repair_is_bounded_and_uses_same_integrator_and_fresh_reviewer(self):
        self.worker.repair = True
        service = await self.open()
        self.receive()
        self.assertEqual(await service.serve(asyncio.Event(), once=True), 0)
        mission = service.router.status()['missions']['recent'][0]
        self.assertEqual(mission['round'], 2)
        jobs = service.router.store.jobs(mission['id'])
        integrations = [job for job in jobs if job['role'] == 'integrate']
        reviews = [job for job in jobs if job['role'] == 'review']
        self.assertEqual(integrations[0]['data']['session_id'], integrations[1]['data']['session_id'])
        self.assertNotEqual(integrations[0]['data']['run_id'], integrations[1]['data']['run_id'])
        self.assertNotEqual(reviews[0]['data']['session_id'], reviews[1]['data']['session_id'])
        repaired = next(frame for frame in self.worker.frames if frame['role'] == 'integrate' and frame['attempt'] == 2)
        self.assertFalse(repaired['feedback']['checks'][0]['passed'])

    async def test_forged_evidence_cannot_complete_or_publish(self):
        self.worker.forged = True
        service = await self.open()
        self.receive()
        self.assertEqual(await service.serve(asyncio.Event(), once=True), 2)
        mission = service.router.status()['missions']['recent'][0]
        self.assertEqual((mission['state'], mission['reason']), ('blocked', 'review/evidence-invalid'))
        self.assertEqual(service.results.drafts(), [])

    async def test_command_in_another_repository_cannot_prove_candidate(self):
        (self.repo / 'combined.txt').write_text('alpha\nbeta\n', encoding='utf-8')
        self.worker.review_cwd = str(self.repo)
        service = await self.open()
        self.receive()
        self.assertEqual(await service.serve(asyncio.Event(), once=True), 2)
        self.assertEqual(service.router.status()['missions']['recent'][0]['reason'], 'review/evidence-invalid')

    async def test_reviewer_changes_do_not_redefine_the_candidate(self):
        self.worker.review_modified = True
        service = await self.open()
        self.receive()
        self.assertEqual(await service.serve(asyncio.Event(), once=True), 2)
        self.assertEqual(service.router.status()['missions']['recent'][0]['reason'], 'review/input-changed')

    async def test_cycle_is_rejected_before_worker_creation(self):
        self.planner.plan['steps'][0]['depends_on'] = ['combine']
        service = await self.open()
        self.receive()
        self.assertEqual(await service.serve(asyncio.Event(), once=True), 2)
        self.assertEqual(service.factory.status()['states'], {})
        self.assertEqual(self.worker.requests, [])

    async def test_invalid_plan_can_be_corrected_without_starting_invalid_work(self):
        self.planner.responses = [{'steps': [], 'acceptance': []}]
        service = await self.open()
        self.receive()
        self.assertEqual(await service.serve(asyncio.Event(), once=True), 0)
        self.assertEqual(len(self.planner.requests), 2)
        self.assertEqual(frame_of(self.planner.requests[1])['previous_plan_error'], 'plan/invalid')
        self.assertEqual(service.factory.status()['states'], {'ready': 5})

    async def test_failed_review_exhausts_the_explicit_budget(self):
        self.spec['autonomy']['max_attempts'] = 1
        self.worker.repair = True
        service = await self.open()
        self.receive()
        self.assertEqual(await service.serve(asyncio.Event(), once=True), 2)
        mission = service.router.status()['missions']['recent'][0]
        self.assertEqual((mission['state'], mission['round'], mission['reason']), ('blocked', 1, 'budget/attempts-exhausted'))
        self.assertEqual(service.results.drafts(), [])

    async def test_restart_does_not_reexecute_completed_plan_or_workers(self):
        service = await self.open()
        self.receive()
        self.assertEqual(await service.serve(asyncio.Event(), once=True), 0)
        original = service.results.drafts()
        await service.aclose()
        reopened = await self.open()
        with patch.object(reopened.router_host.runtime, 'submit', side_effect=AssertionError('规划被重放')), \
                patch.object(reopened.worker_host.runtime, 'submit', side_effect=AssertionError('工作被重放')):
            self.assertEqual(await reopened.serve(asyncio.Event(), once=True), 0)
        self.assertEqual(reopened.results.drafts(), original)

    async def test_slow_predecessor_does_not_block_other_work_or_lose_late_publication(self):
        service = await self.open()
        self.worker.work_gate = asyncio.Event()
        self.worker.gate_request = 'Slow objective'
        slow = self.receive('slow', text='Slow objective')
        fast = self.receive('fast', text='Fast objective')
        try:
            status = await self.advance_until(lambda value: value['missions']['states'].get('completed') == 1, settle=False)
            self.assertTrue(self.worker.work_entered.is_set())
            drafts = service.results.drafts()
            self.assertEqual([draft['event_id'] for draft in drafts], [fast['event_id']])
            self.assertEqual(drafts[0]['seq'], 1)
            slow_id = service.router.store.mission_id(slow['id'])
            jobs = service.router.store.jobs(slow_id)
            self.assertEqual(next(job for job in jobs if job['node'] == 'second')['state'], 'done')
            self.assertEqual(next(job for job in jobs if job['node'] == 'combine')['state'], 'pending')
            self.assertEqual(status['inputs']['pending'], 1)
        finally:
            self.worker.work_gate.set()
        self.assertEqual(await service.serve(asyncio.Event(), once=True), 0)
        self.assertEqual([draft['event_id'] for draft in service.results.drafts(after=1)], [slow['event_id']])
        self.assertEqual(len(list((service.root / 'drafts').glob('*.json'))), 2)

    async def test_frozen_baseline_is_shared_after_source_head_advances(self):
        service = await self.open()
        self.receive()
        original = self.git('rev-parse', 'HEAD')
        await self.advance_until(lambda value: value['missions']['states'].get('running') == 1)
        (self.repo / 'later.txt').write_text('source changed\n', encoding='utf-8')
        self.git('add', 'later.txt')
        self.git('commit', '-m', 'later source')
        self.assertNotEqual(self.git('rev-parse', 'HEAD'), original)
        self.assertEqual(await service.serve(asyncio.Event(), once=True), 0)
        self.assertTrue(all(row['base_commit'] == original for row in service.worker_host.worktrees.list()))

    async def test_lost_plan_return_reconciles_one_original_submission(self):
        service = await self.open()
        self.receive()
        await service.tick()
        submit = service.router_host.runtime.submit
        def lost(*args, **kwargs):
            submit(*args, **kwargs)
            raise OSError('accepted return lost')
        with patch.object(service.router_host.runtime, 'submit', side_effect=lost) as attempted:
            with self.assertRaisesRegex(OSError, 'return lost'):
                await service.tick()
            await self.settle()
            await service.tick()
            self.assertEqual(attempted.call_count, 1)
        self.assertEqual(await service.serve(asyncio.Event(), once=True), 0)

    async def test_missing_submission_remains_unknown_without_replay(self):
        service = await self.open()
        self.receive()
        await service.tick()
        with patch.object(service.router_host.runtime, 'submit', side_effect=OSError('unknown submission')) as attempted:
            with self.assertRaisesRegex(OSError, 'unknown submission'):
                await service.tick()
            self.assertEqual(await service.serve(asyncio.Event(), once=True), 2)
            self.assertEqual(attempted.call_count, 1)
        self.assertEqual(service.router.status()['missions']['states'], {'unknown': 1})

    async def test_source_revocation_during_final_capture_suppresses_completion(self):
        service = await self.open()
        self.receive()
        await self.advance_until(lambda value: any(job['role'] == 'review' and job['state'] == 'accepted'
            for mission in value['missions']['recent'] for job in mission['jobs']))
        await self.settle()
        manager = service.worker_host.worktrees
        capture = manager.capture_delivery
        def revoke(identity):
            service.inbox.configure_source('local', mode='ignore', actors=['operator'], threads=['project'], expected_revision=1)
            return capture(identity)
        with patch.object(manager, 'capture_delivery', side_effect=revoke):
            self.assertEqual(await service.serve(asyncio.Event(), once=True), 2)
        self.assertEqual(service.router.status()['missions']['states'], {'suppressed': 1})
        self.assertEqual(service.results.drafts(), [])

    async def test_background_writer_must_finish_before_delivery_or_dependency_unlock(self):
        self.worker.background_marker = self.root / 'release-background'
        service = await self.open()
        self.receive()
        try:
            status = await self.advance_until(lambda value: any(job.get('waiting_for') == 'background-jobs'
                for mission in value['missions']['recent'] for job in mission['jobs']))
            for _ in range(2):
                status = await service.tick()
                await self.settle()
            jobs = status['missions']['recent'][0]['jobs']
            self.assertEqual(next(job for job in jobs if job['node'] == 'first')['state'], 'accepted')
            self.assertEqual(next(job for job in jobs if job['node'] == 'combine')['state'], 'pending')
            self.assertFalse(any(job['role'] == 'integrate' for job in jobs))
        finally:
            self.worker.background_marker.write_text('release', encoding='utf-8')
        self.assertEqual(await service.serve(asyncio.Event(), once=True), 0)

    async def test_lost_dependency_import_return_is_not_applied_again(self):
        service = await self.open()
        self.receive()
        await self.advance_until(lambda value: sum(job['role'] == 'work' and job['state'] == 'accepted'
            for mission in value['missions']['recent'] for job in mission['jobs']) == 2)
        await self.settle()
        manager = service.worker_host.worktrees
        apply = manager.apply_delivery_merge
        def lost(*args, **kwargs):
            apply(*args, **kwargs)
            raise OSError('import confirmation lost')
        with patch.object(manager, 'apply_delivery_merge', side_effect=lost) as attempted:
            with self.assertRaisesRegex(OSError, 'confirmation lost'):
                await service.tick()
            await service.tick()
            self.assertEqual(attempted.call_count, 1)
        self.assertEqual(await service.serve(asyncio.Event(), once=True), 0)

    async def test_runtime_language_round_trip_reaches_actual_planner_requests(self):
        service = await self.open()
        for index, language in enumerate(('en', 'zh-CN', 'en')):
            service.router_host.config.language = language
            event = self.receive(str(index))
            service.router._collect_missions()
            mission = service.router.store.mission(service.router.store.mission_id(event['id']))
            job = service.router.store.add_job(mission, role='plan', node='goal')
            await service.router._prepare_job(mission, job)
            await service.router._submit_job(service.router.store.job(job['id']))
            await self.settle()
            request = self.planner.requests[-1]
            frame = frame_of(request)
            with current_language.set(language):
                expected = tr('superagent.mission.plan', frame=json.dumps(frame, ensure_ascii=False, sort_keys=True))
            user = next(message for message in reversed(request['messages']) if message['role'] == 'user')
            content = user['content']
            if isinstance(content, list):
                content = ''.join(part.get('text', '') for part in content)
            self.assertEqual(content, expected)
            self.assertFalse(request.get('tools'))

    async def test_corrupt_ready_session_cannot_submit_to_another_conversation(self):
        service = await self.open()
        receipt = self.receive()
        await self.advance_until(lambda value: any(job['role'] == 'work' and job['state'] == 'ready'
            for mission in value['missions']['recent'] for job in mission['jobs']))
        store = service.router.store
        mission_id = store.mission_id(receipt['id'])
        job = next(job for job in store.jobs(mission_id) if job['role'] == 'work' and job['state'] == 'ready')
        other = service.worker_host.create_session(cwd=self.repo)
        changed = {**job['data'], 'session_id': other.id}
        with store.db(write=True) as db:
            db.execute('UPDATE jobs SET data=?,data_hash=? WHERE id=?', (packed(changed), digest(packed(changed)), job['id']))
        with patch.object(service.worker_host.runtime, 'submit', side_effect=AssertionError('错误会话被执行')) as attempted:
            with self.assertRaises(HarnessError) as caught:
                await service.tick()
            self.assertEqual(caught.exception.code, 'superagent/worker-unavailable')
            self.assertEqual(attempted.call_count, 0)
        with store.db(write=True) as db:
            db.execute('UPDATE jobs SET data=?,data_hash=? WHERE id=?', (packed(job['data']), digest(packed(job['data'])), job['id']))
        await service.tick()
        await self.settle()

    async def test_corrupt_review_cache_or_completed_reply_cannot_publish(self):
        service = await self.open()
        receipt = self.receive()
        store = service.router.store
        with patch.object(store, 'complete'):
            await self.advance_until(lambda value: any(job['role'] == 'review' and job['state'] == 'done'
                for mission in value['missions']['recent'] for job in mission['jobs']))
        mission_id = store.mission_id(receipt['id'])
        job = next(job for job in store.jobs(mission_id) if job['role'] == 'review')
        changed = json.loads(packed(job['data']))
        changed['verdict']['summary'] = 'Invented replacement conclusion.'
        with store.db(write=True) as db:
            db.execute('UPDATE jobs SET data=?,data_hash=? WHERE id=?', (packed(changed), digest(packed(changed)), job['id']))
        with self.assertRaises(HarnessError) as caught:
            await service.tick()
        self.assertEqual(caught.exception.code, 'superagent/corrupt')
        self.assertFalse(list((service.root / 'drafts').glob('*.json')))
        with store.db(write=True) as db:
            db.execute('UPDATE jobs SET data=?,data_hash=? WHERE id=?', (packed(job['data']), digest(packed(job['data'])), job['id']))
        self.assertEqual(await service.serve(asyncio.Event(), once=True), 0)
        original = store.mission(mission_id)['reply']
        store.update(mission_id, before='completed', reply='Forged final reply.')
        with self.assertRaises(HarnessError):
            service.results.drafts()
        store.update(mission_id, before='completed', reply=original)

    async def test_close_during_import_preflight_does_not_start_late_workspace_write(self):
        service = await self.open()
        self.receive()
        await self.advance_until(lambda value: sum(job['role'] == 'work' and job['state'] == 'accepted'
            for mission in value['missions']['recent'] for job in mission['jobs']) == 2)
        await self.settle()
        manager = service.worker_host.worktrees
        plan = manager.plan_delivery_merge
        entered, release = threading.Event(), threading.Event()
        def gated(*args, **kwargs):
            result = plan(*args, **kwargs)
            entered.set()
            if not release.wait(5):
                raise TimeoutError('导入预检屏障未释放。')
            return result
        with patch.object(manager, 'plan_delivery_merge', side_effect=gated), \
                patch.object(manager, 'apply_delivery_merge', side_effect=AssertionError('关闭后写入')) as apply:
            tick = asyncio.create_task(service.tick())
            try:
                self.assertTrue(await asyncio.wait_for(asyncio.to_thread(entered.wait), 3))
                close = asyncio.create_task(service.aclose())
                await asyncio.sleep(0)
            finally:
                release.set()
            await asyncio.wait_for(asyncio.gather(tick, close), 10)
            self.assertEqual(apply.call_count, 0)
        self.assertTrue(service.worker_host.runtime.closed)

    async def test_revocation_during_import_preflight_prevents_new_workspace_write(self):
        service = await self.open()
        self.receive()
        await self.advance_until(lambda value: sum(job['role'] == 'work' and job['state'] == 'accepted'
            for mission in value['missions']['recent'] for job in mission['jobs']) == 2)
        await self.settle()
        manager = service.worker_host.worktrees
        plan = manager.plan_delivery_merge
        def revoked(*args, **kwargs):
            result = plan(*args, **kwargs)
            service.inbox.configure_source('local', mode='ignore', actors=['operator'], threads=['project'], expected_revision=1)
            return result
        with patch.object(manager, 'plan_delivery_merge', side_effect=revoked), \
                patch.object(manager, 'apply_delivery_merge', side_effect=AssertionError('撤销后写入')) as apply:
            self.assertEqual(await service.serve(asyncio.Event(), once=True), 2)
            self.assertEqual(apply.call_count, 0)
        self.assertEqual(service.router.status()['missions']['states'], {'suppressed': 1})

    def test_plan_rejects_unknown_dependency_and_missing_or_duplicate_acceptance(self):
        for mutate in (
            lambda value: value['steps'][0].update(depends_on=['missing']),
            lambda value: value.update(acceptance=[]),
            lambda value: value['acceptance'].append(dict(value['acceptance'][0])),
            lambda value: value['steps'][0].update(profile_id='untrusted')):
            value = json.loads(json.dumps(self.planner.plan))
            mutate(value)
            profiles = {'coding': {**self.spec['profiles']['coding'], 'repo': str(self.repo)}}
            with self.assertRaises(ValueError):
                validate_plan(value, profiles, 'local', self.spec['autonomy'])
