"""验证首次建单、计划作用域和已有清单的防覆盖边界。"""
import asyncio
import json
import tempfile
import unittest

from harness.app import plans
from harness.app.plan_mode import PlanModeController
from harness.core.todo_initialization import TodoInitialization
from harness.llm.types import ToolUseBlock
from harness.session.store import SessionStore
from harness.tools.builtin.todo_tools import TodoStore, build_todo_tools
from harness.tools.registry import ToolRegistry, ToolRunContext


class TodoCreationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.sessions = SessionStore(self.directory.name)
        self.session = self.sessions.create()
        self.controller = PlanModeController(self.sessions)
        self.registry = ToolRegistry()
        for tool in build_todo_tools(TodoStore(session_of=self.sessions.get)):
            self.registry.register(tool)
        self.ctx = ToolRunContext(call_id='todo-test', session_id=self.session.id,
            cwd=self.directory.name, turn=1, step=1,
            services={'session': self.session, 'plan_controller': self.controller})
        self.todos = [{'content': f'实施步骤 {index + 1}',
                       'status': 'in_progress' if index == 0 else 'pending'} for index in range(47)]

    async def call(self, name, args):
        return await self.registry.execute_call(ToolUseBlock(id='todo-test', name=name, input=args), self.ctx)

    async def approve_plan(self):
        plan_id = await plans.submit(self.session, '# 实施计划\n按任务清单实施。', 'plan-test')
        await plans.decide(self.session, plan_id, 'approved')
        return plan_id

    def payload(self, result):
        return json.loads(result.content[0].text)

    async def test_first_creation_without_version_persists_47_items(self):
        result = await self.call('todo_write', {'todos': self.todos})
        self.assertFalse(result.is_error, result.content)
        restored = SessionStore(self.directory.name).get(self.session.id).latest('todo/write')
        self.assertEqual(len(restored.data['todos']), 47)
        self.assertEqual(restored.data['todos'][0]['status'], 'in_progress')
        self.assertEqual(len({row['id'] for row in restored.data['todos']}), 47)

    async def test_approved_plan_defaults_read_and_create_to_same_scope(self):
        plan_id = await self.approve_plan()
        gate = TodoInitialization(-1)
        gate.sync(self.controller.implementation_approval(self.session))
        gate.take_notice(None)
        read = self.payload(await self.call('todo_read', {}))
        self.assertEqual(read['plan_id'], plan_id)
        result = await self.call('todo_write', {'todos': self.todos})
        self.assertFalse(result.is_error, result.content)
        event = self.session.latest('todo/write')
        self.assertEqual(event.data['plan_id'], plan_id)
        self.assertEqual(event.data['list_id'], read['list_id'])
        self.assertTrue(gate.has_list(event))
        self.assertEqual(len(plans.implementation(self.session, plan_id)), 47)

    async def test_explicit_initial_version_and_plan_are_still_checked(self):
        plan_id = await self.approve_plan()
        result = await self.call('todo_write', {'todos': self.todos, 'plan_id': plan_id, 'expected_seq': 10})
        self.assertEqual(self.payload(result)['code'], 'VERSION_CONFLICT')
        self.assertIsNone(self.session.latest('todo/write'))
        result = await self.call('todo_write', {'todos': self.todos, 'plan_id': plan_id, 'expected_seq': -1})
        self.assertFalse(result.is_error, result.content)

    async def test_two_versionless_creates_cannot_overwrite_each_other(self):
        results = await asyncio.gather(*(
            self.call('todo_write', {'todos': [{'content': text, 'status': 'pending'}]})
            for text in ('第一个提交', '第二个提交')))
        self.assertEqual(sum(not result.is_error for result in results), 1)
        rejected = next(result for result in results if result.is_error)
        self.assertEqual(self.payload(rejected)['code'], 'VERSION_REQUIRED')
        self.assertEqual(len(self.session.events_of_type('todo/write')), 1)
        winner = next(result for result in results if not result.is_error)
        self.assertEqual(self.session.latest('todo/write').data['todos'][0]['content'], winner.data[0].content)

    async def test_existing_list_requires_version_for_replace_and_update(self):
        created = await self.call('todo_write', {'todos': self.todos, 'expected_seq': -1})
        self.assertFalse(created.is_error, created.content)
        event = self.session.latest('todo/write')
        task_id = event.data['todos'][0]['id']
        for name, args in (
                ('todo_write', {'todos': []}),
                ('todo_update', {'updates': [{'id': task_id, 'status': 'completed'}]})):
            with self.subTest(tool=name):
                result = await self.call(name, args)
                self.assertEqual(self.payload(result)['code'], 'VERSION_REQUIRED')
                self.assertEqual(self.session.latest('todo/write').seq, event.seq)

    async def test_empty_saved_list_is_not_treated_as_first_creation(self):
        created = await self.call('todo_write', {'todos': [], 'expected_seq': -1})
        self.assertFalse(created.is_error, created.content)
        result = await self.call('todo_write', {'todos': self.todos})
        self.assertEqual(self.payload(result)['code'], 'VERSION_REQUIRED')
        self.assertEqual(len(self.session.latest('todo/write').data['todos']), 0)

    async def test_stale_snapshot_cannot_overwrite_newer_progress(self):
        created = await self.call('todo_write', {'todos': self.todos, 'expected_seq': -1})
        token = created.display['snapshot_token']
        task_id = created.data[0].id
        updated = await self.call('todo_update', {'snapshot_token': token,
            'updates': [{'id': task_id, 'status': 'completed'}]})
        self.assertFalse(updated.is_error, updated.content)
        stale = await self.call('todo_write', {'snapshot_token': token, 'todos': self.todos})
        self.assertEqual(self.payload(stale)['code'], 'VERSION_CONFLICT')
        self.assertEqual(self.session.latest('todo/write').data['todos'][0]['status'], 'completed')

    async def test_unbound_token_cannot_be_reused_with_another_plan(self):
        unbound = self.payload(await self.call('todo_read', {}))
        plan_id = await self.approve_plan()
        result = await self.call('todo_write', {'todos': self.todos, 'plan_id': plan_id,
            'snapshot_token': unbound['snapshot_token']})
        self.assertEqual(self.payload(result)['code'], 'SCOPE_MISMATCH')
        self.assertIsNone(self.session.latest('todo/write'))
        current = self.payload(await self.call('todo_read', {'plan_id': plan_id}))
        result = await self.call('todo_write', {'todos': self.todos,
            'snapshot_token': current['snapshot_token']})
        self.assertFalse(result.is_error, result.content)
        self.assertEqual(self.session.latest('todo/write').data['plan_id'], plan_id)

    async def test_new_approval_does_not_retarget_existing_list(self):
        created = await self.call('todo_write', {'todos': self.todos, 'expected_seq': -1})
        plan_id = await self.approve_plan()
        current = self.payload(await self.call('todo_read', {}))
        self.assertEqual(current['list_id'], created.display['list_id'])
        self.assertIsNone(current['plan_id'])
        replaced = await self.call('todo_write', {'todos': self.todos})
        self.assertEqual(self.payload(replaced)['code'], 'VERSION_REQUIRED')
        self.assertNotEqual(self.session.latest('todo/write').data.get('plan_id'), plan_id)
