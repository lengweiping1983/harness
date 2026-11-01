"""真实宿主上的事件投递、幂等回查、未知提交及并发工作回归。"""
import asyncio
import json
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path
from unittest.mock import AsyncMock, patch

from harness import Harness, HarnessConfig
from harness.app.superagent_dispatch import SuperagentDispatcher
from harness.app.superagent_inbox import SuperagentInbox
from harness.core.errors import HarnessError
from harness.i18n import resources
from tests.agent_configuration_fixture import RecordingTransport


class SuperagentDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.transport = RecordingTransport()
        flags = {item.name: False for item in fields(HarnessConfig) if item.name.startswith('enable_')}
        self.config = HarnessConfig(api_key='fixture-only', home=self.root / 'state', cwd=self.root,
                                    model='fixture-model', language='en', max_retries=0, **flags)
        self.host = self.new_host()
        self.inbox = SuperagentInbox(self.root / 'superagent.sqlite3', instance_id='team')
        self.inbox.configure_source('github', mode='requests', actors=['alice', 'bot'], threads=['issue/1'])
        self.dispatcher = SuperagentDispatcher(self.host, self.inbox)
        self.body = dict(source_id='github', event_id='remote-1', actor_id='alice', text='/goal Original 原文 {data}',
                         thread_ids=['issue/1'], human=True, explicit=True)

    def new_host(self):
        host = Harness(self.config, transport=self.transport, codex_gateway_home=self.root / 'gateway')
        self.addAsyncCleanup(host.close)
        return host

    def worker(self, task='fix', *, context=False):
        directory = self.root / task
        directory.mkdir()
        session = self.host.create_session(cwd=directory)
        self.inbox.register_task(task, session_id=session.id)
        self.inbox.subscribe(task, source_id='github', thread_id='issue/1', context_only=context)
        return session

    async def dispatch(self):
        return await self.dispatcher.dispatch(source_id='github', event_id='remote-1')

    async def finish(self, result):
        records = [self.host.runtime.get(item['run_id']) for item in result['deliveries'] if item['run_id'] is not None]
        await asyncio.wait_for(asyncio.gather(*(record.task for record in records if record.task is not None)), 5)
        return records

    async def test_event_reaches_actual_worker_without_executing_slash_command(self):
        session = self.worker()
        event = self.inbox.receive(**self.body)
        result = await self.dispatch()
        records = await self.finish(result)
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.status, 'completed')
        self.assertEqual(record.session_id, session.id)
        self.assertIsNone(self.host.goals.get(session.id))
        self.assertFalse(record.text.startswith('/'))
        frame = json.loads(record.text.split('\n\n', 1)[1])
        self.assertEqual(frame['text'], self.body['text'])
        self.assertEqual(frame['id'], event['id'])
        self.assertEqual(session.latest('user/message').data['text'], record.text)
        self.assertEqual(len(self.transport.requests), 1)

    async def test_concurrent_dispatchers_share_single_submission_and_frozen_payload(self):
        self.worker()
        self.inbox.receive(**self.body)
        second = SuperagentDispatcher(self.host, self.inbox, max_active=1)
        with patch.object(self.host.runtime, 'submit', wraps=self.host.runtime.submit) as submit:
            one, two = await asyncio.gather(self.dispatch(), second.dispatch(source_id='github', event_id='remote-1'))
            self.assertEqual(one['deliveries'], two['deliveries'])
            self.assertEqual(submit.call_count, 1)
            await self.finish(one)
            for language in ('zh-CN', 'en'):
                self.config.language = language
                self.assertEqual((await self.dispatch())['deliveries'], one['deliveries'])
            self.assertEqual(submit.call_count, 1)
        self.assertEqual(len(self.transport.requests), 1)

    async def test_response_lost_after_runtime_submission_is_recovered_without_replay(self):
        self.worker()
        self.inbox.receive(**self.body)
        submit = self.host.runtime.submit
        original = OSError('original return lost')

        def lost(*args, **kwargs):
            submit(*args, **kwargs)
            raise original

        with patch.object(self.host.runtime, 'submit', side_effect=lost) as attempted:
            with self.assertRaises(OSError) as caught:
                await self.dispatch()
            self.assertIs(caught.exception, original)
            result = await self.dispatch()
            self.assertEqual(attempted.call_count, 1)
        self.assertEqual(result['deliveries'][0]['state'], 'accepted')
        await self.finish(result)
        self.assertEqual(len(self.transport.requests), 1)

    async def test_local_acknowledgement_failure_keeps_original_error_and_run_identity(self):
        self.worker()
        self.inbox.receive(**self.body)
        settle = self.dispatcher._settle
        original = OSError('local receipt failed')
        count = 0

        def fail_once(*args):
            nonlocal count
            count += 1
            if count == 1:
                raise original
            return settle(*args)

        with patch.object(self.dispatcher, '_settle', side_effect=fail_once):
            with self.assertRaises(OSError) as caught:
                await self.dispatch()
            self.assertIs(caught.exception, original)
        result = await self.dispatch()
        await self.finish(result)
        self.assertEqual(len(self.transport.requests), 1)

    async def test_missing_submission_after_unknown_outcome_is_not_resubmitted(self):
        self.worker()
        self.inbox.receive(**self.body)
        with patch.object(self.host.runtime, 'submit', side_effect=OSError('unknown submission')) as submit:
            with self.assertRaises(OSError):
                await self.dispatch()
            one = await self.dispatch()
            reopened = SuperagentDispatcher(self.host, self.inbox)
            two = await reopened.dispatch(source_id='github', event_id='remote-1')
            self.assertEqual(submit.call_count, 1)
        self.assertEqual(one['deliveries'], [{'task_id': 'fix', 'state': 'unknown', 'run_id': None}])
        self.assertEqual(two['deliveries'], one['deliveries'])
        self.assertEqual(self.transport.requests, [])

    async def test_reopened_host_finds_original_completed_run_by_persistent_key(self):
        self.worker()
        self.inbox.receive(**self.body)
        one = await self.dispatch()
        await self.finish(one)
        await self.host.close()
        self.host = self.new_host()
        self.dispatcher = SuperagentDispatcher(self.host, self.inbox)
        with patch.object(self.host.runtime, 'submit', side_effect=AssertionError('submission replayed')):
            two = await self.dispatch()
        self.assertEqual(one['deliveries'], two['deliveries'])
        self.assertEqual(len(self.transport.requests), 1)

    async def test_current_policy_and_task_stop_are_checked_before_submission(self):
        self.worker()
        self.inbox.receive(**self.body)
        self.inbox.stop_task('fix')
        result = await self.dispatch()
        self.assertEqual(result['skipped'], ['fix'])
        self.assertEqual(result['deliveries'], [])
        self.assertEqual(self.transport.requests, [])

    async def test_policy_revocation_during_startup_does_not_launch_worker(self):
        self.worker()
        self.inbox.receive(**self.body)
        entered, release = asyncio.Event(), asyncio.Event()
        start = self.host.ensure_started

        async def gated_start():
            entered.set()
            await release.wait()
            await start()

        with patch.object(self.host, 'ensure_started', side_effect=gated_start):
            task = asyncio.create_task(self.dispatch())
            try:
                await asyncio.wait_for(entered.wait(), 2)
                self.inbox.configure_source('github', mode='ignore', actors=['alice'], threads=['issue/1'], expected_revision=1)
            finally:
                release.set()
            result = await asyncio.wait_for(task, 5)
        self.assertEqual(result['skipped'], ['fix'])
        self.assertEqual(self.transport.requests, [])

    async def test_context_and_bot_candidates_do_not_start_host_or_model(self):
        self.worker(context=True)
        self.inbox.configure_source('github', mode='candidates', actors=['alice', 'bot'], threads=['issue/1'], expected_revision=1)
        self.inbox.receive(**self.body)
        with patch.object(self.host, 'ensure_started', new_callable=AsyncMock) as start:
            result = await self.dispatch()
            start.assert_not_awaited()
        self.assertEqual(result['skipped'], ['fix'])
        self.inbox.subscribe('fix', source_id='github', thread_id='issue/1', context_only=False)
        self.inbox.receive(**{**self.body, 'event_id': 'bot-event', 'actor_id': 'bot', 'human': False})
        with patch.object(self.host, 'ensure_started', new_callable=AsyncMock) as start:
            result = await self.dispatcher.dispatch(source_id='github', event_id='bot-event')
            start.assert_not_awaited()
        self.assertEqual(result['skipped'], ['fix'])
        self.assertEqual(self.transport.requests, [])

    async def test_capacity_defers_without_losing_or_duplicate_submission(self):
        self.worker('one')
        self.worker('two')
        self.inbox.receive(**self.body)
        self.dispatcher.max_active = 1
        first = await self.dispatch()
        self.assertEqual(len(first['deliveries']), 1)
        self.assertEqual(first['deferred'], ['two'])
        await self.finish(first)
        second = await self.dispatch()
        self.assertEqual(len(second['deliveries']), 2)
        self.assertEqual(second['deliveries'][0], first['deliveries'][0])
        self.assertEqual(second['deferred'], [])
        await self.finish(second)
        self.assertEqual(len(self.transport.requests), 2)

    async def test_workers_run_in_parallel_and_keep_separate_conversations(self):
        one, two = self.worker('one'), self.worker('two')
        self.inbox.receive(**self.body)
        entered, release = asyncio.Event(), asyncio.Event()
        requests = []
        stream = self.transport.post_stream

        async def gated_stream(*args, **kwargs):
            requests.append(kwargs['body'])
            if len(requests) == 2:
                entered.set()
            await release.wait()
            async for chunk in stream(*args, **kwargs):
                yield chunk

        with patch.object(self.transport, 'post_stream', side_effect=gated_stream):
            result = await self.dispatch()
            try:
                await asyncio.wait_for(entered.wait(), 3)
                self.assertIsNotNone(one.open_turn)
                self.assertIsNotNone(two.open_turn)
            finally:
                release.set()
                await self.finish(result)
        self.assertEqual({self.host.runtime.get(item['run_id']).session_id for item in result['deliveries']}, {one.id, two.id})

    async def test_closed_dispatcher_rejects_before_claiming_or_submitting(self):
        self.worker()
        self.inbox.receive(**self.body)
        self.dispatcher.close()
        with self.assertRaises(HarnessError) as caught:
            await self.dispatch()
        self.assertEqual(caught.exception.code, 'superagent/closed')
        self.assertEqual(self.transport.requests, [])

    async def test_close_during_startup_wait_does_not_claim_or_launch_late_work(self):
        self.worker()
        self.inbox.receive(**self.body)
        entered, release = asyncio.Event(), asyncio.Event()
        start = self.host.ensure_started

        async def gated_start():
            entered.set()
            await release.wait()
            await start()

        with patch.object(self.host, 'ensure_started', side_effect=gated_start):
            task = asyncio.create_task(self.dispatch())
            try:
                await asyncio.wait_for(entered.wait(), 2)
                self.dispatcher.close()
            finally:
                release.set()
            with self.assertRaises(HarnessError) as caught:
                await asyncio.wait_for(task, 5)
        self.assertEqual(caught.exception.code, 'superagent/closed')
        self.assertEqual(len(self.host.runtime.records), 0)
        self.assertEqual(self.transport.requests, [])

    async def test_submission_lookup_has_no_execution_side_effect(self):
        session = self.worker()
        with patch.object(self.host.runtime, 'submit', side_effect=AssertionError('replayed')):
            self.assertIsNone(self.host.runtime.find_submission(session.id, 'missing'))
        self.assertEqual(self.transport.requests, [])

    async def test_submission_and_reconciliation_failures_keep_both_original_errors(self):
        self.worker()
        self.inbox.receive(**self.body)
        original, recovery = OSError('submit original'), OSError('lookup original')
        with patch.object(self.host.runtime, 'submit', side_effect=original), patch.object(
                self.host.runtime, 'find_submission', side_effect=recovery):
            with self.assertRaises(ExceptionGroup) as caught:
                await self.dispatch()
        self.assertEqual(caught.exception.exceptions, (original, recovery))
        self.assertEqual((await self.dispatch())['deliveries'][0]['state'], 'unknown')
        self.assertEqual(self.transport.requests, [])

    async def test_legal_source_text_is_not_rejected_after_json_escape_expansion(self):
        self.worker()
        text = '\x01' * 65536
        self.inbox.receive(**{**self.body, 'text': text})
        result = await self.dispatch()
        records = await self.finish(result)
        self.assertEqual(json.loads(records[0].text.split('\n\n', 1)[1])['text'], text)

    async def test_payload_uses_host_language_and_preserves_source_text(self):
        for index, language in enumerate(('en', 'zh-CN', 'en')):
            session = self.worker(f'worker-{index}')
            self.config.language = language
            body = {**self.body, 'event_id': f'language-{index}'}
            self.inbox.receive(**body)
            result = await self.dispatcher.dispatch(source_id='github', event_id=body['event_id'])
            records = await self.finish(result)
            record = next(record for record in records if record.session_id == session.id)
            content = record.text.split('\n\n', 1)[1]
            self.assertEqual(record.text, resources(language)['superagent.worker_input'].format(event=content))
            self.assertEqual(json.loads(content)['text'], self.body['text'])
