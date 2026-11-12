"""复现无效证据后的首次建单恢复，保留证据与快照的严格边界。"""
import json
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path

from harness import Harness, HarnessConfig

from harness.app import plans
from harness.app.plan_mode import PlanModeController
from harness.llm.types import ToolUseBlock
from harness.session.events import AppendOp
from harness.session.store import SessionStore
from harness.tools.builtin.todo_tools import TodoStore, build_todo_tools
from harness.tools.registry import ToolRegistry, ToolRunContext


class TodoEvidenceRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.sessions = SessionStore(directory.name)
        self.session = self.sessions.create()
        self.registry = ToolRegistry()
        for tool in build_todo_tools(TodoStore(session_of=self.sessions.get)):
            self.registry.register(tool)
        self.ctx = ToolRunContext(call_id='recovery', session_id=self.session.id,
            cwd=directory.name, turn=1, step=1,
            services={'session': self.session, 'plan_controller': PlanModeController(self.sessions)})
        self.plan_id = await plans.submit(self.session, '# 验收计划\n读取并核对结果。', 'review')
        await plans.decide(self.session, self.plan_id, 'approved')

    async def call(self, name, args):
        return await self.registry.execute_call(ToolUseBlock(id='recovery', name=name, input=args), self.ctx)

    async def result(self, key='actual-read', *, error=False, pruned=False, session=None):
        return await (session or self.session).append('tool/result', {'call_id': key, 'name': 'read',
            'pruned': pruned, 'results': [{'tool_use_id': key, 'is_error': error, 'content': '核对到实际文件内容'}]}, surface_op=AppendOp())

    def payload(self, result):
        return json.loads(result.content[0].text)

    async def test_invalid_call_id_recovers_first_creation_using_only_token_and_numeric_evidence(self):
        observed = await self.result()
        bad = await self.call('todo_write', {'plan_id': self.plan_id, 'todos': [
            {'content': '读取核对', 'status': 'completed', 'evidence_call_ids': ['call_invented']}]})
        self.assertTrue(bad.is_error)
        payload = self.payload(bad)
        self.assertEqual(payload['code'], 'INVALID_EVIDENCE')
        self.assertFalse(payload['updated'])
        self.assertEqual(payload['invalid_ids'], ['call_invented'])
        self.assertEqual(payload['evidence_candidates'][0]['source_seq'], observed.seq)
        self.assertIsNone(self.session.latest('todo/write'))
        read = await self.call(payload['recovery']['tool'], payload['recovery']['arguments'])
        self.assertFalse(read.is_error, read.content)
        self.assertEqual(read.data['source_seq'], -1)
        self.assertIn('todo_write', payload['recovery']['instruction'])
        arguments = read.data['write_arguments']
        self.assertEqual(set(arguments), {'snapshot_token'})
        saved = await self.call('todo_write', {**arguments, 'todos': [
            {'content': '读取核对', 'status': 'completed', 'evidence_seqs': [observed.seq]}]})
        self.assertFalse(saved.is_error, saved.content)
        restored = SessionStore(self.sessions.root).get(self.session.id).latest('todo/write')
        self.assertEqual(restored.data['plan_id'], self.plan_id)
        self.assertEqual(restored.data['todos'][0]['evidence'][0]['source_seq'], observed.seq)
        self.assertEqual(restored.data['todos'][0]['evidence'][0]['call_id'], 'actual-read')

    async def test_failed_pruned_and_foreign_evidence_never_commit(self):
        failed = await self.result('failed', error=True)
        pruned = await self.result('pruned', pruned=True)
        foreign = await self.result('foreign', session=self.sessions.create())
        for seq, code in [(failed.seq, 'FAILED_EVIDENCE'), (pruned.seq, 'FAILED_EVIDENCE'),
                          (foreign.seq, 'INVALID_EVIDENCE'),
                          (9999, 'INVALID_EVIDENCE')]:
            with self.subTest(seq=seq, code=code):
                result = await self.call('todo_write', {'todos': [
                    {'content': '不能伪造完成', 'status': 'completed', 'evidence_seqs': [seq]}]})
                self.assertTrue(result.is_error)
                self.assertEqual(self.payload(result)['code'], code)
                self.assertIsNone(self.session.latest('todo/write'))
        read = await self.call('todo_read', {})
        self.assertEqual(read.data['evidence_candidates'], [])

    async def test_sequence_disambiguates_reused_call_id_but_call_id_alone_still_fails(self):
        first = await self.result('reused')
        second = await self.result('reused')
        bad = await self.call('todo_write', {'todos': [
            {'content': '任务', 'status': 'completed', 'evidence_call_ids': ['reused']}]})
        self.assertEqual(self.payload(bad)['code'], 'INVALID_EVIDENCE')
        read = await self.call('todo_read', {})
        self.assertEqual([row['source_seq'] for row in read.data['evidence_candidates']], [second.seq, first.seq])
        good = await self.call('todo_write', {**read.data['write_arguments'], 'todos': [
            {'content': '任务', 'status': 'completed', 'evidence_seqs': [first.seq]}]})
        self.assertFalse(good.is_error, good.content)
        self.assertEqual(good.data[0].evidence[0]['source_seq'], first.seq)

    async def test_update_preserves_and_explicitly_clears_numeric_evidence(self):
        observed = await self.result()
        created = await self.call('todo_write', {'todos': [{'content': '任务', 'status': 'pending'}]})
        task = created.data[0].id
        updated = await self.call('todo_update', {**created.display['update_arguments'], 'updates': [
            {'id': task, 'status': 'completed', 'evidence_seqs': [observed.seq]}]})
        self.assertFalse(updated.is_error, updated.content)
        kept = await self.call('todo_update', {**updated.display['update_arguments'], 'updates': [
            {'id': task, 'content': '已核对任务'}]})
        self.assertEqual(kept.data[0].evidence[0]['source_seq'], observed.seq)
        cleared = await self.call('todo_update', {**kept.display['update_arguments'], 'updates': [
            {'id': task, 'evidence_seqs': []}]})
        self.assertFalse(cleared.is_error, cleared.content)
        self.assertEqual(cleared.data[0].evidence, ())

    async def test_mixed_reference_forms_are_rejected_atomically(self):
        observed = await self.result()
        result = await self.call('todo_write', {'todos': [
            {'content': '任务', 'status': 'completed', 'evidence_call_ids': ['actual-read'],
             'evidence_seqs': [observed.seq]}]})
        self.assertEqual(self.payload(result)['code'], 'INVALID_EVIDENCE')
        self.assertIsNone(self.session.latest('todo/write'))

    async def test_candidate_pagination_is_bounded_and_has_no_duplicates(self):
        expected = [await self.result('read-' + str(index)) for index in range(25)]
        first = await self.call('todo_read', {})
        self.assertEqual(len(first.data['evidence_candidates']), 20)
        second = await self.call('todo_read', {'evidence_before': first.data['next_evidence_before']})
        self.assertEqual(len(second.data['evidence_candidates']), 5)
        self.assertIsNone(second.data['next_evidence_before'])
        actual = [row['source_seq'] for page in (first.data, second.data) for row in page['evidence_candidates']]
        self.assertEqual(actual, [event.seq for event in reversed(expected)])

    async def test_wrong_redundant_plan_is_rejected_and_correct_token_still_recovers(self):
        read = await self.call('todo_read', {})
        token = read.data['write_arguments']['snapshot_token']
        bad = await self.call('todo_write', {'snapshot_token': token, 'plan_id': 'plan_' + token[-30:],
            'todos': [{'content': '任务', 'status': 'pending'}]})
        payload = self.payload(bad)
        self.assertEqual(payload['code'], 'UNKNOWN_PLAN')
        self.assertEqual(payload['current']['plan_id'], self.plan_id)
        reread = await self.call('todo_read', payload['recovery']['arguments'])
        saved = await self.call('todo_write', {**reread.data['write_arguments'],
            'todos': [{'content': '任务', 'status': 'pending'}]})
        self.assertFalse(saved.is_error, saved.content)

    async def test_real_agent_loop_recovers_before_bounded_initialization_stops(self):
        class RecoveryTransport:
            async def post_stream(transport, url, *, headers, body, timeout):
                """先提交错误证据，再按工具返回的最小恢复参数完成建单。"""
                messages = json.loads(body)['messages']
                index = max(i for i, message in enumerate(messages) if message['role'] == 'user')
                calls = [call for message in messages[index + 1:] for call in message.get('tool_calls', [])]
                name, arguments = None, {}
                if len(calls) == 0:
                    name, arguments = 'read', {'path': 'proof.txt'}
                elif len(calls) == 1:
                    name, arguments = 'todo_write', {'todos': [{'content': '读取核对', 'status': 'completed',
                                                              'evidence_call_ids': ['invented-id']}]}
                elif len(calls) == 2:
                    name = 'todo_read'
                elif len(calls) == 3:
                    text = next(str(message['content']) for message in reversed(messages) if message['role'] == 'tool')
                    page = json.JSONDecoder().raw_decode(text[text.index('{'):])[0]
                    name, arguments = 'todo_write', {**page['write_arguments'], 'todos': [
                        {'content': '读取核对', 'status': 'completed',
                         'evidence_seqs': [page['evidence_candidates'][0]['source_seq']]}]}
                delta = {'tool_calls': [{'index': 0, 'id': 'recovery-' + str(len(calls)), 'type': 'function',
                    'function': {'name': name, 'arguments': json.dumps(arguments)}}]} if name else {'content': '恢复并核对成功'}
                for row in [{'choices': [{'delta': {'role': 'assistant', **delta}, 'finish_reason': None}]},
                            {'choices': [{'delta': {}, 'finish_reason': 'tool_calls' if name else 'stop'}]}]:
                    yield ('data: ' + json.dumps(row) + '\n\n').encode()
                yield b'data: [DONE]\n\n'

        root = Path(self.sessions.root)
        (root / 'proof.txt').write_text('真实核对结果', encoding='utf-8')
        flags = {item.name: False for item in fields(HarnessConfig) if item.name.startswith('enable_')}
        flags.update(enable_fs=True, enable_todo=True, enable_plan_mode=True)
        app = Harness(HarnessConfig(api_key='fixture', model='fixture', home=root / 'app-state', cwd=root,
            language='zh-CN', max_steps=8, max_retries=0, **flags), transport=RecoveryTransport())
        self.addAsyncCleanup(app.close)
        session = app.create_session()
        plan = await plans.submit(session, '# 实施验收\n读取核对实际结果。', 'approval')
        await plans.decide(session, plan, 'approved')
        result = await app.run_async(session, '实施已批准计划，核对实际结果。')
        self.assertEqual(result.reason, 'completed')
        event = session.latest('todo/write')
        self.assertEqual(event.data['plan_id'], plan)
        reference = event.data['todos'][0]['evidence'][0]
        self.assertEqual(reference['name'], 'read')
        self.assertIn('真实核对结果', reference['summary'])
        self.assertEqual(len(session.events_of_type('todo/write')), 1)
        results = session.events_of_type('tool/result')
        self.assertEqual([event.data['name'] for event in results], ['read', 'todo_write', 'todo_read', 'todo_write'])
        self.assertFalse(results[-1].data['results'][0]['is_error'])
        notices = [event for event in session.events_of_type('note')
                   if event.data.get('kind') == 'todo_initialization_required']
        self.assertEqual(len(notices), 2)
        self.assertTrue(all(event.data['reason'] == 'approved_plan' and event.data['plan_id'] == plan
                            for event in notices))
        self.assertFalse(any(event.data.get('kind') == 'todo_initialization_reminder'
                             for event in session.events_of_type('note')))
