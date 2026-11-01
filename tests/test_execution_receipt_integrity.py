"""已完成幂等回执损坏时保留原件，绝不重复执行副作用。"""
import json
import tempfile
import unittest
from pathlib import Path

from harness.core.language import current_language
from harness.execution.config import ExecutionConfig, ProjectConfig
from harness.execution.runtime import ExecutionRuntime
from harness.tools.registry import ToolDefinition, ToolResult
from harness.llm.types import TextBlock
from harness.i18n import tr


class ExecutionReceiptIntegrityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        project = root / 'project'
        project.mkdir()
        self.runtime = ExecutionRuntime(ExecutionConfig(root / 'state', {'project': ProjectConfig(project, writable=True)}))
        self.addAsyncCleanup(self.runtime.close)
        self.sid = self.runtime.store.open_session('local', 'project', 'fixture', str(project))['session_id']
        self.count = 0
        self.failed = False

        def execute(args, ctx):
            self.count += 1
            if self.failed:
                return ToolResult(content=[TextBlock(text='外部拒绝原文')], is_error=True, data={'error': '外部拒绝原文'})
            return {'count': self.count, 'text': '用户原文'}

        self.runtime.registry.register(ToolDefinition({'name': 'receipt_probe', 'parameters': {
            'type': 'object', 'properties': {'request_id': {'type': 'string'}, 'session_id': {'type': 'string'}},
            'required': ['request_id', 'session_id']}}, execute, effect='workspace_write'))
        self.args = {'request_id': 'same', 'session_id': self.sid}

    async def call(self):
        return await self.runtime.call('local', 'receipt_probe', self.args)

    async def test_malformed_finished_receipts_do_not_reexecute_or_append_events(self):
        original = await self.call()
        self.assertFalse(original['is_error'])
        event_count = self.runtime.store.db.execute('SELECT count(*) FROM events').fetchone()[0]
        cases = ['null', '{', '[]', 'false', json.dumps({})]
        for change in ({'request_id': 'other'}, {'session_id': 'other'}, {'call_id': ''},
                       {'is_error': 'false'}, {'status': 'running'}, {'is_error': True}, {'extra': 1}):
            cases.append(json.dumps({**original, **change}))
        for raw in cases:
            with self.runtime.store.db:
                self.runtime.store.db.execute('UPDATE calls SET result=? WHERE request_id=?', (raw, 'same'))
            for language in ('en', 'zh-CN', 'en'):
                with self.subTest(raw=raw, language=language), current_language.set(language):
                    result = await self.call()
                    self.assertTrue(result['is_error'])
                    self.assertEqual(result['data']['error']['code'], 'execution/store-invalid')
                    self.assertEqual(result['data']['error']['message'], tr('execution.store.invalid_record'))
                    self.assertEqual(self.count, 1)
                    self.assertEqual(self.runtime.store.db.execute('SELECT count(*) FROM events').fetchone()[0], event_count)
                    row = self.runtime.store.db.execute('SELECT status,result FROM calls WHERE request_id=?', ('same',)).fetchone()
                    self.assertEqual((row['status'], row['result']), ('finished', raw))

    async def test_valid_success_and_rejection_replay_exact_original_receipt_once(self):
        for failed in (False, True):
            self.failed = failed
            self.args['request_id'] = 'failed' if failed else 'success'
            original = await self.call()
            count = self.count
            for language in ('en', 'zh-CN', 'en'):
                with current_language.set(language):
                    self.assertEqual(await self.call(), original)
                    self.assertEqual(self.count, count)
            self.assertEqual(original['is_error'], failed)

    async def test_unknown_running_and_fingerprint_conflicts_keep_no_replay_boundary(self):
        await self.call()
        for status in ('running', 'outcome_unknown'):
            with self.runtime.store.db:
                self.runtime.store.db.execute('UPDATE calls SET status=? WHERE request_id=?', (status, 'same'))
            result = await self.call()
            self.assertEqual(result['data']['error']['code'], 'execution/outcome-unknown')
            self.assertEqual(self.count, 1)
        with self.runtime.store.db:
            self.runtime.store.db.execute("UPDATE calls SET status='finished',fingerprint='other' WHERE request_id='same'")
        result = await self.call()
        self.assertEqual(result['data']['error']['code'], 'execution/request-conflict')
        self.assertEqual(self.count, 1)
