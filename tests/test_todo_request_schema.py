"""清单版本进入真实模型工具定义，SDK、显式作用域和并发冲突仍严格保留。"""
import json
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path

from harness import Harness, HarnessConfig
from harness.app import plans
from harness.core.agent import AgentLoop
from harness.core.language import current_language
from harness.llm.types import ToolUseBlock
from harness.prompt.registry import SystemPromptRegistry
from harness.session.store import SessionStore
from harness.tools.builtin.todo_tools import TodoStore, build_todo_tools
from harness.tools.registry import ToolDefinition, ToolRegistry, ToolRunContext
from tests.agent_configuration_fixture import RecordingTransport
from tests.test_browser_delivery_review import ScriptedAdapter


class SchemaTransport(RecordingTransport):
    """记录实际传输正文，按其中已声明的版本和任务ID构造受控响应。"""

    async def post_stream(self, url, *, headers, body, timeout):
        request = json.loads(body)
        self.requests.append(request)
        if len(self.requests) == 1:
            name, arguments = 'todo_write', {'todos': [{'content': 'Original task 原文', 'status': 'in_progress'}]}
        elif len(self.requests) == 2:
            tool = next(row['function'] for row in request['tools'] if row['function']['name'] == 'todo_update')
            properties = tool['parameters']['properties']
            name, arguments = 'todo_update', {'snapshot_token': properties['snapshot_token']['enum'][0],
                'updates': [{'id': properties['updates']['items']['properties']['id']['enum'][0], 'status': 'completed'}]}
        else:
            name = None
        delta = ({'tool_calls': [{'index': 0, 'id': f'call_{len(self.requests)}', 'type': 'function',
                  'function': {'name': name, 'arguments': json.dumps(arguments)}}]}
                 if name else {'content': 'Done'})
        yield ('data: ' + json.dumps({'choices': [{'delta': delta,
            'finish_reason': 'tool_calls' if name else 'stop'}]}) + '\n\n').encode()
        yield b'data: [DONE]\n\n'


class TodoRequestSchemaTests(unittest.IsolatedAsyncioTestCase):
    async def test_wire_schema_requires_the_issued_snapshot_and_real_task_ids_without_mutating_registry(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            flags = {item.name: False for item in fields(HarnessConfig) if item.name.startswith('enable_')}
            flags['enable_todo'] = True
            transport = SchemaTransport()
            host = Harness(HarnessConfig(api_key='fixture', model='fixture', home=root / 'state',
                cwd=root, max_steps=6, max_retries=0, **flags), transport=transport,
                codex_gateway_home=root / 'gateway')
            try:
                original = host.tools.get('todo_update').schema.to_dict()
                for language in ('en', 'zh-CN', 'en'):
                    host.config.language = language
                    transport.requests.clear()
                    session = host.create_session()
                    with current_language.set(language):
                        result = await host._agent_for(session).prompt(session, 'Implement task')
                    self.assertEqual(result.reason, 'completed', result.error)
                    self.assertEqual(len(transport.requests), 3)
                    parameters = next(row['function']['parameters'] for row in transport.requests[1]['tools']
                                      if row['function']['name'] == 'todo_update')
                    event = session.events_of_type('todo/write')[0]
                    token = json.loads(session.events_of_type('tool/result')[0].data['results'][0]['content'].splitlines()[0])['snapshot_token']
                    self.assertIn('snapshot_token', parameters['required'])
                    self.assertEqual(parameters['properties']['snapshot_token']['enum'], [token])
                    self.assertEqual(parameters['properties']['updates']['items']['properties']['id']['enum'],
                                     [event.data['todos'][0]['id']])
                    self.assertEqual(session.latest('todo/write').data['todos'][0]['status'], 'completed')
                    self.assertEqual(host.tools.get('todo_update').schema.to_dict(), original)
                    saved = session.latest('todo/write')
                    context = ToolRunContext(call_id='sdk', session_id=session.id, cwd=str(root),
                        turn=1, step=1, services={'session': session})
                    missing = await host.tools.execute_call(ToolUseBlock(id='missing-version', name='todo_update', input={
                        'updates': [{'id': event.data['todos'][0]['id'], 'status': 'pending'}]}), context)
                    self.assertTrue(missing.is_error)
                    self.assertEqual(json.loads(missing.content[0].text)['code'], 'VERSION_REQUIRED')
                    self.assertEqual(session.latest('todo/write').seq, saved.seq)
                    sdk = await host.tools.execute_call(ToolUseBlock(id='sdk', name='todo_update', input={
                        'list_id': saved.data['list_id'], 'expected_seq': saved.seq,
                        'updates': [{'id': event.data['todos'][0]['id'], 'status': 'pending'}]}), context)
                    self.assertFalse(sdk.is_error, sdk.content)
            finally:
                await host.close()

    async def test_explicit_read_of_an_inactive_list_binds_that_list_in_the_next_request(self):
        with tempfile.TemporaryDirectory() as folder:
            sessions = SessionStore(folder)
            session = sessions.create(cwd=folder)
            registry = ToolRegistry()
            for tool in build_todo_tools(TodoStore(session_of=sessions.get)):
                registry.register(tool)
            ctx = ToolRunContext(call_id='setup', session_id=session.id, cwd=folder,
                                 turn=1, step=1, services={'session': session})
            async def call(name, args):
                return await registry.execute_call(ToolUseBlock(id='setup', name=name, input=args), ctx)
            first = await call('todo_write', {'todos': [{'content': 'First', 'status': 'pending'}]})
            plan = await plans.submit(session, '# Other plan', 'fixture-plan')
            await plans.decide(session, plan, 'approved')
            second = await call('todo_write', {'plan_id': plan,
                'todos': [{'content': 'Second', 'status': 'pending'}]})
            self.assertFalse(second.is_error, second.content)
            async def inspect(args, ctx):
                return {'observed': True}
            registry.register(ToolDefinition({'name': 'inspect', 'parameters': {'type': 'object'}},
                                            inspect, effect='read'))
            adapter = ScriptedAdapter()
            def update_first():
                schema = next(row['parameters'] for row in adapter.tool_schemas[-1] if row['name'] == 'todo_update')
                self.assertEqual(schema['properties']['snapshot_token']['enum'], [first.display['snapshot_token']])
                self.assertEqual(schema['properties']['updates']['items']['properties']['id']['enum'], [first.data[0].id])
                reminder = next(message for message in adapter.requests[-1]
                                if getattr(message, 'source', None) == 'todo-progress-reminder')
                self.assertEqual(json.loads(reminder.text.split('\n')[-1])['snapshot_token'], first.display['snapshot_token'])
                return ToolUseBlock(id='update-first', name='todo_update', input={
                    'snapshot_token': first.display['snapshot_token'],
                    'updates': [{'id': first.data[0].id, 'status': 'completed'}]})
            adapter.responses = [ToolUseBlock(id='read-first', name='todo_read',
                input={'list_id': first.display['list_id']}),
                *(ToolUseBlock(id=f'inspect-{n}', name='inspect', input={}) for n in range(3)), update_first, 'Done']
            loop = AgentLoop(adapter, registry, SystemPromptRegistry(), max_steps=8)
            result = await loop.prompt(session, 'Finish the first list')
            self.assertEqual(result.reason, 'completed', result.error)
            self.assertEqual(session.latest('todo/write').data['list_id'], first.display['list_id'])
            self.assertEqual(len(adapter.requests), 6)

    async def test_conflict_does_not_rebase_the_issued_schema_until_an_explicit_read(self):
        with tempfile.TemporaryDirectory() as folder:
            sessions = SessionStore(folder)
            session = sessions.create(cwd=folder)
            registry = ToolRegistry()
            for tool in build_todo_tools(TodoStore(session_of=sessions.get)):
                registry.register(tool)
            ctx = ToolRunContext(call_id='setup', session_id=session.id, cwd=folder,
                                 turn=1, step=1, services={'session': session})
            first = await registry.execute_call(ToolUseBlock(id='setup', name='todo_write', input={
                'todos': [{'content': 'First', 'status': 'pending'}]}), ctx)
            token, task = first.display['snapshot_token'], first.data[0].id
            adapter = ScriptedAdapter()
            loop = AgentLoop(adapter, registry, SystemPromptRegistry(), max_steps=8)
            async def concurrent_write(agent, current, turn, step):
                if step == 2:
                    changed = await registry.execute_call(ToolUseBlock(id='external', name='todo_update', input={
                        'snapshot_token': token, 'updates': [{'id': task, 'content': 'External change'}]}), ctx)
                    self.assertFalse(changed.is_error, changed.content)
            loop.add_pre_step_hook(concurrent_write)
            adapter.responses = [ToolUseBlock(id='read-old', name='todo_read', input={}),
                ToolUseBlock(id='stale-update', name='todo_update', input={
                    'snapshot_token': token, 'updates': [{'id': task, 'status': 'completed'}]}),
                ToolUseBlock(id='read-new', name='todo_read', input={'list_id': first.display['list_id']}),
                lambda: ToolUseBlock(id='fresh-update', name='todo_update', input={
                    'snapshot_token': next(row['parameters']['properties']['snapshot_token']['enum'][0]
                                          for row in adapter.tool_schemas[-1] if row['name'] == 'todo_update'),
                    'updates': [{'id': task, 'status': 'completed'}]}), 'Done']
            result = await loop.prompt(session, 'Finish after checking current state')
            self.assertEqual(result.reason, 'completed', result.error)
            tokens = [next(row['parameters']['properties']['snapshot_token']['enum'][0]
                           for row in schemas if row['name'] == 'todo_update') for schemas in adapter.tool_schemas]
            self.assertEqual(tokens[1:3], [token, token])
            self.assertNotEqual(tokens[3], token)
            rejected = next(row for row in session.events_of_type('tool/result') if row.data['call_id'] == 'stale-update')
            self.assertTrue(rejected.data['results'][0]['is_error'])
            self.assertEqual(json.loads(rejected.data['results'][0]['content'])['code'], 'VERSION_CONFLICT')
            self.assertEqual(session.latest('todo/write').data['todos'][0]['content'], 'External change')

    async def test_reading_an_empty_plan_does_not_reuse_another_lists_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            sessions = SessionStore(folder)
            session = sessions.create(cwd=folder)
            registry = ToolRegistry()
            for tool in build_todo_tools(TodoStore(session_of=sessions.get)):
                registry.register(tool)
            ctx = ToolRunContext(call_id='setup', session_id=session.id, cwd=folder,
                                 turn=1, step=1, services={'session': session})
            await registry.execute_call(ToolUseBlock(id='setup', name='todo_write', input={
                'todos': [{'content': 'Old list', 'status': 'completed'}]}), ctx)
            plan = await plans.submit(session, '# New plan', 'new-plan')
            await plans.decide(session, plan, 'approved')
            adapter = ScriptedAdapter()
            def create_selected():
                self.assertFalse(any(getattr(m, 'source', None) == 'todo-snapshot' for m in adapter.requests[-1]))
                schema = next(row['parameters'] for row in adapter.tool_schemas[-1] if row['name'] == 'todo_update')
                from harness.tools.builtin.todo_state import empty_snapshot
                token = empty_snapshot(session.id, plan).token
                self.assertEqual(schema['properties']['snapshot_token']['enum'], [token])
                return ToolUseBlock(id='new-list', name='todo_write', input={
                    'snapshot_token': token, 'todos': [{'content': 'New list', 'status': 'completed'}]})
            adapter.responses = [ToolUseBlock(id='read-empty', name='todo_read', input={'plan_id': plan}),
                                 create_selected, 'Done']
            loop = AgentLoop(adapter, registry, SystemPromptRegistry(), max_steps=6)
            result = await loop.prompt(session, 'Create the approved plan checklist')
            self.assertEqual(result.reason, 'completed', result.error)
            self.assertEqual(session.latest('todo/write').data['plan_id'], plan)
