"""普通开发漏建单不阻断实施；显式批准计划仍要求真实清单。"""
import json
import tempfile
import unittest
from pathlib import Path

from harness.app import plans
from harness.app.plan_mode import PlanModeController
from harness.core.agent import AgentLoop
from harness.core.errors import HarnessError
from harness.core.language import current_language
from harness.core.todo_initialization import TodoInitialization
from harness.i18n import tr
from harness.llm.types import ToolUseBlock
from harness.prompt.registry import SystemPromptRegistry
from harness.session.store import SessionStore
from harness.tools.builtin.fs_tools import build_fs_tools
from harness.tools.builtin.todo_tools import TodoStore, build_todo_tools
from harness.tools.discovery import build_tool_search
from harness.tools.registry import ToolRegistry, ToolRunContext
from tests.test_browser_delivery_review import ScriptedAdapter


class TodoInitializationAdvisoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_continuous_edits_without_list_execute_once_and_finish_in_each_language(self):
        with tempfile.TemporaryDirectory() as folder:
            sessions = SessionStore(folder)
            registry = ToolRegistry()
            for tool in [build_tool_search(), *build_fs_tools(folder),
                         *build_todo_tools(TodoStore(session_of=sessions.get))]:
                registry.register(tool)
            adapter = ScriptedAdapter()
            loop = AgentLoop(adapter, registry, SystemPromptRegistry(), max_steps=20)
            for run, language in enumerate(('en', 'zh-CN', 'en')):
                session = sessions.create(cwd=folder)
                path = f'game-{run}.js'
                calls = [ToolUseBlock(id='search', name='search_tools', input={'query': 'todo_write todo_read'})]
                calls.extend(ToolUseBlock(id=f'create-{i}', name='write', input={
                    'path': path if i == 0 else f'asset-{run}-{i}.txt', 'content': 'revision 0'})
                    for i in range(3))
                calls.extend(ToolUseBlock(id=f'edit-{i}', name='edit', input={
                    'path': path, 'old_string': f'revision {i}', 'new_string': f'revision {i + 1}'})
                    for i in range(6))
                adapter.responses = [*calls, 'Implemented; runtime not verified.']
                adapter.requests.clear()
                with current_language.set(language):
                    result = await loop.prompt(session, 'Develop a game 原文')
                    notices = [e for e in session.events_of_type('note')
                               if e.data.get('kind') == 'todo_initialization_reminder']
                    self.assertEqual(len(notices), 1)
                    self.assertTrue(notices[0].data['text'].startswith(tr('todo.initialization.advisory')))
                    consumed = [m for request in adapter.requests for m in request
                                if getattr(m, 'source', None) == 'todo-initialization']
                    self.assertEqual(len(consumed), 1)
                    self.assertEqual(consumed[0].text, notices[0].data['text'])
                    self.assertEqual(next(schema['description'] for schema in adapter.tool_schemas[-1]
                                          if schema['name'] == 'search_tools'), tr('tool.discovery.description'))
                self.assertEqual(result.reason, 'completed', result.error)
                self.assertEqual(result.text, 'Implemented; runtime not verified.')
                self.assertEqual(len(adapter.requests), len(calls) + 1)
                self.assertEqual((Path(folder) / path).read_text(), 'revision 6')
                results = session.events_of_type('tool/result')
                self.assertEqual([e.data['call_id'] for e in results], [call.id for call in calls])
                self.assertFalse(any(e.data['results'][0]['is_error'] for e in results))
                self.assertIsNone(session.latest('todo/write'))
                self.assertIsNone(session.open_turn)
                adapter.responses = ['Explanation only.']
                with current_language.set(language):
                    followup = await loop.prompt(session, 'Explain')
                self.assertEqual(followup.reason, 'completed')
                self.assertFalse(any(getattr(m, 'source', None) == 'todo-initialization'
                                     for m in adapter.requests[-1]))

    async def test_invalid_list_attempts_cannot_turn_advice_into_a_gate(self):
        with tempfile.TemporaryDirectory() as folder:
            sessions = SessionStore(folder)
            session = sessions.create(cwd=folder)
            registry = ToolRegistry()
            for tool in [*build_fs_tools(folder), *build_todo_tools(TodoStore(session_of=sessions.get))]:
                registry.register(tool)
            adapter = ScriptedAdapter()
            adapter.responses = [
                *(ToolUseBlock(id=f'write-{i}', name='write', input={
                    'path': f'file-{i}.txt', 'content': 'before'}) for i in range(3)),
                *(ToolUseBlock(id=f'invalid-{i}', name='todo_write', input={
                    'todos': [{'content': '任务', 'status': 'completed', 'evidence_call_ids': ['invented']}]})
                    for i in range(4)),
                ToolUseBlock(id='after', name='edit', input={
                    'path': 'file-0.txt', 'old_string': 'before', 'new_string': 'after'}),
                'Files changed; checklist creation failed.',
            ]
            result = await AgentLoop(adapter, registry, SystemPromptRegistry(), max_steps=15).prompt(session, 'Implement')
            self.assertEqual(result.reason, 'completed', result.error)
            self.assertEqual((Path(folder) / 'file-0.txt').read_text(), 'after')
            self.assertEqual(len(adapter.requests), 9)
            self.assertEqual(len([e for e in session.events_of_type('note')
                                  if e.data.get('kind') == 'todo_initialization_reminder']), 2)
            rejected = [e for e in session.events_of_type('tool/result') if e.data['results'][0]['is_error']]
            self.assertEqual(len(rejected), 4)
            self.assertTrue(all(e.data['name'] == 'todo_write' for e in rejected))
            self.assertIsNone(session.latest('todo/write'))

    async def test_approved_plan_still_gates_until_real_list_and_resets_scope(self):
        with tempfile.TemporaryDirectory() as folder:
            sessions = SessionStore(folder)
            session = sessions.create(cwd=folder)
            controller = PlanModeController(sessions)
            plan_id = await plans.submit(session, '# 计划\n实施与验证', 'plan-test')
            await plans.decide(session, plan_id, 'approved')
            registry = ToolRegistry()
            for tool in build_fs_tools(folder):
                registry.register(tool)
            gate = TodoInitialization(-1)
            for _ in range(3):
                gate.observe_batch(None, successful_write=True)
            self.assertIsNone(gate.denial(None, registry.get('write')))
            self.assertEqual(gate.take_notice(None)['kind'], 'todo_initialization_reminder')
            gate.sync(controller.implementation_approval(session))
            for _ in range(gate.MAX_REPAIR_REQUESTS):
                denied = gate.denial(None, registry.get('write'))
                self.assertEqual(denied['plan_id'], plan_id)
                self.assertTrue(denied['not_executed'])
                self.assertIsNone(gate.denial(None, registry.get('read')))
                self.assertTrue(gate.defer_completion(None))
                self.assertEqual(gate.take_notice(None)['kind'], 'todo_initialization_required')
            for language in ('en', 'zh-CN', 'en'):
                with current_language.set(language):
                    self.assertEqual(gate.denial(None, registry.get('write'))['message'],
                                     tr('todo.initialization.before_work'))
                    with self.assertRaises(HarnessError) as raised:
                        gate.take_notice(None)
                    self.assertEqual(raised.exception.code, 'todo/list-required')
                    self.assertEqual(raised.exception.message, tr('todo.initialization.failed'))
            for tool in build_todo_tools(TodoStore(session_of=sessions.get)):
                registry.register(tool)
            created = await registry.execute_call(ToolUseBlock(id='list', name='todo_write', input={
                'todos': [{'content': '实施与验证', 'status': 'pending'}]}),
                ToolRunContext(call_id='list', session_id=session.id, cwd=folder, turn=1, step=1,
                    services={'session': session, 'plan_controller': controller}))
            self.assertFalse(created.is_error, created.content)
            event = session.latest('todo/write')
            self.assertEqual(event.data['plan_id'], plan_id)
            self.assertIsNone(gate.denial(event, registry.get('write')))
            self.assertFalse(gate.defer_completion(event))
            gate.sync(None)
            self.assertIsNone(gate.denial(None, registry.get('write')))
            self.assertFalse(gate.defer_completion(None))

    async def test_search_loaded_todos_then_create_list_through_actual_loop(self):
        with tempfile.TemporaryDirectory() as folder:
            sessions = SessionStore(folder)
            session = sessions.create(cwd=folder)
            registry = ToolRegistry()
            for tool in [build_tool_search(), *build_todo_tools(TodoStore(session_of=sessions.get))]:
                registry.register(tool)
            adapter = ScriptedAdapter()
            adapter.responses = [
                ToolUseBlock(id='search', name='search_tools', input={'query': 'todo_write todo_read'}),
                ToolUseBlock(id='create', name='todo_write', input={
                    'todos': [{'content': '实施与验证', 'status': 'pending'}]}),
                'Task list created; implementation pending.',
                'Task list created; implementation still pending.',
            ]
            loop = AgentLoop(adapter, registry, SystemPromptRegistry(), max_steps=8)
            result = await loop.prompt(session, 'Prepare a task list')
            self.assertEqual(result.reason, 'completed', result.error)
            self.assertEqual(len(adapter.requests), 4)
            self.assertEqual(len([e for e in session.events_of_type('note')
                                  if e.data.get('kind') == 'todo_final_review']), 1)
            self.assertIsNotNone(session.latest('todo/write'))
            self.assertEqual(len(session.events_of_type('todo/write')), 1)
            self.assertFalse(any(e.data['results'][0]['is_error'] for e in session.events_of_type('tool/result')))
            self.assertFalse(any(e.data.get('kind') == 'tool-discovery/activated'
                                 for e in session.events_of_type('note')))
            names = {schema['name'] for schema in adapter.tool_schemas[1]}
            self.assertTrue({'todo_write', 'todo_read'} <= names)
            found = session.events_of_type('tool/result')[0].data['results'][0]['content']
            self.assertEqual({row['name'] for row in json.loads(found)['tools']}, {'todo_write', 'todo_read'})
