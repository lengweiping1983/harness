"""结束提交失败不能生成成功回执，也不能重放模型或改写原始日志。"""
import asyncio
import json
import tempfile
import unittest
from contextlib import nullcontext
from dataclasses import fields
from pathlib import Path
from unittest.mock import patch

from harness import Harness, HarnessConfig
from harness.core.errors import HarnessError, PersistenceError
from harness.i18n import tr
from harness.core.language import current_language
from harness.session.events import TurnEndReason
from harness.session.store import SessionStore
from harness.tools.registry import ToolDefinition
from tests.web_fixture import FixtureTransport
from tests.agent_configuration_fixture import RecordingTransport


class WriteThenAnswerTransport(RecordingTransport):
    async def post_stream(self, url, *, headers, body, timeout):
        self.requests.append(json.loads(body))
        if len(self.requests) > 1:
            async for chunk in FixtureTransport.post_stream(self, url, headers=headers, body=body, timeout=timeout):
                yield chunk
            return
        chunks = [
            {'choices': [{'delta': {'tool_calls': [{'index': 0, 'id': 'fixture-write',
                'function': {'name': 'fixture_write', 'arguments': '{}'}}]}, 'finish_reason': None}]},
            {'choices': [{'delta': {}, 'finish_reason': 'tool_calls'}]},
        ]
        for chunk in chunks:
            yield ('data: ' + json.dumps(chunk) + '\n\n').encode()
        yield b'data: [DONE]\n\n'


class TurnEndCommitTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_completion_error_and_cancellation_stay_failed_and_preserve_originals(self):
        for language in ('en', 'zh-CN'):
            for queued in (False, True):
                for branch in ('completed', 'error', 'cancelled'):
                    for written in (False, True):
                        with self.subTest(language=language, queued=queued, branch=branch, written=written):
                            await self.check_failure(language, queued, branch, written)

    async def check_failure(self, language, queued, branch, written):
        with tempfile.TemporaryDirectory() as directory, current_language.set(language):
            root = Path(directory)
            flags = {item.name: False for item in fields(HarnessConfig) if item.name.startswith('enable_')}
            transport = RecordingTransport()
            app = Harness(HarnessConfig(api_key='fixture-only', home=root / 'state', cwd=root,
                language=language, model='fixture-model', max_steps=2, max_retries=0, **flags), transport=transport)
            try:
                session = app.create_session()
                agent = app._agent_for(session)
                original_commit = session.commit_event
                log = app.sessions._dir(session.id) / 'events.jsonl'
                ends, states, attempts, originals = [], [], [], []
                app.bus.on('turn/end', lambda payload: ends.append(dict(payload)))
                app.bus.on('run/state', lambda payload: states.append(dict(payload)))

                def fail_end(event):
                    if event.type != 'turn/end':
                        return original_commit(event)
                    attempts.append(event)
                    if written:
                        original_commit(event)
                    originals.append(log.read_bytes())
                    raise OSError('private fixture disk failure')

                session.commit_event = fail_end
                failure = asyncio.CancelledError() if branch == 'cancelled' else HarnessError('Original failure', code='fixture/error')
                model = nullcontext() if branch == 'completed' else patch.object(agent, '_stream_once', side_effect=failure)
                with model:
                    if queued:
                        record = app.runtime.submit(session.id, 'Original task', key='fixture-request')
                        await record.task
                        self.assertIs(app.runtime.submission(session.id, 'fixture-request'), record)
                    else:
                        with self.assertRaises(PersistenceError) as caught:
                            await app.run_async(session, 'Original task')
                        self.assertEqual(caught.exception.message, tr('python.session.2daa27c516d2'))
                        record = next(iter(app.runtime.records.values()))
                self.assertEqual(len(attempts), 1)
                self.assertEqual(len(transport.requests), 1 if branch == 'completed' else 0)
                self.assertEqual(session.open_turn, 1)
                self.assertTrue(session.persistence_error)
                self.assertEqual(agent.status.status, 'error')
                self.assertFalse(agent.accepting_steering)
                self.assertEqual(record.status, 'failed')
                self.assertIsNone(record.result)
                self.assertEqual(record.error['code'], 'session/persistence')
                self.assertNotIn('private fixture', record.error['message'])
                self.assertEqual(len(ends), 1)
                self.assertEqual(ends[0]['reason'], 'error')
                self.assertIs(ends[0]['committed'], False)
                self.assertEqual(ends[0]['code'], record.error['code'])
                self.assertEqual(ends[0]['message'], record.error['message'])
                self.assertEqual(ends[0]['details'], record.error['details'])
                self.assertEqual(ends[0]['error'], str(PersistenceError(tr('python.session.2daa27c516d2'), session_id=session.id)))
                self.assertEqual(states[-1]['status'], 'failed')
                self.assertIsNone(app.runtime.current(session.id))
                self.assertTrue(record.settled)
                self.assertEqual(log.read_bytes(), originals[0])
                with app.runtime.db() as db:
                    stored = db.execute('SELECT data FROM runs WHERE id=?', (record.id,)).fetchone()[0]
                self.assertEqual(app.runtime.storage.decode(stored, expected_id=record.id).status, 'failed')
                # 未确认落盘不等于确定未写入；回查只读取真实磁盘，不补结束事件。
                reopened = SessionStore(app.sessions.root).get(session.id)
                self.assertEqual(reopened.open_turn, None if written else 1)
                self.assertEqual(len([event for event in reopened.events if event.type == 'turn/end']), int(written))
                self.assertEqual(len([event for event in session.events if event.type == 'turn/end']), 0)
                with self.assertRaises(HarnessError):
                    await app.run_async(session, 'Must not execute')
                with self.assertRaises(HarnessError):
                    app.runtime.submit(session.id, 'Must not enqueue', key='next-request')
                self.assertEqual(len(transport.requests), 1 if branch == 'completed' else 0)
                self.assertEqual(log.read_bytes(), originals[0])
                self.assertEqual(json.loads(stored)['status'], 'failed')
            finally:
                if session.persistence_error:
                    with self.assertRaises(PersistenceError) as close_error:
                        await app.close()
                    self.assertEqual(close_error.exception.code, 'session/persistence')
                    self.assertTrue(app.runtime.closed)
                    self.assertEqual(len(attempts), 1)
                    self.assertEqual(log.read_bytes(), originals[0])
                else:
                    await app.close()

    async def test_success_commits_once_and_idempotent_end_does_not_append(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            flags = {item.name: False for item in fields(HarnessConfig) if item.name.startswith('enable_')}
            app = Harness(HarnessConfig(api_key='fixture-only', home=root / 'state', cwd=root,
                model='fixture-model', max_steps=2, max_retries=0, **flags), transport=RecordingTransport())
            try:
                session = app.create_session()
                result = await app.run_async(session, 'Original task')
                self.assertEqual(result.reason, 'completed')
                self.assertIsNone(result.error)
                self.assertIsNone(session.open_turn)
                end = session.latest('turn/end')
                self.assertEqual(end.data['reason']['kind'], 'completed')
                self.assertEqual(session.event_at(end.data['final_response_seq']).type, 'assistant/message')
                count = session.event_count
                await app._agent_for(session)._end_turn_safely(session, TurnEndReason(kind='completed'))
                self.assertEqual(session.event_count, count)
                self.assertEqual(next(iter(app.runtime.records.values())).status, 'completed')
            finally:
                await app.close()

    async def test_written_tool_and_original_evidence_are_not_replayed_after_end_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            flags = {item.name: False for item in fields(HarnessConfig) if item.name.startswith('enable_')}
            transport = WriteThenAnswerTransport()
            app = Harness(HarnessConfig(api_key='fixture-only', home=root / 'state', cwd=root,
                model='fixture-model', max_steps=3, max_retries=0, **flags), transport=transport)
            calls = []
            original = '原始交付😀'.encode()
            target = root / 'delivery.txt'
            def write(args, context):
                calls.append(context.call_id)
                target.write_bytes(original)
                return 'Original write succeeded'
            app.tools.register(ToolDefinition({'name': 'fixture_write'}, write, effect='workspace_write'))
            try:
                session = app.create_session()
                commit = session.commit_event
                def fail_end(event):
                    if event.type == 'turn/end':
                        raise OSError('fixture disk full')
                    return commit(event)
                session.commit_event = fail_end
                record = app.runtime.submit(session.id, 'Write once', key='write-once')
                await record.task
                self.assertEqual(record.status, 'failed')
                self.assertEqual(record.error['code'], 'session/persistence')
                self.assertEqual(len(calls), 1)
                self.assertEqual(len(transport.requests), 2)
                self.assertEqual(target.read_bytes(), original)
                result = session.latest('tool/result')
                self.assertIs(result.data['results'][0]['is_error'], False)
                self.assertEqual(result.data['call_id'], calls[0])
                log = app.sessions._dir(session.id) / 'events.jsonl'
                before = log.read_bytes()
                self.assertIs(app.runtime.submission(session.id, 'write-once'), record)
                with self.assertRaises(HarnessError):
                    app.runtime.submit(session.id, 'Write once', key='new-key')
                self.assertEqual(len(calls), 1)
                self.assertEqual(target.read_bytes(), original)
                self.assertEqual(log.read_bytes(), before)
            finally:
                if session.persistence_error:
                    before_close = (app.sessions._dir(session.id) / 'events.jsonl').read_bytes()
                    with self.assertRaises(PersistenceError) as close_error:
                        await app.close()
                    self.assertEqual(close_error.exception.code, 'session/persistence')
                    self.assertTrue(app.runtime.closed)
                    self.assertEqual(len(calls), 1)
                    self.assertEqual(target.read_bytes(), original)
                    self.assertEqual((app.sessions._dir(session.id) / 'events.jsonl').read_bytes(), before_close)
                else:
                    await app.close()
