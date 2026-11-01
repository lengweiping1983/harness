"""真实执行器中的内部结果消费、连续 Router 整理和持久候选回归。"""
import asyncio
import json
import sqlite3
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path
from contextlib import closing
from unittest.mock import patch

from harness import Harness, HarnessConfig
from harness.app.superagent_inbox import SuperagentInbox
from harness.app.superagent_dispatch import SuperagentDispatcher
from harness.app.superagent_router import SuperagentRouter
from harness.app.superagent_results import SuperagentResults
from harness.core.errors import HarnessError
from tests.agent_configuration_fixture import RecordingTransport


class ResultTransport(RecordingTransport):
    def __init__(self):
        super().__init__()
        self.output = '{"reply":"A patch is available for review."}'
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.release.set()

    async def post_stream(self, url, *, headers, body, timeout):
        self.requests.append(json.loads(body))
        self.entered.set()
        await self.release.wait()
        yield ('data: ' + json.dumps({'choices': [{'delta': {'content': self.output}, 'finish_reason': 'stop'}]}) + '\n\n').encode('utf-8')
        yield b'data: [DONE]\n\n'


class SuperagentResultsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.transport, self.result_transport = RecordingTransport(), ResultTransport()
        self.host = self.new_host('workers', self.transport)
        self.router_host = self.new_host('router', self.result_transport)
        self.worker = self.host.create_session()
        self.router_session = self.router_host.create_session()
        self.inbox = SuperagentInbox(self.root / 'inbox.sqlite3', instance_id='team')
        self.inbox.configure_source('github', mode='requests', actors=['alice'], threads=['issue/1'])
        self.inbox.register_task('fix', session_id=self.worker.id)
        self.inbox.subscribe('fix', source_id='github', thread_id='issue/1')
        self.dispatcher = SuperagentDispatcher(self.host, self.inbox)
        self.router = self.new_router()
        self.results = SuperagentResults(self.router)

    def new_host(self, name, transport):
        flags = {item.name: False for item in fields(HarnessConfig) if item.name.startswith('enable_')}
        host = Harness(HarnessConfig(api_key='fixture-only', home=self.root / name, cwd=self.root,
            model='fixture-model', language='en', max_retries=0, **flags), transport=transport,
            codex_gateway_home=self.root / (name + '-gateway'))
        self.addAsyncCleanup(host.close)
        return host

    def new_router(self):
        return SuperagentRouter(self.dispatcher, self.router_host, session_id=self.router_session.id, workers={'fix': 'Fix regressions'})

    async def submit(self, identity='one'):
        self.inbox.receive(source_id='github', event_id=identity, actor_id='alice', text='/goal original source',
            thread_ids=['issue/1'], human=True, explicit=True)
        delivery = await self.dispatcher.dispatch(source_id='github', event_id=identity)
        await self.finish(self.host)
        return delivery

    async def finish(self, host):
        await asyncio.wait_for(asyncio.gather(*(record.task for record in tuple(host.runtime.records.active.values())
                                               if record.task is not None)), 5)

    async def complete(self):
        await self.router.tick()
        await self.finish(self.router_host)
        return await self.router.tick()

    async def test_terminal_worker_report_reaches_same_router_and_creates_local_draft(self):
        delivery = await self.submit()
        status = await self.complete()
        self.assertEqual(status['results'], {'draft': 1})
        drafts = self.results.drafts()
        self.assertEqual(len(drafts), 1)
        draft = drafts[0]
        self.assertEqual(draft['worker_run'], delivery['deliveries'][0]['run_id'])
        self.assertEqual(draft['reply'], 'A patch is available for review.')
        self.assertEqual(draft['state'], 'draft')
        self.assertEqual(len(self.result_transport.requests), 1)
        self.assertFalse(self.result_transport.requests[0].get('tools'))
        prompt = self.router_host.runtime.get(draft['router_run']).text
        frame = json.loads(prompt.split('\n\n', 1)[1])
        self.assertEqual(frame['worker_status'], 'completed')
        self.assertEqual(frame['worker_output'], 'fixture response verified')
        self.assertEqual(frame['request'], '/goal original source')
        await self.router.tick()
        self.assertEqual(len(self.result_transport.requests), 1)

    async def test_lost_router_return_only_reconciles_original_submission(self):
        await self.submit()
        submit = self.router_host.runtime.submit
        def lost(*args, **kwargs):
            submit(*args, **kwargs)
            raise OSError('result return lost')
        with patch.object(self.router_host.runtime, 'submit', side_effect=lost) as attempted:
            with self.assertRaisesRegex(OSError, 'return lost'):
                await self.router.tick()
            await self.finish(self.router_host)
            await self.router.tick()
            self.assertEqual(attempted.call_count, 1)
        self.assertEqual(self.results.status(), {'draft': 1})

    async def test_unknown_submission_is_retained_and_never_replayed(self):
        await self.submit()
        with patch.object(self.router_host.runtime, 'submit', side_effect=OSError('unknown result')) as attempted:
            with self.assertRaises(OSError):
                await self.router.tick()
            await self.router.tick()
            self.assertEqual(attempted.call_count, 1)
        self.assertEqual(self.results.status(), {'unknown': 1})
        self.assertEqual(self.results.drafts(), [])

    async def test_source_revocation_during_inference_suppresses_draft(self):
        await self.submit()
        self.result_transport.release.clear()
        await self.router.tick()
        await asyncio.wait_for(self.result_transport.entered.wait(), 2)
        self.inbox.configure_source('github', mode='ignore', actors=['alice'], threads=['issue/1'], expected_revision=1)
        self.result_transport.release.set()
        await self.finish(self.router_host)
        await self.router.tick()
        self.assertEqual(self.results.status(), {'suppressed': 1})
        self.assertEqual(self.results.drafts(), [])

    async def test_revoked_source_before_collection_avoids_model(self):
        await self.submit()
        self.inbox.configure_source('github', mode='ignore', actors=['alice'], threads=['issue/1'], expected_revision=1)
        await self.router.tick()
        self.assertEqual(self.results.status(), {'suppressed': 1})
        self.assertEqual(self.result_transport.requests, [])

    async def test_restart_keeps_original_candidate_without_rerunning_model(self):
        await self.submit()
        await self.complete()
        original = self.results.drafts()
        await self.router_host.close()
        self.router_host = self.new_host('router', self.result_transport)
        self.router = self.new_router()
        self.results = SuperagentResults(self.router)
        with patch.object(self.router_host.runtime, 'submit', side_effect=AssertionError('结果整理被重放')):
            await self.router.tick()
        self.assertEqual(self.results.drafts(), original)

    async def test_reply_shape_length_and_silence_are_enforced(self):
        for index, output in enumerate(('{"reply":null}', '{"reply":"ok","sent":true}',
                                         json.dumps({'reply': 'word ' * 101}), '{"reply":42}',
                                         '{"reply":null,"reply":"ambiguous"}')):
            with self.subTest(output=output):
                self.result_transport.output = output
                await self.submit(str(index))
                await self.complete()
        self.assertEqual(self.results.status(), {'silent': 1, 'rejected': 4})
        self.assertEqual(self.results.drafts(), [])

    async def test_actual_result_prompt_tracks_english_chinese_english_on_one_router(self):
        prefixes = []
        for index, language in enumerate(('en','zh-CN','en')):
            self.router_host.config.language = language
            await self.submit(str(index))
            await self.complete()
            draft = self.results.drafts()[-1]
            prefixes.append(self.router_host.runtime.get(draft['router_run']).text.split('\n\n', 1)[0])
        self.assertEqual(prefixes[0], prefixes[2])
        self.assertIn('内部 Worker 报告', prefixes[1])
        self.assertNotEqual(prefixes[0], prefixes[1])

    async def test_corrupt_persisted_result_prompt_is_preserved_and_never_submitted(self):
        await self.submit()
        self.results._collect()
        self.results._prepare()
        with closing(sqlite3.connect(self.results.path)) as db,db:
            db.execute("UPDATE reports SET payload='/runner should-not-run'")
        with patch.object(self.router_host.runtime,'submit',side_effect=AssertionError('损坏提示被提交')):
            with self.assertRaises(HarnessError) as caught:
                await self.router.tick()
        self.assertEqual(caught.exception.code,'superagent/corrupt')
        with closing(sqlite3.connect(self.results.path)) as db:
            self.assertEqual(db.execute('SELECT payload FROM reports').fetchone()[0],'/runner should-not-run')

    async def test_finished_worker_can_report_while_another_worker_is_still_running(self):
        second = self.host.create_session()
        self.inbox.register_task('second',session_id=second.id)
        self.inbox.subscribe('second',source_id='github',thread_id='issue/1')
        entered, release = asyncio.Event(),asyncio.Event()
        original = self.transport.post_stream
        count = 0
        async def gated(*args,**kwargs):
            nonlocal count
            count += 1
            slow = count == 1
            if slow:
                entered.set()
                await release.wait()
            async for chunk in original(*args,**kwargs):
                yield chunk
        with patch.object(self.transport,'post_stream',new=gated):
            self.inbox.receive(source_id='github',event_id='two-workers',actor_id='alice',text='inspect',
                thread_ids=['issue/1'],human=True,explicit=True)
            delivery = await self.dispatcher.dispatch(source_id='github',event_id='two-workers')
            try:
                await asyncio.wait_for(entered.wait(),2)
                tasks = [self.host.runtime.get(item['run_id']).task for item in delivery['deliveries']]
                done,pending = await asyncio.wait(tasks,timeout=2,return_when=asyncio.FIRST_COMPLETED)
                self.assertEqual(len(done),1)
                self.assertEqual(len(pending),1)
                await asyncio.gather(*done)
                await self.complete()
                self.assertEqual(self.results.status(),{'waiting':1,'draft':1})
                self.assertEqual(len(self.host.runtime.records.active),1)
            finally:
                release.set()
                await self.finish(self.host)
