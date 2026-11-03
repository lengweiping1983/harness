"""真实节点引导的归属、原生受理、应用证据及未知结果不重放回归。"""
import asyncio
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from harness.app.superagent_guidance import command_id, validate_command, validate_receipt
from harness.app.superagent_local import read_guidance_receipt, steer_local
from harness.core.errors import HarnessError
from harness.core.language import current_language
from harness.i18n import tr
from tests.test_superagent_missions import (
    MissionWorkerTransport, frame_of, stream_delta,
)
from tests import test_superagent_missions as mission_fixture


def message_text(message):
    value = message.get('content', '')
    return value if isinstance(value, str) else ''.join(item.get('text', '') for item in value)


class GuidedWorkerTransport(MissionWorkerTransport):
    async def post_stream(self, url, *, headers, body, timeout):
        request = json.loads(body)
        frame = frame_of(request)
        if frame['role'] == 'work' and any(message['role'] == 'user' and message_text(message).startswith('guidance:')
                                         for message in request['messages']):
            self.requests.append(request)
            self.frames.append(frame)
            if any(message['role'] == 'tool' and message.get('tool_call_id') == 'guided-prepare' for message in request['messages']):
                yield stream_delta({'content': 'The current node incorporated its additional execution context.'}, 'stop')
            else:
                # 引导可使旧模型提议不进入执行；新推理须实际完成节点工作，不能只报告收到。
                yield stream_delta({'tool_calls': [{'index': 0, 'id': 'guided-prepare', 'type': 'function',
                    'function': {'name': 'bash', 'arguments': json.dumps({'command': "test -f first.txt || printf 'alpha\\n' > first.txt"})}}]}, 'tool_calls')
            yield b'data: [DONE]\n\n'
        else:
            async for chunk in super().post_stream(url, headers=headers, body=body, timeout=timeout):
                yield chunk


class SuperagentGuidanceTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = mission_fixture.SuperagentMissionTests.asyncSetUp
    git = mission_fixture.SuperagentMissionTests.git
    open = mission_fixture.SuperagentMissionTests.open
    receive = mission_fixture.SuperagentMissionTests.receive
    settle = mission_fixture.SuperagentMissionTests.settle
    advance_until = mission_fixture.SuperagentMissionTests.advance_until

    async def begin(self):
        self.worker = GuidedWorkerTransport()
        self.worker.work_gate = asyncio.Event()
        self.addAsyncCleanup(self.release)
        service = await self.open()
        self.receive()
        await self.advance_until(lambda status: any(job['role'] == 'work' and job['node'] == 'first' and job['state'] == 'accepted'
            for mission in status['missions']['recent'] for job in mission['jobs']), settle=False)
        await asyncio.wait_for(self.worker.work_entered.wait(), 5)
        await service.tick()
        self.guidance = service.router.guidance
        self.job = next(job for mission in service.router.status()['missions']['recent'] for job in mission['jobs']
                        if job['role'] == 'work' and job['node'] == 'first')
        return service

    async def release(self):
        self.worker.work_gate.set()

    def issue(self, request='guide', text='guidance: Keep the expected alpha content and inspect the existing files.'):
        queued = steer_local(self.path, text, job_id=self.job['id'], request_id=request)
        spec = json.loads(Path(queued['path']).read_text(encoding='utf-8'))
        return command_id(spec['instance_id'], request), spec

    def accepted_events(self):
        session = self.service.worker_host.get_session(self.job['session_id'])
        return [event for event in session.events_of_type('note') if event.data.get('kind') == 'steering/accepted']

    async def test_node_instruction_reaches_original_run_once_and_later_independent_context(self):
        service = await self.begin()
        original = service.router.store.job(self.job['id'])
        mission = service.router.store.mission(original['mission'])
        identity, spec = self.issue()
        await service.tick()
        receipt = read_guidance_receipt(self.path, spec['request_id'])
        self.assertEqual((receipt['state'], receipt['application']), ('accepted', 'pending'))
        self.assertEqual(len(self.accepted_events()), 1)
        duplicate = steer_local(self.path, spec['text'], job_id=self.job['id'], request_id=spec['request_id'])
        self.assertTrue(duplicate['duplicate'])
        self.worker.work_gate.set()
        self.assertEqual(await service.serve(asyncio.Event(), once=True), 0,
                         [(item['state'], item['reason']) for item in service.router.status()['missions']['recent']])
        receipt = read_guidance_receipt(self.path, spec['request_id'])
        self.assertEqual((receipt['state'], receipt['application']), ('accepted', 'applied'))
        self.assertGreater(receipt['applied_seq'], receipt['accepted_seq'])
        self.assertEqual(len(self.accepted_events()), 1)
        self.assertTrue(any(spec['text'] == message_text(message) for request in self.worker.requests
                            for message in request['messages'] if message['role'] == 'user'))
        for frame in self.worker.frames:
            if frame['role'] == 'work':
                self.assertNotIn('applied_guidance', frame)
            else:
                self.assertEqual(frame['applied_guidance']['items'][0]['text'], spec['text'])
                self.assertEqual(frame['applied_guidance']['items'][0]['run_id'], self.job['run_id'])
        self.assertEqual(service.router.store.job(self.job['id'])['data']['payload'], original['data']['payload'])
        self.assertEqual(service.router.store.mission(mission['id'])['plan_hash'], mission['plan_hash'])
        self.assertEqual(service.router.store.mission(mission['id'])['plan']['acceptance'], mission['plan']['acceptance'])
        self.assertNotIn(spec['text'], json.dumps(service.router.status()))
        with patch.object(service.worker_host, 'steer', side_effect=AssertionError('只读状态不得再次执行')):
            self.assertEqual(self.guidance.receipt(identity)['application'], 'applied')

    async def test_revocation_during_steering_lock_wait_prevents_acceptance(self):
        service = await self.begin()
        identity, spec = self.issue()
        self.guidance.enqueue(spec)
        agent = service.worker_host._agents[self.job['session_id']]
        claimed = asyncio.Event()
        change = self.guidance._change
        def track(row, state, reason=None):
            result = change(row, state, reason)
            if state == 'submitting':
                claimed.set()
            return result
        await agent.steering_lock.acquire()
        try:
            with patch.object(self.guidance, '_change', side_effect=track):
                task = asyncio.create_task(self.guidance.tick())
                await asyncio.wait_for(claimed.wait(), 5)
                service.inbox.configure_source('local', mode='ignore', actors=['operator'], threads=['project'], expected_revision=1)
                agent.steering_lock.release()
                await asyncio.wait_for(task, 5)
        finally:
            if agent.steering_lock.locked():
                agent.steering_lock.release()
        receipt = self.guidance.receipt(identity)
        self.assertEqual((receipt['state'], receipt['reason']), ('rejected', 'superagent/guidance-unauthorized'))
        self.assertEqual(self.accepted_events(), [])

    async def test_accepted_instruction_survives_revocation_and_close_flushes_unapplied_receipt(self):
        service = await self.begin()
        identity, spec = self.issue()
        await service.tick()
        accepted = self.guidance.receipt(identity)['accepted_seq']
        service.inbox.configure_source('local', mode='ignore', actors=['operator'], threads=['project'], expected_revision=1)
        with patch.object(service.worker_host, 'steer', side_effect=AssertionError('已受理指令不得重新提交')):
            await self.guidance.tick()
        await service.aclose()
        receipt = read_guidance_receipt(self.path, spec['request_id'])
        self.assertEqual((receipt['state'], receipt['application'], receipt['accepted_seq']), ('accepted', 'unapplied', accepted))

    async def test_lost_accepted_return_reconciles_original_request_without_replay(self):
        service = await self.begin()
        identity, spec = self.issue()
        self.guidance.enqueue(spec)
        steer = service.worker_host.steer
        async def lost(*args, **kwargs):
            await steer(*args, **kwargs)
            raise OSError('accepted guidance return lost')
        with patch.object(service.worker_host, 'steer', side_effect=lost) as attempted:
            with self.assertRaisesRegex(OSError, 'return lost'):
                await self.guidance.tick()
            await self.guidance.tick()
            self.assertEqual(attempted.call_count, 1)
        self.assertEqual(self.guidance.receipt(identity)['state'], 'accepted')
        self.assertEqual(len(self.accepted_events()), 1)

    async def test_unconfirmed_intent_only_queries_and_keeps_original_run(self):
        service = await self.begin()
        identity, spec = self.issue()
        self.guidance.enqueue(spec)
        with self.guidance.db(write=True) as db:
            db.execute("UPDATE commands SET state='submitting' WHERE id=?", (identity,))
        with patch.object(service.worker_host, 'steer', side_effect=AssertionError('未知意图不得重新执行')) as attempted:
            await self.guidance.tick()
            before = self.guidance.path.read_bytes()
            log_path = service.worker_host.sessions.events_path(self.job['session_id'])
            events_before = log_path.read_bytes()
            await self.guidance.tick()
            self.guidance.receipt(identity)
            self.guidance.status()
            self.assertEqual(self.guidance.path.read_bytes(), before)
            self.assertEqual(log_path.read_bytes(), events_before)
            receipt = self.guidance.receipt(identity)
            self.assertEqual((receipt['state'], receipt['application']), ('unknown', None))
            self.assertEqual(attempted.call_count, 0)
        self.assertEqual(self.accepted_events(), [])
        self.assertEqual(receipt['run_id'], self.job['run_id'])

    async def test_confirmation_write_failure_recovers_one_original_acceptance(self):
        service = await self.begin()
        identity, spec = self.issue()
        self.guidance.enqueue(spec)
        change = self.guidance._change
        failed = False
        def lose_confirmation(row, state, reason=None):
            nonlocal failed
            if state == 'accepted' and not failed:
                failed = True
                raise OSError('guidance confirmation lost')
            return change(row, state, reason)
        with patch.object(service.worker_host, 'steer', wraps=service.worker_host.steer) as attempted:
            with patch.object(self.guidance, '_change', side_effect=lose_confirmation):
                with self.assertRaisesRegex(OSError, 'confirmation lost'):
                    await self.guidance.tick()
            self.assertEqual(self.guidance.get(identity)['state'], 'submitting')
            await self.guidance.tick()
            self.assertEqual(attempted.call_count, 1)
        self.assertEqual(self.guidance.receipt(identity)['state'], 'accepted')
        self.assertEqual(len(self.accepted_events()), 1)

    async def test_restart_keeps_original_unapplied_receipt_without_new_model_or_submission(self):
        service = await self.begin()
        identity, spec = self.issue()
        await service.tick()
        accepted = self.guidance.receipt(identity)['accepted_seq']
        await service.aclose()
        requests = len(self.worker.requests)
        reopened = await self.open()
        with patch.object(reopened.worker_host, 'steer', side_effect=AssertionError('重开不得重放引导')) as attempted:
            await reopened.router.guidance.tick()
            receipt = reopened.router.guidance.receipt(identity)
            self.assertEqual((receipt['state'], receipt['application'], receipt['accepted_seq']), ('accepted', 'unapplied', accepted))
            self.assertEqual(attempted.call_count, 0)
        self.assertEqual(len(self.worker.requests), requests)
        self.assertEqual(read_guidance_receipt(self.path, spec['request_id'])['application'], 'unapplied')

    async def test_async_acceptance_guards_are_rejected_and_existing_receipts_skip_new_guard(self):
        service = await self.begin()
        host = service.worker_host
        async def asynchronous_guard():
            raise AssertionError('异步授权不能作为同步检查执行')
        def hidden_asynchronous_guard():
            return asynchronous_guard()
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                for guard in (asynchronous_guard, hidden_asynchronous_guard):
                    with self.subTest(language=language, guard=guard.__name__), self.assertRaises(HarnessError) as caught:
                        await host.steer(self.job['session_id'], 'guidance: extra context', expected_turn=self.job['run']['turn'],
                                         request_id='native', before_accept=guard)
                    self.assertEqual(caught.exception.code, 'run/invalid')
                    self.assertEqual(caught.exception.message, tr('runtime.steering.invalid_guard'))
        self.assertEqual(self.accepted_events(), [])
        receipt = await host.steer(self.job['session_id'], 'guidance: extra context', expected_turn=self.job['run']['turn'], request_id='native')
        def rejected_guard():
            raise AssertionError('重复回查不得重新执行授权')
        duplicate = await host.steer(self.job['session_id'], 'guidance: extra context', expected_turn=self.job['run']['turn'],
                                     request_id='native', before_accept=rejected_guard)
        self.assertEqual(duplicate['receipt']['accepted_seq'], receipt['receipt']['accepted_seq'])
        self.assertEqual(len(self.accepted_events()), 1)

    async def test_repeated_waiter_cancellation_keeps_one_owned_acceptance(self):
        service = await self.begin()
        identity, spec = self.issue()
        self.guidance.enqueue(spec)
        agent = service.worker_host._agents[self.job['session_id']]
        claimed = asyncio.Event()
        change = self.guidance._change
        def track(row, state, reason=None):
            result = change(row, state, reason)
            if state == 'submitting':
                claimed.set()
            return result
        await agent.steering_lock.acquire()
        try:
            with patch.object(self.guidance, '_change', side_effect=track):
                waiting = asyncio.create_task(self.guidance.tick())
                await asyncio.wait_for(claimed.wait(), 5)
                waiting.cancel()
                await asyncio.sleep(0)
                waiting.cancel()
                agent.steering_lock.release()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(waiting, 5)
        finally:
            if agent.steering_lock.locked():
                agent.steering_lock.release()
        self.assertEqual(self.guidance.receipt(identity)['state'], 'accepted')
        self.assertEqual(len(self.accepted_events()), 1)
        with patch.object(service.worker_host, 'steer', side_effect=AssertionError('取消等待不能导致重放')):
            await self.guidance.tick()

    async def test_original_display_text_cannot_replace_model_instruction_identity(self):
        service = await self.begin()
        identity, spec = self.issue()
        self.guidance.enqueue(spec)
        await service.worker_host.steer(self.job['session_id'], 'Different model instruction', original_text=spec['text'],
            expected_turn=spec['turn'], request_id='superagent-guidance:' + identity)
        with self.assertRaises(HarnessError) as caught:
            self.guidance._original(self.guidance.get(identity))
        self.assertEqual(caught.exception.code, 'superagent/corrupt')

    async def test_close_before_queued_tick_enters_guidance_is_normal_shutdown(self):
        service = await self.open()
        entered, release, closing = asyncio.Event(), asyncio.Event(), asyncio.Event()
        tick, close = service._tick, service.router.close
        async def delayed_tick():
            entered.set()
            await release.wait()
            return await tick()
        def mark_close():
            close()
            closing.set()
        with patch.object(service, '_tick', side_effect=delayed_tick), patch.object(service.router, 'close', side_effect=mark_close):
            task = asyncio.create_task(service.tick())
            await asyncio.wait_for(entered.wait(), 5)
            shutdown = asyncio.create_task(service.aclose())
            await asyncio.wait_for(closing.wait(), 5)
            release.set()
            await asyncio.wait_for(asyncio.gather(task, shutdown), 5)
        self.assertEqual(json.loads((service.root / 'status.json').read_text(encoding='utf-8'))['runtime_state'], 'stopped')

    async def test_stable_request_conflicts_and_wrong_turn_never_change_native_input(self):
        await self.begin()
        identity, spec = self.issue()
        self.guidance.enqueue(spec)
        for key, value in (('text', 'changed'), ('job_id', 'other'), ('run_id', 'other'), ('turn', spec['turn'] + 1),
                           ('source_id', 'other'), ('actor_id', 'other'), ('thread_id', 'other')):
            with self.subTest(field=key), self.assertRaises(HarnessError) as caught:
                self.guidance.enqueue({**spec, key: value})
            self.assertEqual(caught.exception.code, 'superagent/event-conflict')
        wrong = {**spec, 'request_id': 'wrong-turn', 'turn': spec['turn'] + 1}
        rejected = self.guidance.enqueue(wrong)
        await self.guidance.tick()
        self.assertEqual(self.guidance.receipt(rejected['id'])['state'], 'rejected')
        self.assertEqual(self.guidance.receipt(identity)['state'], 'accepted')
        self.assertEqual(len(self.accepted_events()), 1)

    async def test_new_requests_with_wrong_target_or_authority_never_reach_native_steering(self):
        service = await self.begin()
        _, spec = self.issue()
        requests = []
        for index, (key, value) in enumerate((('mission_id', 'other'), ('run_id', 'other'), ('revision', 2),
                                             ('source_id', 'other'), ('actor_id', 'other'), ('thread_id', 'other'))):
            requests.append(self.guidance.enqueue({**spec, key: value, 'request_id': 'invalid-' + str(index)})['id'])
        planner = next(job for job in service.router.store.jobs(spec['mission_id']) if job['role'] == 'plan')
        requests.append(self.guidance.enqueue({**spec, 'request_id': 'planner-target',
                                               'job_id': planner['id'], 'run_id': planner['data']['run_id']})['id'])
        with patch.object(service.worker_host, 'steer', side_effect=AssertionError('错误归属不得发起原生引导')) as attempted:
            await self.guidance.tick()
            self.assertEqual(attempted.call_count, 0)
        self.assertTrue(all(self.guidance.receipt(identity)['state'] == 'rejected' for identity in requests))
        self.assertEqual(self.accepted_events(), [])

    async def test_late_original_turn_does_not_redirect_to_new_run_in_same_conversation(self):
        service = await self.begin()
        _, spec = self.issue()
        self.worker.work_gate.set()
        await self.settle()
        original = service.router.store.job(self.job['id'])
        self.worker.work_gate = asyncio.Event()
        self.worker.work_entered.clear()
        newer = service.worker_host.runtime.submit(self.job['session_id'], original['data']['payload'],
                                                   key='next-run', request_id='next-run')
        await asyncio.wait_for(self.worker.work_entered.wait(), 5)
        self.assertNotEqual(newer.id, spec['run_id'])
        self.assertNotEqual(service.worker_host.get_session(self.job['session_id']).open_turn, spec['turn'])
        late = self.guidance.enqueue({**spec, 'request_id': 'late'})
        with patch.object(service.worker_host, 'steer', side_effect=AssertionError('迟到指令不得送到新运行')) as attempted:
            await self.guidance.tick()
            self.assertEqual(attempted.call_count, 0)
        self.assertEqual(self.guidance.receipt(late['id'])['state'], 'rejected')
        self.assertEqual(self.accepted_events(), [])
        self.worker.work_gate.set()
        await self.settle()

    async def test_recent_applied_guidance_retains_newest_correction_with_global_budget(self):
        service = await self.begin()
        for index in range(6):
            _, spec = self.issue('long-' + str(index), 'guidance: ' + str(index) + ' ' + 'x' * 3000)
            self.guidance.enqueue(spec)
        await self.guidance.tick()
        self.assertEqual(self.guidance.applied_context(self.job['id'].split('/')[0])['items'], [])
        self.worker.work_gate.set()
        self.assertEqual(await service.serve(asyncio.Event(), once=True), 0)
        context = self.guidance.applied_context(self.job['id'].split('/')[0])
        self.assertEqual((context['total'], context['shown'], context['has_more']), (6, 4, True))
        self.assertEqual([row['request_id'] for row in context['items']], ['long-2', 'long-3', 'long-4', 'long-5'])
        self.assertEqual(sum(len(row['text']) for row in context['items']), 8192)
        self.assertTrue(all(row['text_truncated'] for row in context['items']))


class GuidanceProtocolTests(unittest.TestCase):
    def test_command_requires_strict_types_and_complete_fields(self):
        value = {'version': 1, 'instance_id': 'team', 'request_id': 'one', 'mission_id': 'mission', 'job_id': 'job',
                 'run_id': 'run', 'turn': 1, 'source_id': 'local', 'revision': 1, 'actor_id': 'actor', 'thread_id': 'thread', 'text': 'instruction'}
        self.assertEqual(validate_command(value), value)
        for changed in ({**value, 'turn': True}, {**value, 'revision': '1'}, {**value, 'extra': None}, {**value, 'text': '\0'}):
            with self.assertRaises(HarnessError):
                validate_command(changed)

    def test_receipt_rejects_false_application_and_broken_sequence_without_repair(self):
        value = {'version': 1, 'instance_id': 'team', 'id': command_id('team', 'one'), 'request_id': 'one',
                 'mission_id': 'mission', 'job_id': 'job', 'run_id': 'run', 'turn': 1, 'state': 'accepted', 'reason': None,
                 'application': 'applied', 'accepted_seq': 2, 'applied_seq': 3}
        self.assertEqual(validate_receipt(value), value)
        for changed in ({**value, 'state': 'rejected'}, {**value, 'state': 'pending'}, {**value, 'turn': True},
                        {**value, 'applied_seq': 2}, {**value, 'accepted_seq': None}, {**value, 'extra': None}):
            with self.assertRaises(HarnessError):
                validate_receipt(changed)
