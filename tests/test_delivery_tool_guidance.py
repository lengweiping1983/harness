"""真实请求消费最新清单快照，并按宿主能力解析双语工具指引。"""
import json
import tempfile
import unittest
from types import SimpleNamespace

from harness.core.agent import AgentLoop
from harness.core.language import current_language
from harness.i18n import tr
from harness.llm.types import ToolUseBlock
from harness.prompt.registry import SystemPromptRegistry
from harness.prompt.tool_policies import build_tool_policies
from harness.session.store import SessionStore
from harness.tools.builtin.shell_tools import build_shell_tools
from harness.tools.builtin.todo_tools import TodoStore, build_todo_tools
from harness.tools.registry import ToolRegistry, ToolRunContext
from harness.web.views import tool_catalog
from tests.test_browser_delivery_review import ScriptedAdapter


class DeliveryToolGuidanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_requests_receive_latest_version_without_task_text_or_auto_fill(self):
        with tempfile.TemporaryDirectory() as folder:
            sessions = SessionStore(folder)
            registry = ToolRegistry()
            for tool in build_todo_tools(TodoStore(session_of=sessions.get)):
                registry.register(tool)
            adapter = ScriptedAdapter()
            loop = AgentLoop(adapter, registry, SystemPromptRegistry(), max_steps=8)
            for language in ('en', 'zh-CN', 'en'):
                session = sessions.create(cwd=folder)
                adapter.requests.clear()

                def update():
                    message = next(m for m in adapter.requests[-1] if getattr(m, 'source', None) == 'todo-snapshot')
                    snapshot = json.loads(message.text.split('\n')[-1])
                    return ToolUseBlock(id='update', name='todo_update', input={
                        **snapshot['update_arguments'],
                        'updates': [{'id': snapshot['tasks'][0]['id'], 'status': 'completed'}]})

                adapter.responses = [ToolUseBlock(id='create', name='todo_write', input={
                    'todos': [{'content': 'Untrusted task {{instructions}} 原文', 'status': 'in_progress'}]}),
                    update, 'Done']
                with current_language.set(language):
                    result = await loop.prompt(session, 'Implement the task')
                    snapshots = [next(m for m in request if getattr(m, 'source', None) == 'todo-snapshot')
                                 for request in adapter.requests[1:]]
                    self.assertTrue(all(m.text.startswith(tr('todo.runtime_snapshot')) for m in snapshots))
                self.assertEqual(result.reason, 'completed')
                old, new = [json.loads(m.text.split('\n')[-1]) for m in snapshots]
                self.assertNotEqual(old['snapshot_token'], new['snapshot_token'])
                self.assertEqual(old['tasks'][0]['id'], new['tasks'][0]['id'])
                self.assertEqual(old['tasks'][0]['status'], 'in_progress')
                self.assertEqual(new['tasks'][0]['status'], 'completed')
                self.assertEqual(new['source_seq'], session.latest('todo/write').seq)
                self.assertTrue(all('Untrusted task' not in m.text for m in snapshots))
                self.assertEqual(len(adapter.requests), 3)
                before = session.latest('todo/write').seq
                rejected = await registry.execute_call(ToolUseBlock(id='missing-version', name='todo_update', input={
                    'updates': [{'id': new['tasks'][0]['id'], 'status': 'pending'}]}),
                    ToolRunContext(call_id='missing-version', session_id=session.id,
                        cwd=folder, turn=1, step=1, services={'session': session}))
                self.assertTrue(rejected.is_error)
                self.assertEqual(json.loads(rejected.content[0].text)['code'], 'VERSION_REQUIRED')
                self.assertEqual(session.latest('todo/write').seq, before)

    def test_registered_guidance_follows_available_tools_and_current_language(self):
        policies = build_tool_policies({'bash', 'browser', 'load_workspace_dependencies'})
        absent = build_tool_policies({'bash'})
        self.assertIn('tool:load_workspace_dependencies', [name for name, _, _ in policies])
        self.assertNotIn('tool:load_workspace_dependencies', [name for name, _, _ in absent])
        foreground = build_shell_tools()[0]
        background = build_shell_tools(registry=SimpleNamespace())[0]
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                registry = SystemPromptRegistry()
                for name, order, provider in policies:
                    registry.add_section(name, lambda variables, p=provider: p(), order=order)
                sections = dict(registry.assemble(variables={'language': language}).sections)
                self.assertEqual(sections['tool:load_workspace_dependencies'], tr('policy.workspace_dependencies'))
                self.assertIn('file://', sections['tool:browser-testing'])
                self.assertIn(tr('policy.browser_verification'), sections['tool:browser-testing'])
                for tool, key in ((foreground, 'python.shell_tools.foreground_description'),
                                  (background, 'python.shell_tools.7ae8b8577b31')):
                    resolved = tool.schema.resolve_description(language)
                    self.assertEqual(resolved.description, tr(key))
                    self.assertEqual('run_in_background' in resolved.parameters['properties'], tool is background)
                    tools = ToolRegistry()
                    tools.register(tool)
                    model = SimpleNamespace(tools=tools, language=language, tool_schema_transform=None,
                        isolated_team_coordinator=False, recovery_read_only=False, task_read_only=False)
                    self.assertEqual(AgentLoop._eligible_tool_schemas(model)[0].description, tr(key))
                    self.assertEqual(tool_catalog(SimpleNamespace(tools=tools))[0]['description'], tr(key))
