"""验证分层工具绑定可信身份、写入声明及修订冲突。"""
import tempfile
import unittest
from pathlib import Path

from harness.app.layered_memory import LayeredMemory
from harness.app.memory import ProjectMemory
from harness.tools.builtin.memory_tools import build_layered_memory_tools
from harness.tools.registry import ToolRegistry, ToolRunContext
from harness.llm.types import ToolUseBlock
from harness.tools.write_scope import declared_targets, write_scope_denial


class LayeredMemoryToolTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.project = self.root / 'project'
        self.project.mkdir()
        self.memory = LayeredMemory(self.root / 'home', ProjectMemory())
        self.tools = {tool.schema.name: tool for tool in build_layered_memory_tools(self.memory)}

    def context(self, identity='one', **services):
        return ToolRunContext(call_id='call', session_id=identity, cwd=str(self.project),
                              turn=1, step=1, services=services)

    def test_tool_effects_and_real_targets(self):
        self.assertEqual(self.tools['user_memory_save'].effect, 'external_write')
        self.assertEqual(self.tools['memory_save'].effect, 'workspace_write')
        for prefix, scope in [('memory', 'project'), ('user_memory', 'user'), ('memory', 'session')]:
            tool = self.tools[prefix + '_save']
            expected = self.memory.store(scope, session_id='one').path(self.project)
            self.assertEqual(declared_targets(tool, {} if scope == 'user' else {'scope': scope}, self.context()), ((expected, 'write'),))
            self.assertEqual(self.tools['memory_recall'].effect, 'read')
            merge = self.tools[prefix + '_consolidate']
            self.assertEqual(merge.effect, tool.effect)
            self.assertEqual(declared_targets(merge, {} if scope == 'user' else {'scope': scope}, self.context()), ((expected, 'write'),))

    def test_session_tool_uses_calling_session(self):
        saved = self.tools['memory_save'].execute({'content': '会话任务', 'scope': 'session'}, self.context())
        self.assertEqual(saved['source'], 'chat:one')
        own = self.tools['memory_recall'].execute({'scopes': ['session']}, self.context())
        other = self.tools['memory_recall'].execute({'scopes': ['session']}, self.context('two'))
        self.assertEqual(len(own['memories']), 1)
        self.assertEqual(other, {'memories': []})
        self.assertEqual(self.tools['memory_recall'].execute({'scopes': ['project']}, self.context()), {'memories': []})

    def test_workspace_restriction_cannot_write_global_preference(self):
        ctx = self.context(workspace_write_root=str(self.project))
        self.assertIsNotNone(write_scope_denial(self.tools['user_memory_save'], {}, ctx))
        self.assertIsNone(write_scope_denial(self.tools['memory_save'], {'scope': 'project'}, ctx))
        self.assertFalse(self.memory.home.exists())

    def test_forget_is_scoped_and_requires_revision(self):
        saved = self.tools['memory_save'].execute({'content': '任务', 'scope': 'session'}, self.context())
        args = {'memory_id': saved['id'], 'expected_revision': saved['revision']}
        from harness.core.errors import HarnessError
        with self.assertRaises(HarnessError):
            self.tools['memory_forget'].execute({**args, 'scope': 'session'}, self.context('two'))
        self.tools['memory_forget'].execute({**args, 'scope': 'session'}, self.context())
        self.assertEqual(self.tools['memory_recall'].execute({'scopes': ['session']}, self.context()), {'memories': []})

    def test_rewritten_queries_use_one_snapshot_and_keep_scope(self):
        from unittest.mock import patch
        saved = self.tools['memory_save'].execute({'content': '未知写入先回查，不重放', 'scope': 'session'}, self.context())
        service = self.memory.store('session', session_id='one')
        with patch.object(ProjectMemory, 'list', wraps=None) as read:
            read.return_value = [saved]
            result = service.recall(self.project, queries=['ambiguous write retry', '回查', '回查'])
            self.assertEqual(read.call_count, 1)
        self.assertEqual(result[0]['id'], saved['id'])
        self.assertEqual(result[0]['query_matches'], [1])
        result = self.tools['memory_recall'].execute({'queries': ['回查'], 'scopes': ['session']}, self.context('two'))
        self.assertEqual(result, {'memories': []})

    def test_invalid_rewrites_rejected_before_any_read(self):
        from unittest.mock import patch
        from harness.core.errors import HarnessError
        for queries in ([], [' '], ['x'] * 5, [False], 'query', ['x' * 501]):
            with self.subTest(queries=queries), patch.object(ProjectMemory, 'list') as read:
                with self.assertRaises(HarnessError):
                    self.memory.project.recall(self.project, queries=queries)
                read.assert_not_called()
        with self.assertRaises(HarnessError):
            self.memory.project.recall(self.project, 'original', queries=['rewrite'])

    def test_explicit_conflicting_modes_fail_even_when_query_is_empty(self):
        from unittest.mock import patch
        from harness.core.errors import HarnessError
        for scope in ('project', 'user', 'session'):
            for args in ({'query': '', 'queries': ['rewrite']}, {'queries': None},
                         {'query': '', 'queries': None}):
                with self.subTest(scope=scope, args=args), patch.object(ProjectMemory, 'list') as read:
                    with self.assertRaises(HarnessError):
                        self.tools['memory_recall'].execute({**args, 'scopes': [scope]}, self.context())
                    read.assert_not_called()
        self.assertEqual(self.tools['memory_recall'].execute({'query': '', 'scopes': ['project']}, self.context()), {'memories': []})

    def test_catalog_keeps_five_common_tools_and_nine_lazy_maintenance_tools(self):
        from harness.tools.discovery import ToolDiscovery, build_tool_search
        common = {'memory_recall', 'memory_save', 'memory_forget', 'user_memory_save', 'user_memory_forget'}
        self.assertEqual(len(self.tools), 14)
        self.assertFalse(any(name.startswith('session_memory_') for name in self.tools))
        registry = ToolRegistry()
        for tool in self.tools.values():
            registry.register(tool)
        registry.register(build_tool_search())
        discovery = ToolDiscovery()
        active, deferred = discovery.split(registry.schemas(), registry, 'auto')
        self.assertEqual({schema.name for schema in active}, common | {'search_tools'})
        self.assertEqual(len(deferred), 9)
        all_tools, deferred = discovery.split(registry.schemas(), registry, 'all')
        self.assertEqual(len(all_tools), 15)
        self.assertEqual(deferred, [])
        registry.unregister('search_tools')
        active, deferred = discovery.split(registry.schemas(), registry, 'auto')
        self.assertEqual(len(active), 14)
        self.assertEqual(deferred, [])

    def test_combined_recall_limits_results_reads_each_layer_once_and_namespaces_ids(self):
        from unittest.mock import patch
        from uuid import UUID
        shared_id = 'mem_' + 'a' * 32
        with patch('harness.app.memory.uuid.uuid4', return_value=UUID('a' * 32)):
            for scope in ('session', 'project', 'user'):
                store = self.memory.store(scope, session_id='one')
                store.save(self.project, '共同规则 ' + scope)
        original = ProjectMemory.list
        reads = []
        def read(store, cwd, *args, **kwargs):
            reads.append(store.path(cwd))
            return original(store, cwd, *args, **kwargs)
        with patch.object(ProjectMemory, 'list', read):
            result = self.tools['memory_recall'].execute({'queries': ['共同规则', 'unmatched', '共同规则'],
                                                         'limit': 2}, self.context())
        self.assertEqual(len(reads), 3)
        self.assertEqual(len(set(reads)), 3)
        self.assertEqual(len(result['memories']), 2)
        self.assertEqual(len({row['scope'] for row in result['memories']}), 2)
        self.assertTrue(all(row['id'] == shared_id and row['query_matches'] == [0] for row in result['memories']))
        current = self.tools['memory_recall'].execute({'query': shared_id}, self.context('two'))
        self.assertEqual({row['scope'] for row in current['memories']}, {'project', 'user'})

    def test_invalid_or_unbound_scopes_fail_before_read_and_missing_scope_cannot_write(self):
        from unittest.mock import patch
        from harness.core.errors import HarnessError
        for scopes in ([], ['project', 'project'], ['other'], 'user', [None], [['user']]):
            with self.subTest(scopes=scopes), patch.object(ProjectMemory, 'list') as read:
                with self.assertRaises(HarnessError):
                    self.tools['memory_recall'].execute({'scopes': scopes}, self.context())
                read.assert_not_called()
        unbound = ToolRunContext(call_id='unbound', session_id='', cwd=None, turn=1, step=1)
        for scopes in (['user', 'project'], ['user', 'session']):
            with self.subTest(scopes=scopes), patch.object(ProjectMemory, 'list') as read:
                with self.assertRaises(HarnessError):
                    self.tools['memory_recall'].execute({'scopes': scopes}, unbound)
                read.assert_not_called()
        self.assertEqual(self.tools['memory_recall'].execute({}, unbound), {'memories': []})
        for args in ({'content': '禁止默认写入'}, {'content': '禁止用户写入', 'scope': 'user'}):
            with self.assertRaises(HarnessError):
                self.tools['memory_save'].execute(args, self.context())
        self.assertFalse(self.memory.home.exists())
        self.assertFalse(self.memory.project.path(self.project).exists())

    def test_single_scope_rewrites_preserve_original_ranking_and_matches(self):
        import random
        generator = random.Random(7)
        for index in range(40):
            words = generator.sample(['alpha', 'beta', 'gamma', 'delta', 'epsilon', 'zeta'], generator.randint(1, 6))
            self.memory.project.save(self.project, ' '.join(words) + ' record' + str(index), pinned=index % 3 == 0)
        queries = ['alpha beta', 'gamma delta', 'epsilon zeta']
        expected = self.memory.project.recall(self.project, queries=queries, limit=100)
        actual = self.tools['memory_recall'].execute({'scopes': ['project'], 'queries': queries, 'limit': 100},
                                                    self.context())['memories']
        self.assertEqual(actual, [{**row, 'scope': 'project'} for row in expected])


class ConsolidationPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_scope_cannot_bypass_schema_readonly_or_workspace_boundaries(self):
        from harness.tools.builtin.memory_tools import WRITES
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            project = root / 'project'
            project.mkdir()
            memory = LayeredMemory(root / 'home', ProjectMemory())
            registry = ToolRegistry()
            for tool in build_layered_memory_tools(memory):
                registry.register(tool)
            counter = 0
            async def invoke(name, args, **services):
                nonlocal counter
                counter += 1
                context = ToolRunContext(call_id=str(counter), session_id='one', cwd=str(project),
                                         turn=1, step=counter, services=services)
                return await registry.execute_call(ToolUseBlock(str(counter), name, args), context)
            for name, args in [('memory_save', {'content': '缺少作用域'}),
                               ('memory_save', {'scope': 'user', 'content': '越权'}),
                               ('user_memory_save', {'scope': 'project', 'content': '伪装'}),
                               ('memory_save', {'scope': 'session', 'session_id': 'other', 'content': '越界'}),
                               ('memory_recall', {'scopes': ['project'], 'path': '/other'})]:
                result = await invoke(name, args)
                self.assertTrue(result.is_error, (name, args, result.content))
            payloads = {
                'save': {'content': '不可写入'},
                'forget': {'memory_id': 'unknown', 'expected_revision': 1},
                'consolidate': {'content': '不可整理', 'inputs': [
                    {'id': 'one', 'revision': 1}, {'id': 'two', 'revision': 1}]},
                'archive': {'inputs': [{'id': 'one', 'revision': 1}]},
                'restore': {'memory_id': 'one', 'archived_revision': 1},
            }
            for action in WRITES:
                for scope in ('session', 'project', 'user'):
                    name = ('user_memory_' if scope == 'user' else 'memory_') + action
                    args = payloads[action] if scope == 'user' else {**payloads[action], 'scope': scope}
                    for restriction in ('task_read_only', 'recovery_read_only'):
                        result = await invoke(name, args, **{restriction: True})
                        self.assertTrue(result.is_error, (name, scope, restriction))
                    if scope != 'project':
                        result = await invoke(name, args, workspace_write_root=str(project))
                        self.assertTrue(result.is_error, (name, scope))
            self.assertFalse(memory.home.exists())
            self.assertFalse(memory.project.path(project).exists())

    async def test_registered_pipeline_denies_readonly_then_consolidates_once(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        memory = LayeredMemory(root / 'home', ProjectMemory())
        rows = [memory.project.save(root, text, pinned=True) for text in ('甲项目决策', '乙项目决策')]
        registry = ToolRegistry()
        for tool in build_layered_memory_tools(memory):
            registry.register(tool)
        args = {'content': '共同决策摘要', 'inputs': [{'id': row['id'], 'revision': row['revision']} for row in rows]}
        before = memory.project.list(root)
        def context(call, **services):
            return ToolRunContext(call_id=call, session_id='one', cwd=str(root), turn=1, step=1, services=services)
        denied = await registry.execute_call(ToolUseBlock('denied', 'memory_consolidate', {**args, 'scope': 'project'}),
                                             context('denied', task_read_only=True))
        self.assertTrue(denied.is_error)
        self.assertEqual(memory.project.list(root), before)
        result = await registry.execute_call(ToolUseBlock('accepted', 'memory_consolidate', {**args, 'scope': 'project'}), context('accepted'))
        self.assertFalse(result.is_error, result.content)
        self.assertFalse(result.data['replayed'])
        again = await registry.execute_call(ToolUseBlock('retried', 'memory_consolidate', {**args, 'scope': 'project'}), context('retried'))
        self.assertFalse(again.is_error, again.content)
        self.assertTrue(again.data['replayed'])
        self.assertEqual(len(memory.project.list(root)), 3)
        recalled = await registry.execute_call(ToolUseBlock('read', 'memory_recall', {
            'queries': ['decision', '决策'], 'limit': 2, 'scopes': ['project']}), context('read', task_read_only=True))
        self.assertFalse(recalled.is_error, recalled.content)
        self.assertEqual(len(recalled.data['memories']), 2)
        self.assertTrue(all(row['query_matches'] == [1] for row in recalled.data['memories']))
        candidate = recalled.data['memories'][0]
        checked = await registry.execute_call(ToolUseBlock('check', 'memory_check', {'references': [{
            'id': candidate['id'], 'revision': candidate['revision'], 'excerpt': candidate['content']}], 'scope': 'project'}),
            context('check', task_read_only=True))
        self.assertFalse(checked.is_error, checked.content)
        self.assertEqual(checked.data['checks'][0]['status'], 'matched')
        self.assertEqual(checked.data['meaning'], 'record-excerpt-only')
