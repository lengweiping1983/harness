"""真实文件交接的长节点接续、预算、证据和崩溃边界回归。"""
import asyncio
import json
import shlex
import unittest
from pathlib import Path
from unittest.mock import patch

from harness.app.superagent_mission_store import digest, packed, validate_autonomy
from harness.app.superagent_mission_status import run_progress
from harness.app.superagent_checkpoints import status_jobs, validate_checkpoint
from harness.app.superagent_local import steer_local
from harness.core.errors import HarnessError
from harness.core.language import current_language
from harness.i18n import tr
from harness.session.events import SessionEvent
from tests.test_superagent_missions import (
    SuperagentMissionFixture, MissionWorkerTransport, frame_of, tool_messages, stream_delta,
)


class CheckpointTransport(MissionWorkerTransport):
    def __init__(self):
        super().__init__()
        self.outcomes = {}
        self.repeat = False
        self.stage_gate = None
        self.stage_entered = asyncio.Event()

    async def post_stream(self, url, *, headers, body, timeout):
        request = json.loads(body)
        frame = frame_of(request)
        if frame['role'] != 'work':
            async for chunk in super().post_stream(url, headers=headers, body=body, timeout=timeout):
                yield chunk
            return
        self.requests.append(request)
        self.frames.append(frame)
        node = frame['job_id'].split('/work:')[1].split(':')[0]
        attempt = frame['attempt']
        if node == 'first' and attempt == 2 and self.stage_gate is not None:
            self.stage_entered.set()
            await self.stage_gate.wait()
        if node == 'first' and attempt == 1 and self.work_gate is not None:
            self.work_entered.set()
            await self.work_gate.wait()
        messages = tool_messages(request)
        if not messages:
            if node == 'first' and attempt == 1:
                if self.background_marker is not None:
                    tool, args = 'bash', {'command': 'while [ ! -f ' + shlex.quote(str(self.background_marker))
                        + " ]; do sleep 0.01; done; printf 'alpha\\n' > first.txt", 'run_in_background': True}
                else:
                    tool, args = 'write', {'path': 'first.txt', 'content': 'alpha\n'}
            elif node == 'first':
                command = 'test "$(cat first.txt)" = alpha'
                if not self.repeat:
                    command += " && printf 'stage two\\n' > stage.txt"
                tool, args = 'bash', {'command': command}
            elif node == 'second':
                tool, args = 'write', {'path': 'second.txt', 'content': 'beta\n'}
            else:
                tool, args = 'bash', {'command': 'cat first.txt second.txt > combined.txt'}
            yield stream_delta({'tool_calls': [{'index': 0, 'id': 'stage-' + node + '-' + str(attempt),
                'type': 'function', 'function': {'name': tool, 'arguments': json.dumps(args)}}]}, 'tool_calls')
        else:
            continuing = node == 'first' and (attempt == 1 or self.repeat)
            outcome = self.outcomes.get((node, attempt), {
                'status': 'continue' if continuing else 'ready',
                'summary': 'The alpha file is complete; a second stage remains.' if continuing else 'The node is complete.',
                'next_steps': ['Inspect first.txt and create stage.txt; preserve prior work.'] if continuing else []})
            yield stream_delta({'content': json.dumps(outcome)}, 'stop')
        yield b'data: [DONE]\n\n'


class CheckpointProtocolTests(unittest.TestCase):
    def test_checkpoint_rejects_ambiguous_states_and_unbounded_handoffs(self):
        valid = {'status': 'continue', 'summary': 'Completed one stage.', 'next_steps': ['Finish the next stage.']}
        for values in ({'status': True}, {'status': ['ready']}, {'summary': ''}, {'summary': '界' * 2731},
                {'next_steps': []}, {'next_steps': ['x'] * 17}, {'next_steps': ['界' * 683]}, {'next_steps': [False]}):
            with self.subTest(values=values), self.assertRaises((ValueError, HarnessError)):
                validate_checkpoint({**valid, **values})
        self.assertEqual(validate_checkpoint(valid), valid)

    def test_status_keeps_current_stages_and_never_hides_an_active_or_unconfirmed_predecessor(self):
        jobs = [{'role': 'work', 'node': 'long', 'attempt': attempt, 'state': 'checkpointed'} for attempt in range(1, 33)]
        jobs[-1]['state'] = 'accepted'
        jobs.append({'role': 'work', 'node': 'other', 'attempt': 1, 'state': 'pending'})
        self.assertEqual(status_jobs(jobs), jobs[-3:])
        for state in ('accepted', 'unknown', 'pending', 'blocked'):
            jobs[0]['state'] = state
            with self.subTest(state=state), self.assertRaises(HarnessError):
                status_jobs(jobs)


class SuperagentCheckpointTests(SuperagentMissionFixture):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.spec['autonomy']['max_work_turns'] = 3
        self.worker = CheckpointTransport()

    def first_stage(self):
        return next(job for mission in self.service.router.status()['missions']['recent'] for job in mission['jobs']
                    if job['role'] == 'work' and job['node'] == 'first' and job['attempt'] == 1)

    async def checkpoint(self):
        await self.open()
        self.receive()
        await self.advance_until(lambda value: any(job['role'] == 'work' and job['node'] == 'first'
            and job['attempt'] == 1 and job['state'] == 'checkpointed'
            for mission in value['missions']['recent'] for job in mission['jobs']))
        return self.service.router.store.job(self.first_stage()['id'])

    async def test_fresh_stages_preserve_files_and_unlock_only_the_final_delivery(self):
        self.worker.stage_gate = asyncio.Event()
        service = await self.open()
        self.receive()
        try:
            await self.advance_until(lambda _: self.worker.stage_entered.is_set(), settle=False)
            mission = service.router.status()['missions']['recent'][0]
            self.assertEqual(mission['work']['delivered'], 1)
            first = self.first_stage()
            self.assertEqual(first['state'], 'checkpointed')
            self.assertNotIn('waiting_for', first)
            self.assertEqual(first['checkpoint']['status'], 'continue')
            combined = next(job for job in mission['jobs'] if job['node'] == 'combine')
            self.assertEqual(combined['waiting_for'], 'dependencies')
            self.assertEqual(combined['unresolved_dependencies'][0]['job_id'], first['id'][:-1] + '2')
            self.assertFalse(any(frame['role'] == 'work' and '/work:combine:' in frame['job_id'] for frame in self.worker.frames))
        finally:
            self.worker.stage_gate.set()
        self.assertEqual(await service.serve(asyncio.Event(), once=True), 0)
        mission = service.router.status()['missions']['recent'][0]
        self.assertEqual(mission['work'], {'total': 3, 'delivered': 3})
        stages = [job for job in service.router.store.jobs(mission['id']) if job['node'] == 'first']
        self.assertEqual([job['state'] for job in stages], ['checkpointed', 'done'])
        self.assertNotEqual(stages[0]['data']['session_id'], stages[1]['data']['session_id'])
        self.assertNotEqual(stages[0]['data']['workspace_id'], stages[1]['data']['workspace_id'])
        frame = next(frame for frame in self.worker.frames if frame['job_id'] == stages[1]['id'])
        self.assertEqual(frame['checkpoint'], stages[0]['data']['checkpoint'])
        self.assertEqual(frame['predecessors'][0]['delivery'], stages[0]['data']['delivery'])
        integration = next(job for job in service.router.store.jobs(mission['id']) if job['role'] == 'integrate')
        root = Path(service.worker_host.get_session(integration['data']['session_id']).header.cwd)
        self.assertEqual((root / 'stage.txt').read_text(encoding='utf-8'), 'stage two\n')
        self.assertEqual((root / 'combined.txt').read_text(encoding='utf-8'), 'alpha\nbeta\n')
        self.assertEqual(sorted(path.name for path in self.repo.iterdir()), ['.git', 'main.txt'])

    async def test_restart_after_checkpoint_continues_without_replaying_the_previous_stage(self):
        original = await self.checkpoint()
        await self.service.aclose()
        self.worker.requests.clear()
        self.worker.frames.clear()
        service = await self.open()
        self.assertEqual(await service.serve(asyncio.Event(), once=True), 0)
        self.assertFalse(any(frame['job_id'] == original['id'] for frame in self.worker.frames))
        self.assertEqual(service.router.store.job(original['id'])['data'], original['data'])
        self.assertTrue(any(frame['job_id'] == original['id'][:-1] + '2' for frame in self.worker.frames))

    async def test_budget_exhaustion_and_repeated_unchanged_checkpoint_do_not_unlock_dependencies(self):
        for maximum, repeat, reason in ((1, False, 'budget/work-turns-exhausted'), (3, True, 'work/no-progress')):
            with self.subTest(reason=reason):
                self.spec['state_dir'] = 'state-' + str(maximum)
                self.spec['autonomy']['max_work_turns'] = maximum
                self.worker = CheckpointTransport()
                self.worker.repeat = repeat
                service = await self.open()
                self.receive()
                self.assertEqual(await service.serve(asyncio.Event(), once=True), 2)
                mission = service.router.status()['missions']['recent'][0]
                self.assertEqual((mission['state'], mission['reason']), ('blocked', reason))
                self.assertEqual(service.results.drafts(), [])
                self.assertFalse(any(frame['role'] == 'work' and '/work:combine:' in frame['job_id'] for frame in self.worker.frames))
                await service.aclose()

    async def test_blocked_and_malformed_checkpoints_do_not_get_automatic_retries(self):
        for index, outcome in enumerate(({'status': 'blocked', 'summary': 'A required user fact is missing.', 'next_steps': []},
                {'status': 'ready', 'summary': 'Premature completion.', 'next_steps': ['unfinished work']},
                'The work is ready.', {'status': 'continue', 'summary': 'No concrete next step.', 'next_steps': []})):
            with self.subTest(outcome=outcome):
                self.spec['state_dir'] = 'invalid-' + str(index)
                self.worker = CheckpointTransport()
                self.worker.outcomes[('first', 1)] = outcome
                service = await self.open()
                self.receive()
                self.assertEqual(await service.serve(asyncio.Event(), once=True), 2)
                mission = service.router.status()['missions']['recent'][0]
                self.assertEqual(mission['reason'], 'work/blocked' if index == 0 else 'work/checkpoint-invalid')
                self.assertFalse(any(frame['attempt'] > 1 for frame in self.worker.frames))
                self.assertEqual(service.results.drafts(), [])
                await service.aclose()

    async def test_corrupt_checkpoint_cache_cannot_supply_new_context_or_release_dependencies(self):
        job = await self.checkpoint()
        data = {**job['data'], 'checkpoint': {**job['data']['checkpoint'], 'summary': 'Forged checkpoint context.'}}
        with self.service.router.store.db(write=True) as db:
            db.execute('UPDATE jobs SET data=?,data_hash=? WHERE id=?', (packed(data), digest(packed(data)), job['id']))
        with self.assertRaises(HarnessError) as caught:
            await self.service.tick()
        self.assertEqual(caught.exception.code, 'superagent/corrupt')
        self.assertFalse(any(frame['role'] == 'work' and frame['attempt'] == 2 for frame in self.worker.frames))
        with self.service.router.store.db(write=True) as db:
            db.execute('UPDATE jobs SET data=?,data_hash=? WHERE id=?', (packed(job['data']), digest(packed(job['data'])), job['id']))
        await self.service.tick()

    async def test_unknown_tool_result_prevents_capture_and_continuation(self):
        service = await self.open()
        self.receive()
        original = service.router._checkpoint_progress
        def missing_result(job):
            progress = original(job)
            if job['node'] == 'first':
                session = service.worker_host.get_session(job['data']['session_id'])
                events = session.events
                result = next(event for event in events if event.type == 'tool/result')
                altered = [SessionEvent(event.type, event.seq, event.time,
                    {**event.data, 'name': 'mismatched-tool'}) if event is result else event for event in events]
                return run_progress(service.router._record(job), altered)
            return progress
        with patch.object(service.router, '_checkpoint_progress', side_effect=missing_result):
            self.assertEqual(await service.serve(asyncio.Event(), once=True), 2)
        self.assertEqual(service.router.status()['missions']['recent'][0]['reason'], 'work/effects-unconfirmed')
        self.assertEqual(self.first_stage()['state'], 'unknown')
        self.assertFalse(any(frame['role'] == 'work' and frame['attempt'] == 2 for frame in self.worker.frames))

    async def test_lost_checkpoint_capture_confirmation_uses_original_frozen_delivery(self):
        service = await self.open()
        self.receive()
        original = service.worker_host.worktrees.capture_delivery
        calls = []
        def lost(identity):
            calls.append(identity)
            result = original(identity)
            if len(calls) == 1:
                raise OSError('checkpoint capture return lost')
            return result
        with patch.object(service.worker_host.worktrees, 'capture_delivery', side_effect=lost):
            with self.assertRaisesRegex(OSError, 'capture return lost'):
                await self.advance_until(lambda _: False)
            self.assertEqual(await service.serve(asyncio.Event(), once=True), 0)
        self.assertEqual(len(calls), len(set(calls)))

    async def test_background_work_finishes_before_checkpoint_or_next_stage(self):
        self.worker.background_marker = self.root / 'release'
        service = await self.open()
        self.receive()
        try:
            await self.advance_until(lambda value: any(job.get('waiting_for') == 'background-jobs'
                for mission in value['missions']['recent'] for job in mission['jobs']))
            self.assertEqual(self.first_stage()['state'], 'accepted')
            self.assertFalse(any(frame['role'] == 'work' and frame['attempt'] == 2 for frame in self.worker.frames))
        finally:
            self.worker.background_marker.write_text('finish', encoding='utf-8')
        self.assertEqual(await service.serve(asyncio.Event(), once=True), 0)

    async def test_node_guidance_is_carried_only_to_its_own_continuation_and_final_review(self):
        self.worker.work_gate = asyncio.Event()
        service = await self.open()
        self.receive()
        try:
            await self.advance_until(lambda value: any(job['role'] == 'work' and job['node'] == 'first' and job['state'] == 'accepted'
                for mission in value['missions']['recent'] for job in mission['jobs']), settle=False)
            await asyncio.wait_for(self.worker.work_entered.wait(), 5)
            await service.tick()
            job = self.first_stage()
            steer_local(self.path, 'Preserve alpha and finish the next stage.', job_id=job['id'], request_id='stage-guide')
            await service.tick()
        finally:
            self.worker.work_gate.set()
        self.assertEqual(await service.serve(asyncio.Event(), once=True), 0)
        continuation = next(frame for frame in self.worker.frames if frame['role'] == 'work' and frame['attempt'] == 2)
        self.assertEqual(continuation['applied_guidance']['items'][0]['text'], 'Preserve alpha and finish the next stage.')
        other = [frame for frame in self.worker.frames if frame['role'] == 'work' and '/work:first:' not in frame['job_id']]
        self.assertTrue(all('applied_guidance' not in frame for frame in other))

    def test_configuration_requires_explicit_bounded_checkpoint_mode(self):
        profiles = self.spec['profiles']
        for invalid in (False, 0, 33, '2', 1.5, None):
            with self.subTest(invalid=invalid), self.assertRaises(HarnessError):
                validate_autonomy({**self.spec['autonomy'], 'max_work_turns': invalid}, profiles)
        for maximum in (1, 32):
            self.assertEqual(validate_autonomy({**self.spec['autonomy'], 'max_work_turns': maximum}, profiles)['max_work_turns'], maximum)

    async def test_language_round_trip_reaches_the_actual_checkpoint_worker(self):
        service = await self.open()
        for index, language in enumerate(('en', 'zh-CN', 'en')):
            for host in service.hosts:
                host.config.language = language
            self.receive(str(index))
            before = len(self.worker.requests)
            self.assertEqual(await service.serve(asyncio.Event(), once=True), 0)
            request = next(request for request in self.worker.requests[before:] if frame_of(request)['role'] == 'work')
            frame = frame_of(request)
            with current_language.set(language):
                expected = tr('superagent.mission.checkpoint', frame=packed(frame))
            def content(message):
                value = message.get('content', '')
                return value if isinstance(value, str) else ''.join(item.get('text', '') for item in value)
            self.assertTrue(any(message['role'] == 'user' and content(message) == expected for message in request['messages']))
