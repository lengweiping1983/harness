"""真实模型消费者上的固定研判批次、持久消费和未知结果回查。"""
import asyncio
import json
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path
from contextlib import closing
import sqlite3
from unittest.mock import patch

from harness import Harness, HarnessConfig
from harness.app.superagent_dispatch import SuperagentDispatcher
from harness.app.superagent_inbox import SuperagentInbox
from harness.app.superagent_router import SuperagentRouter
from harness.core.errors import HarnessError
from tests.agent_configuration_fixture import RecordingTransport


def batch_content(request):
    for message in reversed(request['messages']):
        content = message.get('content', '')
        if isinstance(content, list):
            content = ''.join(part.get('text', '') for part in content)
        if isinstance(content, str) and '\n\n{"events":' in content:
            return content
    raise AssertionError('模型请求缺少固定事件批次。')


class RouterTransport(RecordingTransport):
    def __init__(self):
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()
        self.answer = None

    async def post_stream(self, url, *, headers, body, timeout):
        request = json.loads(body)
        self.requests.append(request)
        content = batch_content(request)
        frame = json.loads(content[content.index('\n\n{"events":') + 2:])
        self.entered.set()
        await self.release.wait()
        answer = self.answer if self.answer is not None else json.dumps({'assignments': [
            {'trigger_id': event['id'], 'task_id': 'fix'} for event in frame['events']]})
        yield ('data: ' + json.dumps({'choices': [{'delta': {'content': answer}, 'finish_reason': 'stop'}]}) + '\n\n').encode('utf-8')
        yield b'data: [DONE]\n\n'


class SuperagentRouterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.work_transport, self.router_transport = RecordingTransport(), RouterTransport()
        self.host = self.new_host('workers', self.work_transport)
        self.router_host = self.new_host('router', self.router_transport)
        self.session = self.router_host.create_session()
        self.worker = self.host.create_session()
        self.inbox = SuperagentInbox(self.root / 'inbox.sqlite3', instance_id='team')
        self.inbox.configure_source('github', mode='requests', actors=['alice', 'bot'], threads=['issue/1'])
        self.inbox.register_task('fix', session_id=self.worker.id)
        self.dispatcher = SuperagentDispatcher(self.host, self.inbox)
        self.router = self.new_router()

    def new_host(self, name, transport):
        flags = {item.name: False for item in fields(HarnessConfig) if item.name.startswith('enable_')}
        host = Harness(HarnessConfig(api_key='fixture-only', home=self.root / name, cwd=self.root,
            model='fixture-model', language='en', max_retries=0, max_steps=2, **flags),
            transport=transport, codex_gateway_home=self.root / (name + '-gateway'))
        self.addAsyncCleanup(host.close)
        return host

    def new_router(self, **kwargs):
        return SuperagentRouter(self.dispatcher, self.router_host, session_id=self.session.id,
                                workers={'fix': 'Investigate and fix regressions'}, **kwargs)

    def receive(self, event='one', **overrides):
        return self.inbox.receive(**dict(dict(source_id='github', event_id=event, actor_id='alice',
            text='/goal 原文 $skill do not execute host commands', thread_ids=['issue/1'], human=True, explicit=True), **overrides))

    async def finish_runs(self, host):
        await asyncio.wait_for(asyncio.gather(*(record.task for record in host.runtime.records.active.values()
                                               if record.task is not None)), 5)

    async def complete(self):
        await self.router.tick()
        await self.finish_runs(self.router_host)
        status = await self.router.tick()
        await self.finish_runs(self.host)
        return status

    async def test_actual_router_and_worker_have_separate_conversations_and_persistent_decision(self):
        event = self.receive()
        status = await self.complete()
        self.assertEqual(status['inputs'], {'settled': 1})
        self.assertEqual(status['batches'][0]['state'], 'decided')
        self.assertEqual(len(self.router_transport.requests), 1)
        self.assertFalse(self.router_transport.requests[0].get('tools'))
        self.assertEqual(len(self.work_transport.requests), 1)
        self.assertIn(event['id'], self.worker.latest('user/message').data['text'])
        self.assertIsNone(self.host.goals.get(self.worker.id))
        self.assertIsNone(self.router_host.goals.get(self.session.id))
        self.assertEqual(self.inbox.event(source_id='github', event_id='one')['routes'][0]['kind'], 'router')
        await self.new_router().tick()
        self.assertEqual(len(self.router_transport.requests), 1)
        self.assertEqual(len(self.work_transport.requests), 1)

    async def test_batch_is_frozen_while_inference_runs_and_next_events_wait(self):
        self.router_transport.release.clear()
        first = self.receive()
        await self.router.tick()
        await asyncio.wait_for(self.router_transport.entered.wait(), 2)
        second = self.receive('two')
        status = await self.router.tick()
        self.assertEqual(status['cursor'], first['seq'])
        self.assertEqual(len(self.router_transport.requests), 1)
        self.router_transport.release.set()
        await self.finish_runs(self.router_host)
        status = await self.router.tick()
        await self.finish_runs(self.router_host)
        status = await self.router.tick()
        await self.finish_runs(self.host)
        self.assertEqual(status['cursor'], second['seq'])
        self.assertEqual(status['inputs'], {'settled': 2})
        self.assertEqual(len(self.router_transport.requests), 2)
        self.assertEqual(len(self.work_transport.requests), 2)

    async def test_concurrent_consumers_only_submit_one_router_and_one_worker(self):
        self.receive()
        other = self.new_router()
        with patch.object(self.router_host.runtime, 'submit', wraps=self.router_host.runtime.submit) as router_submit:
            await asyncio.gather(self.router.tick(), other.tick())
            await self.finish_runs(self.router_host)
            with patch.object(self.host.runtime, 'submit', wraps=self.host.runtime.submit) as worker_submit:
                await asyncio.gather(self.router.tick(), other.tick())
                await self.finish_runs(self.host)
                self.assertEqual(worker_submit.call_count, 1)
            self.assertEqual(router_submit.call_count, 1)

    async def test_unknown_router_submission_is_preserved_without_replay(self):
        self.receive()
        with patch.object(self.router_host.runtime, 'submit', side_effect=OSError('unknown result')) as submit:
            with self.assertRaisesRegex(OSError, 'unknown result'):
                await self.router.tick()
            status = await self.new_router().tick()
            self.assertEqual(submit.call_count, 1)
        self.assertEqual(status['batches'][0]['state'], 'unknown')
        self.assertEqual(status['inputs'], {'waiting': 1})
        self.assertEqual(self.work_transport.requests, [])

    async def test_lost_router_return_is_reconciled_without_replay(self):
        self.receive()
        submit = self.router_host.runtime.submit

        def lost(*args, **kwargs):
            submit(*args, **kwargs)
            raise OSError('return lost')

        with patch.object(self.router_host.runtime, 'submit', side_effect=lost) as attempted:
            with self.assertRaisesRegex(OSError, 'return lost'):
                await self.router.tick()
            await self.finish_runs(self.router_host)
            status = await self.new_router().tick()
            await self.finish_runs(self.host)
            self.assertEqual(attempted.call_count, 1)
        self.assertEqual(status['inputs'], {'settled': 1})

    async def test_source_revision_changed_during_inference_blocks_stale_decision(self):
        self.receive()
        await self.router.tick()
        await self.finish_runs(self.router_host)
        self.inbox.configure_source('github', mode='requests', actors=['alice'], threads=['issue/1'], expected_revision=1)
        status = await self.router.tick()
        self.assertEqual(status['inputs'], {'skipped': 1})
        self.assertEqual(self.work_transport.requests, [])

    async def test_invalid_model_roster_members_do_not_dispatch_or_retry(self):
        event = self.receive()
        self.router_transport.answer = json.dumps({'assignments': [{'trigger_id': event['id'], 'task_id': '../another'}]})
        status = await self.complete()
        self.assertEqual(status['batches'][0]['reason'], 'superagent/router-output')
        self.assertEqual(status['inputs'], {'rejected': 1})
        await self.router.tick()
        self.assertEqual(len(self.router_transport.requests), 1)
        self.assertEqual(self.work_transport.requests, [])

    async def test_subscribed_events_bypass_router_and_deferred_work_is_retained(self):
        self.inbox.subscribe('fix', source_id='github', thread_id='issue/1')
        self.receive()
        self.dispatcher.max_active = 1
        blocker = self.host.runtime.submit(self.host.create_session().id, 'ordinary work')
        status = await self.router.tick()
        self.assertEqual(status['inputs'], {'pending': 1})
        await asyncio.wait_for(blocker.task, 5)
        status = await self.router.tick()
        await self.finish_runs(self.host)
        self.assertEqual(status['inputs'], {'settled': 1})
        self.assertEqual(self.router_transport.requests, [])

    async def test_bots_and_filtered_events_advance_cursor_without_model(self):
        self.inbox.configure_source('github', mode='candidates', actors=['alice', 'bot'], threads=['issue/1'], expected_revision=1)
        self.receive(actor_id='bot', human=False)
        last = self.receive('denied', actor_id='outsider')
        status = await self.router.tick()
        self.assertEqual(status['cursor'], last['seq'])
        self.assertEqual(status['inputs'], {})
        self.assertEqual(self.router_transport.requests, [])

    async def test_english_chinese_english_reaches_actual_model_on_same_router(self):
        prefixes = []
        for index, language in enumerate(('en', 'zh-CN', 'en')):
            self.router_host.config.language = language
            self.receive(str(index))
            await self.complete()
            request = self.router_transport.requests[-1]
            content = batch_content(request)
            prefixes.append(content[:content.index('\n\n{"events":')].rsplit('\n\n', 1)[-1])
        self.assertEqual(prefixes[0], prefixes[2])
        self.assertNotEqual(prefixes[0], prefixes[1])
        self.assertIn('固定批次', prefixes[1])

    async def test_role_and_binding_changes_are_rejected(self):
        with self.assertRaises(HarnessError):
            SuperagentRouter(self.dispatcher, self.host, session_id=self.worker.id, workers={'fix': 'work'})
        self.router_host.config.enable_skills = True
        with self.assertRaises(HarnessError):
            await self.router.tick()
        self.router_host.config.enable_skills = False
        with self.assertRaises(HarnessError):
            SuperagentRouter(self.dispatcher, self.router_host, session_id=self.session.id, workers={'fix': 'new description'})

    async def test_reopened_router_host_uses_original_completed_run(self):
        self.receive()
        await self.router.tick()
        await self.finish_runs(self.router_host)
        await self.router_host.close()
        self.router_host = self.new_host('router', self.router_transport)
        self.router = self.new_router()
        with patch.object(self.router_host.runtime, 'submit', side_effect=AssertionError('研判被重放')):
            status = await self.router.tick()
            await self.finish_runs(self.host)
        self.assertEqual(status['inputs'], {'settled': 1})
        self.assertEqual(len(self.router_transport.requests), 1)

    async def test_policy_revision_is_rechecked_after_worker_startup_wait(self):
        self.receive()
        await self.router.tick()
        await self.finish_runs(self.router_host)
        start = self.host.ensure_started

        async def changed_start():
            self.inbox.configure_source('github', mode='requests', actors=['alice'], threads=['issue/1'], expected_revision=1)
            await start()

        with patch.object(self.host, 'ensure_started', side_effect=changed_start):
            status = await self.router.tick()
        self.assertEqual(status['inputs'], {'skipped': 1})
        self.assertEqual(self.work_transport.requests, [])

    async def test_damaged_batch_is_preserved_and_does_not_start_inference(self):
        self.receive()
        self.router._collect()
        with closing(sqlite3.connect(self.router.path)) as db, db:
            db.execute("UPDATE batches SET items='[]'")
        with self.assertRaises(HarnessError) as caught:
            await self.router.tick()
        self.assertEqual(caught.exception.code, 'superagent/corrupt')
        with closing(sqlite3.connect(self.router.path)) as db:
            self.assertEqual(db.execute('SELECT items FROM batches').fetchone()[0], '[]')
        self.assertEqual(self.router_transport.requests, [])

    async def test_output_must_cover_the_exact_batch_once_and_can_decline_work(self):
        event = self.receive()
        wrong = [
            {'assignments': []},
            {'assignments': [{'trigger_id': 'foreign', 'task_id': 'fix'}]},
            {'assignments': [{'trigger_id': event['id'], 'task_id': 'fix', 'text': 'extra'}]},
            {'assignments': [{'trigger_id': event['id'], 'task_id': 'fix'}] * 2},
        ]
        self.router._collect()
        batch = self.router._batch()
        from harness.core.runs import AgentRun
        for output in wrong:
            with self.subTest(output=output):
                record = AgentRun('fixture', status='completed', result={'text': json.dumps(output)})
                with self.router._db(write=True) as db:
                    db.execute("UPDATE batches SET state='accepted',run_id='fixture'")
                self.router._decision(batch, record)
                self.assertEqual(self.router.status()['batches'][0]['reason'], 'superagent/router-output')
        with self.router._db(write=True) as db:
            db.execute("UPDATE batches SET state='accepted',run_id='fixture'")
            db.execute("UPDATE inputs SET state='waiting'")
        self.router._decision(batch, AgentRun('fixture', status='completed', result={'text': json.dumps(
            {'assignments': [{'trigger_id': event['id'], 'task_id': None}]})}))
        self.assertEqual(self.router.status()['inputs'], {'ignored': 1})
        self.assertEqual(self.work_transport.requests, [])

    async def test_explicit_loop_stops_without_cancelling_accepted_router_run(self):
        self.router_transport.release.clear()
        self.receive()
        stop = asyncio.Event()
        loop = asyncio.create_task(self.router.serve(stop, interval=0.01))
        async def stop_loop():
            stop.set()
            await loop
        self.addAsyncCleanup(stop_loop)
        await asyncio.wait_for(self.router_transport.entered.wait(), 2)
        stop.set()
        await asyncio.wait_for(loop, 2)
        self.assertEqual(len(self.router_host.runtime.records.active), 1)
        self.router_transport.release.set()
        await self.finish_runs(self.router_host)
        await self.router.tick()
        await self.finish_runs(self.host)

    async def test_continuous_loop_routes_and_dispatches_without_manual_ticks(self):
        self.receive()
        stop = asyncio.Event()
        stream = self.work_transport.post_stream

        async def observed(*args, **kwargs):
            stop.set()
            async for chunk in stream(*args, **kwargs):
                yield chunk

        with patch.object(self.work_transport, 'post_stream', new=observed):
            await asyncio.wait_for(self.router.serve(stop, interval=0.01), 5)
            await self.finish_runs(self.host)
        self.assertEqual(len(self.router_transport.requests), 1)
        self.assertEqual(len(self.work_transport.requests), 1)
        self.assertEqual(self.router.status()['inputs'], {'settled': 1})
