"""成员证据只授权直接委派者，并按运行隔离复用上下文中的历史工具。"""
import re
import hashlib
import json

from types import SimpleNamespace
import unittest

from harness.core.errors import ToolExecutionError
from harness.i18n import tr
from harness.session.store import SessionStore
from harness.session.events import AppendOp
from harness.subagent.evidence import build_member_evidence_tool


class MemberEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.session = SessionStore().create()
        self.state = SimpleNamespace(id='member-current', session_id=self.session.id,
            parent_session_id='parent', settled=True, session=self.session, status='completed')
        self.manager = SimpleNamespace(target_for=lambda _id, _sid: self.state,
            get=lambda identifier: self.state if identifier == self.state.id else None, contexts=None)
        self.tool = build_member_evidence_tool(self.manager)
        self.ctx = SimpleNamespace(session_id='parent')
        await self.add('old', 'member-old', '历史结果')
        await self.add('new', 'member-current', '当前原始证据，包含关键事实')
        await self.add('failed', 'member-current', '访问失败', error=True)

    async def add(self, call, run, text, error=False):
        await self.session.append('tool/call', {'call_id': call, 'name': 'web_fetch',
            'run_id': run, 'status': 'running', 'turn': 1})
        await self.session.append('tool/result', {'call_id': call, 'name': 'web_fetch', 'turn': 1,
            'results': [{'tool_use_id': call, 'content': text, 'is_error': error}]}, surface_op=AppendOp())

    def read(self, **args):
        return self.tool.execute({'agent_id': 'member-current', **args}, self.ctx)

    def test_inventory_excludes_other_runs_and_preserves_failures(self):
        result = self.read()
        self.assertEqual([row['call_id'] for row in result['tools']], ['new', 'failed'])
        self.assertEqual([row['is_error'] for row in result['tools']], [False, True])
        self.assertEqual(result['agent_id'], self.state.id)
        self.assertEqual(result['context_session_id'], self.session.id)

    def test_exact_result_and_literal_query_use_original_reader(self):
        result = self.read(call_id='new', query='关键事实')['result']
        self.assertIn('关键事实', result['text'])
        self.assertEqual(result['name'], 'web_fetch')
        self.assertEqual(len(result['sha256']), 64)
        self.assertFalse(self.read(call_id='new')['call']['input_available'])
        with self.assertRaises(ToolExecutionError):
            self.read(call_id='old')

    def test_peers_running_and_missing_context_are_denied(self):
        self.state.parent_session_id = 'other-parent'
        with self.assertRaises(ToolExecutionError):
            self.read()
        self.state.parent_session_id = 'parent'
        self.state.settled = False
        with self.assertRaises(ToolExecutionError):
            self.read()
        self.state.settled = True
        self.state.session = None
        with self.assertRaises(ToolExecutionError):
            self.read()

    def test_guessed_call_ids_are_denied_with_listing_instruction(self):
        for call_id in ('list', '*', 'invented'):
            with self.subTest(call_id=call_id), self.assertRaisesRegex(ToolExecutionError, re.escape(tr('member.evidence.call'))):
                self.read(call_id=call_id)

    def test_persisted_context_can_be_read_without_reusing_live_agent(self):
        self.state.session = None
        self.manager.contexts = SimpleNamespace(get=lambda sid: self.session if sid == self.session.id else None)
        self.assertIn('关键事实', self.read(call_id='new')['result']['text'])

    def test_unknown_agent_is_rejected_before_context_access(self):
        def missing(_agent, _session):
            raise KeyError('not-visible')
        self.manager.target_for = missing
        self.manager.get = lambda _id: None
        with self.assertRaisesRegex(ToolExecutionError, re.escape(tr('member.evidence.unavailable'))):
            self.read()

    def test_original_parent_can_read_settled_evidence_after_scope_changes(self):
        def different_scope(_agent, _session):
            raise KeyError('old-team-scope')
        self.manager.target_for = different_scope
        self.state.parent_run_id = 'original-parent-run'
        self.state.scope = 'prompt:original-parent-run'
        result = self.read(call_id='new')
        self.assertEqual(result['agent_id'], self.state.id)
        self.assertEqual(result['parent_run_id'], 'original-parent-run')
        self.assertEqual(result['run_scope'], 'prompt:original-parent-run')
        self.assertIn('关键事实', result['result']['text'])
        self.state.session = None
        self.manager.contexts = SimpleNamespace(get=lambda sid: self.session if sid == self.session.id else None)
        self.assertIn('关键事实', self.read(call_id='new')['result']['text'])
        self.state.parent_session_id = 'another-parent'
        with self.assertRaises(ToolExecutionError):
            self.read(call_id='new')
        self.state.parent_session_id = 'parent'
        self.state.settled = False
        with self.assertRaises(ToolExecutionError):
            self.read(call_id='new')

    async def test_reused_call_id_cannot_relabel_previous_run_evidence(self):
        await self.add('reused', 'member-old', '旧运行独有结果')
        await self.add('reused', 'member-current', '本轮独有结果')
        with self.assertRaisesRegex(ToolExecutionError, re.escape(tr('member.evidence.ambiguous'))):
            self.read()
        with self.assertRaisesRegex(ToolExecutionError, re.escape(tr('member.evidence.ambiguous'))):
            self.read(call_id='reused')
        self.assertIn('关键事实', self.read(call_id='new')['result']['text'])

    async def test_repeated_call_id_within_one_run_is_also_ambiguous(self):
        await self.add('duplicate', 'member-current', '第一份结果')
        await self.add('duplicate', 'member-current', '第二份结果')
        with self.assertRaisesRegex(ToolExecutionError, re.escape(tr('member.evidence.ambiguous'))):
            self.read()
        with self.assertRaisesRegex(ToolExecutionError, re.escape(tr('member.evidence.ambiguous'))):
            self.read(call_id='duplicate')

    async def test_inventory_cursor_keeps_all_results_without_duplicates(self):
        for index in range(25):
            await self.add(f'call-{index}', 'member-current', f'原文 {index}')
        first = self.read()
        self.assertEqual(len(first['tools']), 20)
        self.assertTrue(first['has_more'])
        second = self.read(after=first['next_after'])
        self.assertFalse(second['has_more'])
        ids = [row['call_id'] for row in first['tools'] + second['tools']]
        self.assertEqual(len(ids), 27)
        self.assertEqual(len(set(ids)), 27)

    async def test_original_call_inputs_are_bound_to_run_and_explicitly_bounded(self):
        await self.session.append('tool/call', {'call_id': 'file', 'name': 'read',
            'input': {'path': '/workspace/design.md'}, 'effect': 'read',
            'run_id': 'member-current', 'status': 'running', 'turn': 1})
        await self.session.append('tool/result', {'call_id': 'file', 'name': 'read', 'turn': 1,
            'results': [{'tool_use_id': 'file', 'content': '设计原文', 'is_error': False}]}, surface_op=AppendOp())
        result = self.read(call_id='file')
        call = result['call']
        self.assertTrue(call['input_available'])
        self.assertEqual(call['name'], 'read')
        self.assertEqual(call['effect'], 'read')
        self.assertIn('/workspace/design.md', call['input_json'])
        self.assertFalse(call['input_truncated'])
        self.assertEqual(len(call['input_sha256']), 64)
        self.assertEqual(result['result']['text'], '设计原文')
        await self.session.append('tool/call', {'call_id': 'large', 'name': 'read',
            'input': {'path': 'x' * 5000}, 'run_id': 'member-current', 'status': 'running', 'turn': 1})
        await self.session.append('tool/result', {'call_id': 'large', 'name': 'read', 'turn': 1,
            'results': [{'tool_use_id': 'large', 'content': '失败', 'is_error': True}]}, surface_op=AppendOp())
        large = self.read(call_id='large')['call']
        self.assertTrue(large['input_truncated'])
        self.assertEqual(len(large['input_json']), 4096)
        self.assertGreater(large['input_total_chars'], 4096)
        self.assertEqual(large['effect'], 'unknown')
        expected = json.dumps({'path': 'x' * 5000}, ensure_ascii=False, sort_keys=True)
        self.assertEqual(large['input_sha256'], hashlib.sha256(expected.encode('utf-8')).hexdigest())
        with self.assertRaises(ToolExecutionError):
            self.read(call_id='old')

    async def test_interrupted_tool_without_result_is_visible_as_unknown(self):
        self.state.status = 'interrupted'
        await self.session.append('tool/call', {'call_id': 'unfinished', 'name': 'execute',
            'input': {'command': 'background-job'}, 'run_id': 'member-current', 'status': 'running', 'turn': 1})
        await self.session.append('tool/call', {'call_id': 'unfinished', 'name': 'execute',
            'status': 'done', 'turn': 1})
        await self.session.append('tool/call', {'call_id': 'other-unfinished', 'name': 'read',
            'run_id': 'member-old', 'status': 'running', 'turn': 1})
        inventory = self.read()['tools']
        self.assertNotIn('other-unfinished', [row['call_id'] for row in inventory])
        row = next(row for row in inventory if row['call_id'] == 'unfinished')
        self.assertEqual(row['name'], 'execute')
        self.assertIsNone(row['result_seq'])
        self.assertIsNone(row['is_error'])
        self.assertFalse(row['result_available'])
        self.assertEqual(row['evidence_seq'], row['call_seq'])
        with self.assertRaises(ToolExecutionError) as caught:
            self.read(call_id='unfinished')
        self.assertEqual(caught.exception.code, 'tool/execution')
        self.assertEqual(caught.exception.message, tr('tool.result.55ba8a63e2ec'))

    async def test_missing_results_are_paginated_without_hiding_completed_calls(self):
        for index in range(25):
            await self.session.append('tool/call', {'call_id': f'unfinished-{index}', 'name': 'read',
                'run_id': 'member-current', 'status': 'running', 'turn': 1})
        await self.add('last-result', 'member-current', '最后完成')
        first = self.read()
        second = self.read(after=first['next_after'])
        rows = first['tools'] + second['tools']
        self.assertEqual(len(rows), 28)
        self.assertEqual(len({row['call_id'] for row in rows}), 28)
        self.assertEqual(sum(not row['result_available'] for row in rows), 25)
        self.assertIn('last-result', [row['call_id'] for row in rows])
        self.assertFalse(second['has_more'])
