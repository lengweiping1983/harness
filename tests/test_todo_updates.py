"""验证局部任务更新能合并互不影响的修改，同时拒绝真实覆盖。"""
import asyncio
import json
import tempfile
import unittest

from harness.app import plans
from harness.llm.types import ToolUseBlock
from harness.session.events import AppendOp
from harness.session.store import SessionStore
from harness.tools.builtin.todo_tools import TodoStore, build_todo_tools
from harness.tools.registry import ToolRegistry, ToolRunContext


class TodoUpdateTests(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_new_id_returns_correct_tool_guidance_and_preserves_all_tasks(self):
        from harness.core.language import current_language
        from harness.i18n import tr

        before = self.session.latest('todo/write')
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                result = await self.call('todo_update', {
                    'snapshot_token': self.base['snapshot_token'],
                    'updates': [{'id': self.ids[0], 'status': 'completed'},
                                {'id': 'invented-new-task', 'content': '新任务', 'status': 'pending'}]})
                payload = self.payload(result)
                self.assertTrue(result.is_error)
                self.assertEqual(payload['code'], 'UNKNOWN_TASK_ID')
                self.assertEqual(payload['recovery']['instruction'], tr('todo.recovery.unknown_task'))
                schema = self.registry.get('todo_update').schema.resolve_description(language).to_dict()
                self.assertEqual(schema['parameters']['properties']['updates']['items']['properties']['id']['description'],
                                 tr('todo.update_task_id'))
                self.assertEqual(self.session.latest('todo/write').seq, before.seq)
        read = await self.call('todo_read', payload['recovery']['arguments'])
        snapshot = read.data
        result = await self.call('todo_write', {**snapshot['write_arguments'],
            'todos': [*snapshot['todos'], {'content': '新任务', 'status': 'pending'}]})
        self.assertFalse(result.is_error, result.content)
        rows = self.session.latest('todo/write').data['todos']
        self.assertEqual([row['id'] for row in rows[:8]], self.ids)
        self.assertEqual([row['status'] for row in rows[:8]], ['pending'] * 8)
        self.assertEqual(len(rows), 9)
        self.assertNotIn(rows[-1]['id'], self.ids)

    async def test_invalid_update_guidance_lists_all_mutable_fields_without_committing(self):
        from harness.core.language import current_language

        before = self.session.latest('todo/write')
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                result = await self.call('todo_update', {
                    'snapshot_token': self.base['snapshot_token'],
                    'updates': [{'id': self.ids[0], 'status': 'completed'},
                                {'id': self.ids[0], 'status': 'pending'}]})
                self.assertTrue(result.is_error)
                schema = self.registry.get('todo_update').schema.resolve_description(language).to_dict()
                fields = schema['parameters']['properties']['updates']['items']['properties']
                for field in fields.keys() - {'id'}:
                    self.assertIn(field, result.content[0].text)
                self.assertEqual(self.session.latest('todo/write').seq, before.seq)
        accepted = await self.update(0, evidence_seqs=[])
        self.assertFalse(accepted.is_error, accepted.content)
        self.assertEqual(self.session.latest('todo/write').data['todos'][0]['status'], 'pending')

    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.sessions = SessionStore(self.directory.name)
        self.session = self.sessions.create()
        self.registry = ToolRegistry()
        for tool in build_todo_tools(TodoStore(session_of=self.sessions.get)):
            self.registry.register(tool)
        self.ctx = ToolRunContext(call_id='update-test', session_id=self.session.id,
            cwd=self.directory.name, turn=1, step=1, services={'session': self.session})
        self.created = await self.call('todo_write', {'todos': [
            {'content': f'任务 {index + 1}', 'status': 'pending'} for index in range(8)]})
        self.assertFalse(self.created.is_error, self.created.content)
        self.ids = [row.id for row in self.created.data]
        self.base = {key: self.created.display[key] for key in ('snapshot_token', 'source_seq', 'list_id')}

    async def call(self, name, args):
        return await self.registry.execute_call(ToolUseBlock(id='update-test', name=name, input=args), self.ctx)

    async def update(self, index, *, version=None, **fields):
        return await self.call('todo_update', {**(version or {'snapshot_token': self.base['snapshot_token']}),
            'updates': [{'id': self.ids[index], **fields}]})

    def payload(self, result):
        return json.loads(result.content[0].text)

    async def assert_disjoint_merge(self, version):
        first = await self.update(0, version=version, status='completed')
        self.assertFalse(first.is_error, first.content)
        second = await self.update(1, version=version, status='in_progress')
        self.assertFalse(second.is_error, second.content)
        self.assertEqual(second.display['merged_from_seq'], self.base['source_seq'])
        restored = SessionStore(self.directory.name).get(self.session.id).latest('todo/write')
        self.assertEqual([row['status'] for row in restored.data['todos'][:3]],
                         ['completed', 'in_progress', 'pending'])
        self.assertEqual(len(restored.data['todos']), 8)

    async def test_two_calls_from_one_model_response_can_share_snapshot(self):
        await self.assert_disjoint_merge({'snapshot_token': self.base['snapshot_token'],
                                         'expected_seq': self.base['source_seq']})

    async def test_disjoint_updates_with_only_snapshot_token(self):
        await self.assert_disjoint_merge({'snapshot_token': self.base['snapshot_token']})

    async def test_disjoint_updates_with_only_expected_sequence(self):
        await self.assert_disjoint_merge({'list_id': self.base['list_id'],
                                         'expected_seq': self.base['source_seq']})

    async def test_concurrent_disjoint_updates_preserve_both_changes(self):
        results = await asyncio.gather(self.update(0, status='completed'),
                                       self.update(1, status='in_progress'))
        self.assertTrue(all(not result.is_error for result in results), [r.content for r in results])
        self.assertEqual(len(self.session.events_of_type('todo/write')), 3)
        self.assertEqual([row['status'] for row in self.session.latest('todo/write').data['todos'][:2]],
                         ['completed', 'in_progress'])

    async def test_changed_target_status_is_not_overwritten(self):
        await self.update(0, status='completed')
        before = self.session.latest('todo/write')
        result = await self.update(0, status='in_progress')
        self.assertEqual(self.payload(result)['code'], 'VERSION_CONFLICT')
        self.assertEqual(self.session.latest('todo/write').seq, before.seq)
        self.assertEqual(self.session.latest('todo/write').data['todos'][0]['status'], 'completed')

    async def test_changed_target_content_cannot_receive_stale_completion(self):
        await self.update(0, content='范围已经变化的任务')
        result = await self.update(0, status='completed')
        self.assertEqual(self.payload(result)['code'], 'VERSION_CONFLICT')
        self.assertEqual(self.session.latest('todo/write').data['todos'][0]['status'], 'pending')

    async def test_changed_target_evidence_is_not_ignored(self):
        await self.session.append('tool/result', {'call_id': 'verify-task', 'name': 'read',
            'results': [{'tool_use_id': 'verify-task', 'is_error': False, 'content': '已核对文件'}]},
            surface_op=AppendOp())
        evidence = await self.update(0, evidence_call_ids=['verify-task'])
        self.assertFalse(evidence.is_error, evidence.content)
        result = await self.update(0, status='completed')
        self.assertEqual(self.payload(result)['code'], 'VERSION_CONFLICT')
        self.assertEqual(self.session.latest('todo/write').data['todos'][0]['evidence'][0]['call_id'], 'verify-task')

    async def test_conflict_in_one_target_rejects_entire_update_batch(self):
        await self.update(0, status='completed')
        before = self.session.latest('todo/write')
        result = await self.call('todo_update', {'snapshot_token': self.base['snapshot_token'],
            'updates': [{'id': self.ids[1], 'status': 'in_progress'},
                        {'id': self.ids[0], 'status': 'pending'}]})
        self.assertEqual(self.payload(result)['code'], 'VERSION_CONFLICT')
        self.assertEqual(self.session.latest('todo/write').seq, before.seq)
        self.assertEqual(self.session.latest('todo/write').data['todos'][1]['status'], 'pending')

    async def test_token_and_sequence_must_describe_the_same_snapshot(self):
        first = await self.update(0, status='completed')
        for version in (
                {'snapshot_token': self.base['snapshot_token'], 'expected_seq': first.display['source_seq']},
                {'snapshot_token': first.display['snapshot_token'], 'expected_seq': self.base['source_seq']}):
            with self.subTest(version=version):
                result = await self.update(1, version=version, status='in_progress')
                self.assertEqual(self.payload(result)['code'], 'VERSION_CONFLICT')
                self.assertEqual(self.session.latest('todo/write').seq, first.display['source_seq'])

    async def test_plan_reassociation_blocks_old_snapshot_updates(self):
        plan_id = await plans.submit(self.session, '# 已批准计划', 'update-plan')
        await plans.decide(self.session, plan_id, 'approved')
        bound = await self.call('todo_bind', {'list_id': self.base['list_id'], 'plan_id': plan_id,
            'snapshot_token': self.base['snapshot_token']})
        self.assertFalse(bound.is_error, bound.content)
        result = await self.update(1, status='in_progress')
        self.assertTrue(result.is_error)
        self.assertEqual(self.session.latest('todo/write').seq, bound.display['source_seq'])

    async def test_removed_task_is_not_recreated_by_stale_patch(self):
        rows = [{'id': row.id, 'content': row.content, 'status': row.status} for row in self.created.data[1:]]
        removed = await self.call('todo_write', {'snapshot_token': self.base['snapshot_token'], 'todos': rows})
        self.assertFalse(removed.is_error, removed.content)
        result = await self.update(0, status='completed')
        self.assertTrue(result.is_error)
        self.assertEqual(self.session.latest('todo/write').seq, removed.display['source_seq'])
        self.assertEqual(len(self.session.latest('todo/write').data['todos']), 7)

    async def test_non_snapshot_event_sequence_is_rejected(self):
        unrelated = await self.session.append('note', {'kind': 'diagnostic'})
        result = await self.update(1, version={'list_id': self.base['list_id'],
            'expected_seq': unrelated.seq}, status='in_progress')
        self.assertEqual(self.payload(result)['code'], 'VERSION_CONFLICT')
        self.assertEqual(len(self.session.events_of_type('todo/write')), 1)

    async def test_disjoint_merge_preserves_new_evidence_on_other_task(self):
        await self.session.append('tool/result', {'call_id': 'verify-other', 'name': 'read',
            'results': [{'tool_use_id': 'verify-other', 'is_error': False, 'content': '核对另一任务'}]},
            surface_op=AppendOp())
        first = await self.update(0, evidence_call_ids=['verify-other'])
        self.assertFalse(first.is_error, first.content)
        result = await self.update(1, status='in_progress')
        self.assertFalse(result.is_error, result.content)
        rows = self.session.latest('todo/write').data['todos']
        self.assertEqual(rows[0]['evidence'][0]['call_id'], 'verify-other')
        self.assertEqual(rows[1]['status'], 'in_progress')

    async def test_foreign_session_token_is_never_used_as_merge_baseline(self):
        other = self.sessions.create()
        ctx = ToolRunContext(call_id='foreign', session_id=other.id, cwd=self.directory.name,
            turn=1, step=1, services={'session': other})
        result = await self.registry.execute_call(ToolUseBlock(id='foreign', name='todo_write',
            input={'todos': [{'content': '其他会话任务', 'status': 'pending'}]}), ctx)
        self.assertFalse(result.is_error, result.content)
        rejected = await self.update(0, version={'snapshot_token': result.display['snapshot_token']},
            status='completed')
        self.assertEqual(self.payload(rejected)['code'], 'INVALID_SNAPSHOT')
        self.assertEqual(len(self.session.events_of_type('todo/write')), 1)
