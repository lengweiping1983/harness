"""归档工具的双库声明、只读限制、会话隔离与真实审批流水线。"""
import tempfile
import json
import unittest
from dataclasses import fields
from pathlib import Path
from unittest.mock import patch

from harness import Harness, HarnessConfig
from harness.app.layered_memory import LayeredMemory
from harness.app.memory import ProjectMemory
from harness.app.memory_archive import MemoryArchive
from harness.llm.types import ToolUseBlock
from harness.tools.builtin.memory_tools import build_layered_memory_tools
from harness.tools.registry import ToolRegistry, ToolRunContext
from harness.tools.write_scope import declared_targets, write_scope_denial
from tests.web_fixture import FixtureTransport


class ArchiveToolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.project = self.root / 'project'
        self.project.mkdir()
        self.memory = LayeredMemory(self.root / 'home', ProjectMemory())
        self.tools = {tool.schema.name: tool for tool in build_layered_memory_tools(self.memory)}
        self.registry = ToolRegistry()
        for tool in self.tools.values():
            self.registry.register(tool)
        self.calls = 0

    def context(self, identity='one', **services):
        self.calls += 1
        return ToolRunContext(call_id=str(self.calls), session_id=identity, cwd=str(self.project),
                              turn=1, step=self.calls, services=services)

    async def call(self, name, args, session='one', **services):
        ctx = self.context(session, **services)
        return await self.registry.execute_call(ToolUseBlock(ctx.call_id, name, args), ctx)

    def test_writers_declare_both_real_databases_in_all_scopes(self):
        for prefix, scope, effect in [('memory', 'project', 'workspace_write'),
                                     ('memory', 'session', 'workspace_write'),
                                     ('user_memory', 'user', 'external_write')]:
            store = self.memory.store(scope, session_id='one')
            expected = ((store.path(self.project), 'write'), (MemoryArchive(store).path(self.project), 'write'))
            for action in ('archive', 'restore'):
                tool = self.tools[prefix + '_' + action]
                self.assertEqual(tool.effect, effect)
                self.assertEqual(declared_targets(tool, {} if scope == 'user' else {'scope': scope}, self.context()), expected)
            self.assertEqual(self.tools['memory_archives'].effect, 'read')

    async def test_identity_filter_reaches_each_scoped_store(self):
        for prefix, scope in [('memory', 'project'), ('user_memory', 'user'), ('memory', 'session')]:
            store = self.memory.store(scope, session_id='one')
            selected = store.save(self.project, '目标记录')
            other = store.save(self.project, '其他记录')
            archive = MemoryArchive(store)
            archive.archive(self.project, [{'id': row['id'], 'revision': row['revision']} for row in (selected, other)])
            result = await self.call('memory_archives', {'memory_id': selected['id'], 'scope': scope}, task_read_only=True)
            self.assertFalse(result.is_error, result.content)
            self.assertEqual([item['memory'] for item in result.data['archives']], [selected])
            found = await self.call('memory_archive_search', {'query': '目标', 'scope': scope}, task_read_only=True)
            self.assertFalse(found.is_error, found.content)
            self.assertEqual([item['memory'] for item in found.data['archives']], [selected])
            self.assertEqual(found.data['meaning'], 'historical-keyword-candidates-only')

    async def test_readonly_denies_moves_and_restore_but_allows_history_reads(self):
        row = self.memory.project.save(self.project, '原始记录')
        args = {'inputs': [{'id': row['id'], 'revision': row['revision']}]}
        denied = await self.call('memory_archive', {**args, 'scope': 'project'}, task_read_only=True)
        self.assertTrue(denied.is_error)
        self.assertFalse(MemoryArchive(self.memory.project).path(self.project).exists())
        moved = await self.call('memory_archive', {**args, 'scope': 'project'})
        self.assertFalse(moved.is_error, moved.content)
        history = await self.call('memory_archives', {'scope': 'project'}, task_read_only=True)
        self.assertEqual(history.data['archives'][0]['memory'], row)
        denied = await self.call('memory_restore', {'memory_id': row['id'], 'archived_revision': 1, 'scope': 'project'}, task_read_only=True)
        self.assertTrue(denied.is_error)
        self.assertEqual(self.memory.project.list(self.project), [])

    async def test_session_history_and_restore_cannot_access_other_session(self):
        store = self.memory.store('session', session_id='one')
        row = store.save(self.project, '会话一私有记录')
        result = await self.call('memory_archive', {'inputs': [{'id': row['id'], 'revision': 1}], 'scope': 'session'})
        self.assertFalse(result.is_error, result.content)
        other = await self.call('memory_archives', {'scope': 'session'}, session='two')
        self.assertEqual(other.data['archives'], [])
        denied = await self.call('memory_restore', {'memory_id': row['id'], 'archived_revision': 1, 'scope': 'session'}, session='two')
        self.assertTrue(denied.is_error)
        restored = await self.call('memory_restore', {'memory_id': row['id'], 'archived_revision': 1, 'scope': 'session'})
        self.assertFalse(restored.is_error, restored.content)
        self.assertEqual(restored.data['memory']['revision'], 2)

    def test_archive_path_outside_workspace_is_also_checked(self):
        with patch.object(MemoryArchive, 'path', return_value=self.root / 'outside.sqlite3'):
            self.assertIsNotNone(write_scope_denial(self.tools['memory_archive'], {'scope': 'project'},
                self.context(workspace_write_root=str(self.project))))
        self.assertIsNotNone(write_scope_denial(self.tools['user_memory_restore'], {},
            self.context(workspace_write_root=str(self.project))))

    async def test_global_archive_requires_real_host_approval_and_readback_does_not(self):
        flags = {item.name: False for item in fields(HarnessConfig) if item.name.startswith('enable_')}
        flags['enable_project_memory'] = True
        app = Harness(HarnessConfig(api_key='fixture-only', home=self.root / 'app-home', cwd=self.project,
                      permission_policy='request_approval', **flags), transport=FixtureTransport())
        self.addAsyncCleanup(app.close)
        session = app.create_session()
        row = app.layered_memory.user.save(self.project, '用户明确保存的偏好')
        accepted, reviews = False, []
        async def answer(**kwargs):
            reviews.append(kwargs)
            options = kwargs['questions'][0]['options']
            return [{'selected': [options[0 if accepted else -1]['label']]}]
        async def invoke(name, args):
            ctx = self.context(session.id, session=session)
            return await app.tools.execute_call(ToolUseBlock(ctx.call_id, name, args), ctx)
        with patch.object(app.questions, 'ask', side_effect=answer):
            args = {'inputs': [{'id': row['id'], 'revision': 1}]}
            rejected = await invoke('user_memory_archive', args)
            self.assertTrue(rejected.is_error)
            self.assertFalse(MemoryArchive(app.layered_memory.user).path(self.project).exists())
            accepted = True
            moved = await invoke('user_memory_archive', args)
            self.assertFalse(moved.is_error, moved.content)
            self.assertEqual(len(reviews), 2)
            history = await invoke('memory_archives', {'scope': 'user'})
            self.assertEqual(history.data['archives'][0]['memory'], row)
            self.assertEqual(len(reviews), 2)
            restored = await invoke('user_memory_restore', {'memory_id': row['id'], 'archived_revision': 1})
            self.assertFalse(restored.is_error, restored.content)
            self.assertEqual(len(reviews), 3)
        decisions = session.events_of_type('note')
        self.assertTrue(any(event.data.get('kind') == 'permission_decision' for event in decisions))

    async def test_agent_loop_discovers_archives_reads_back_and_restores(self):
        class Transport:
            def __init__(self):
                self.requests = []
                self.row = None
            async def post_stream(transport, url, *, headers, body, timeout):
                """固定模型调用验证实际工具发现与持久回执，不宣称真实模型任务质量。"""
                step = len(transport.requests)
                transport.requests.append(json.loads(body))
                calls = [
                    ('search_tools', {'query': 'memory_archive'}),
                    ('memory_archive', {'inputs': [{'id': transport.row['id'], 'revision': 1}], 'scope': 'project'}),
                    ('search_tools', {'query': 'memory_archives'}), ('memory_archives', {'scope': 'project'}),
                    ('search_tools', {'query': 'memory_archive_search'}),
                    ('memory_archive_search', {'query': '原始依据', 'scope': 'project'}),
                    ('search_tools', {'query': 'memory_restore'}),
                    ('memory_restore', {'memory_id': transport.row['id'], 'archived_revision': 1, 'scope': 'project'}),
                ]
                if step < len(calls):
                    name, args = calls[step]
                    delta = {'role': 'assistant', 'tool_calls': [{'index': 0, 'id': f'archive-{step}',
                        'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}]}
                    reason = 'tool_calls'
                else:
                    delta, reason = {'role': 'assistant', 'content': '归档与恢复核对完成'}, 'stop'
                for chunk in ({'choices': [{'delta': delta, 'finish_reason': None}]},
                              {'choices': [{'delta': {}, 'finish_reason': reason}]}):
                    yield ('data: ' + json.dumps(chunk) + '\n\n').encode()
                yield b'data: [DONE]\n\n'
        flags = {item.name: False for item in fields(HarnessConfig) if item.name.startswith('enable_')}
        flags['enable_project_memory'] = True
        transport = Transport()
        app = Harness(HarnessConfig(api_key='fixture-only', home=self.root / 'loop-home', cwd=self.project,
                      max_steps=10, max_retries=0, **flags), transport=transport)
        self.addAsyncCleanup(app.close)
        transport.row = app.memory.save(self.project, '本次明确归档的原始依据')
        session = app.create_session()
        result = await app.run_async(session, '显式归档指定记录，读取历史核对，再恢复。')
        self.assertEqual(result.reason, 'completed')
        events = session.events_of_type('tool/result')
        self.assertEqual(len(events), 8)
        self.assertTrue(all(not event.data['results'][0]['is_error'] for event in events))
        self.assertIn(transport.row['id'], json.dumps(transport.requests[4]['messages'], ensure_ascii=False))
        self.assertIn('historical-keyword-candidates-only', json.dumps(transport.requests[6]['messages']))
        self.assertEqual(app.memory.list(self.project)[0]['revision'], 2)
        self.assertEqual(MemoryArchive(app.memory).list(self.project)['archives'][0]['memory'], transport.row)
