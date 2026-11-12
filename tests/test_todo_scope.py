"""验证计划抄写错误的定向恢复、最小更新参数及审批边界。"""
import json
import tempfile
import unittest

from harness.app import plans
from harness.core.todo_progress import TodoProgressReminder
from harness.llm.types import ToolUseBlock
from harness.session.store import SessionStore
from harness.tools.builtin.todo_tools import TodoStore, build_todo_tools
from harness.tools.registry import ToolRegistry, ToolRunContext


class TodoScopeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.sessions = SessionStore(self.directory.name)
        self.session = self.sessions.create()
        self.registry = ToolRegistry()
        for tool in build_todo_tools(TodoStore(session_of=self.sessions.get)):
            self.registry.register(tool)
        self.ctx = ToolRunContext(call_id='scope-test', session_id=self.session.id,
            cwd=self.directory.name, turn=1, step=1, services={'session': self.session})
        self.plan_id = await self.plan()
        self.created = await self.call('todo_write', {'plan_id': self.plan_id, 'todos': [
            {'content': f'实施步骤 {index + 1}', 'status': 'pending'} for index in range(25)]})
        self.assertFalse(self.created.is_error, self.created.content)

    async def call(self, name, args):
        return await self.registry.execute_call(ToolUseBlock(id='scope-test', name=name, input=args), self.ctx)

    async def plan(self, status='approved'):
        plan_id = await plans.submit(self.session, '# 实施计划', 'scope-plan')
        if status != 'pending':
            await plans.decide(self.session, plan_id, status)
        return plan_id

    def payload(self, result):
        self.assertTrue(result.is_error)
        self.assertFalse(result.progress)
        payload = json.loads(result.content[0].text)
        self.assertEqual(payload, result.display)
        self.assertFalse(payload['updated'])
        return payload

    def update_args(self, **scope):
        return {**scope, 'updates': [{'id': self.created.data[0].id, 'status': 'completed'}]}

    async def test_mistyped_plan_returns_pinned_recovery_and_retry_preserves_tasks(self):
        before = self.session.latest('todo/write')
        result = await self.call('todo_update', self.update_args(
            list_id=self.created.display['list_id'], plan_id='plan_mistyped',
            snapshot_token=self.created.display['snapshot_token'], expected_seq=before.seq))
        payload = self.payload(result)
        self.assertEqual(payload['code'], 'UNKNOWN_PLAN')
        self.assertEqual(payload['plan'], {'id': 'plan_mistyped', 'status': None})
        self.assertEqual(payload['current']['plan_id'], self.plan_id)
        self.assertEqual(payload['current']['source_seq'], before.seq)
        self.assertEqual(self.session.latest('todo/write').seq, before.seq)
        recovered = await self.call(payload['recovery']['tool'], payload['recovery']['arguments'])
        self.assertFalse(recovered.is_error, recovered.content)
        page = recovered.data
        self.assertIsNotNone(page['next_offset'])
        tail = await self.call('todo_read', {**page['update_arguments'], 'offset': page['next_offset']})
        self.assertFalse(tail.is_error, tail.content)
        self.assertEqual(len(page['todos']) + len(tail.data['todos']), 25)
        updated = await self.call('todo_update', self.update_args(**page['update_arguments']))
        self.assertFalse(updated.is_error, updated.content)
        restored = SessionStore(self.directory.name).get(self.session.id).latest('todo/write')
        self.assertEqual(len(restored.data['todos']), 25)
        self.assertEqual(restored.data['todos'][0]['status'], 'completed')
        self.assertTrue(all(row['status'] == 'pending' for row in restored.data['todos'][1:]))
        self.assertEqual(restored.data['plan_id'], self.plan_id)

    async def test_scope_errors_recover_selected_inactive_list(self):
        other_plan = await self.plan()
        other = await self.call('todo_write', {'plan_id': other_plan,
            'todos': [{'content': '另一清单', 'status': 'pending'}]})
        self.assertFalse(other.is_error, other.content)
        before = self.session.latest('todo/write').seq
        for plan_id, code in [('plan_mistyped', 'UNKNOWN_PLAN'), (other_plan, 'SCOPE_MISMATCH')]:
            with self.subTest(plan_id=plan_id):
                result = await self.call('todo_update', self.update_args(
                    list_id=self.created.display['list_id'], plan_id=plan_id,
                    snapshot_token=self.created.display['snapshot_token']))
                payload = self.payload(result)
                self.assertEqual(payload['code'], code)
                self.assertEqual(payload['current']['list_id'], self.created.display['list_id'])
                recovered = await self.call('todo_read', payload['recovery']['arguments'])
                self.assertFalse(recovered.is_error, recovered.content)
                self.assertEqual(recovered.data['list_id'], self.created.display['list_id'])
        self.assertEqual(self.session.latest('todo/write').seq, before)

    async def test_unknown_plan_on_read_write_and_bind_is_structured(self):
        before = self.session.latest('todo/write').seq
        for name, args in [
            ('todo_read', {}),
            ('todo_write', {'todos': []}),
            ('todo_bind', {}),
        ]:
            with self.subTest(tool=name):
                result = await self.call(name, {**args, 'plan_id': 'plan_missing',
                    'list_id': self.created.display['list_id'],
                    'snapshot_token': self.created.display['snapshot_token']})
                self.assertEqual(self.payload(result)['code'], 'UNKNOWN_PLAN')
        self.assertEqual(self.session.latest('todo/write').seq, before)

    async def test_unapproved_creation_and_binding_never_commit(self):
        for status in ('pending', 'revision_requested', 'closed', 'superseded'):
            plan_id = await self.plan(status)
            before = self.session.latest('todo/write').seq
            for name, args in [
                ('todo_write', {'todos': [{'content': '未经批准', 'status': 'pending'}]}),
                ('todo_bind', {'list_id': self.created.display['list_id'],
                               'snapshot_token': self.created.display['snapshot_token']}),
            ]:
                with self.subTest(status=status, tool=name):
                    payload = self.payload(await self.call(name, {**args, 'plan_id': plan_id}))
                    self.assertEqual(payload['code'], 'PLAN_NOT_APPROVED')
                    self.assertEqual(payload['plan'], {'id': plan_id, 'status': status})
                    self.assertEqual(payload['recovery']['max_retries'], 0)
                    self.assertEqual(self.session.latest('todo/write').seq, before)

    async def test_token_only_update_still_checks_persisted_approval(self):
        await plans.decide(self.session, self.plan_id, 'revision_requested')
        before = self.session.latest('todo/write').seq
        result = await self.call('todo_update', self.update_args(
            snapshot_token=self.created.display['snapshot_token']))
        payload = self.payload(result)
        self.assertEqual(payload['code'], 'PLAN_NOT_APPROVED')
        read = await self.call('todo_read', payload['recovery']['arguments'])
        self.assertFalse(read.is_error, read.content)
        retried = await self.call('todo_update', self.update_args(**read.data['update_arguments']))
        self.assertEqual(self.payload(retried)['code'], 'PLAN_NOT_APPROVED')
        self.assertEqual(self.session.latest('todo/write').seq, before)

    async def test_minimal_arguments_from_write_and_read_locate_same_list(self):
        text_metadata = json.loads(self.created.content[0].text.splitlines()[0])
        args = self.created.display['update_arguments']
        self.assertEqual(args, {'snapshot_token': self.created.display['snapshot_token']})
        self.assertEqual(text_metadata['update_arguments'], args)
        read = await self.call('todo_read', args)
        self.assertFalse(read.is_error, read.content)
        self.assertEqual(read.data['update_arguments'], args)
        result = await self.call('todo_update', self.update_args(**args))
        self.assertFalse(result.is_error, result.content)
        self.assertNotEqual(result.display['update_arguments'], args)

    async def test_progress_notice_provides_executable_minimal_arguments(self):
        event = self.session.latest('todo/write')
        reminder = TodoProgressReminder()
        reminder.sync(event)
        for _ in range(3):
            reminder.observe_batch(event, successful_work=True)
        notice = reminder.take_notice(event, session_id=self.session.id)
        data = json.loads(notice['text'].split('\n')[-1])
        self.assertEqual(data['update_arguments'], self.created.display['update_arguments'])
        result = await self.call('todo_update', self.update_args(**data['update_arguments']))
        self.assertFalse(result.is_error, result.content)

    async def test_bind_receipt_uses_new_plan_and_blocks_previous_receipt(self):
        plan_id = await self.plan()
        bound = await self.call('todo_bind', {'list_id': self.created.display['list_id'],
            'plan_id': plan_id, 'snapshot_token': self.created.display['snapshot_token']})
        self.assertFalse(bound.is_error, bound.content)
        self.assertEqual(bound.display['plan_id'], plan_id)
        stale = await self.call('todo_update', self.update_args(**self.created.display['update_arguments']))
        self.assertEqual(self.payload(stale)['code'], 'SCOPE_MISMATCH')
        result = await self.call('todo_update', self.update_args(**bound.display['update_arguments']))
        self.assertFalse(result.is_error, result.content)
        self.assertEqual(result.display['list_id'], self.created.display['list_id'])

    async def test_foreign_plan_cannot_reassign_list(self):
        other = self.sessions.create()
        plan_id = await plans.submit(other, '# 另一会话计划', 'foreign-plan')
        await plans.decide(other, plan_id, 'approved')
        before = self.session.latest('todo/write').seq
        result = await self.call('todo_update', self.update_args(plan_id=plan_id,
            snapshot_token=self.created.display['snapshot_token']))
        self.assertEqual(self.payload(result)['code'], 'UNKNOWN_PLAN')
        self.assertEqual(self.session.latest('todo/write').seq, before)
