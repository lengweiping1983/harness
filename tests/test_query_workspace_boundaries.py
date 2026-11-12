"""真实临时会话的检索和祖先投影不能泄露其他工作区身份。"""
import tempfile
import unittest
from dataclasses import replace

from harness.core.errors import ToolError
from harness.core.language import current_language
from harness.i18n import tr

from harness.session.store import SessionStore
from harness.session.events import AppendOp, ReplaceOp
from harness.tools.builtin.session_query_tools import build_session_query_tools
from harness.tools.registry import ToolRegistry, ToolRunContext
from harness.llm.types import ToolUseBlock


class QueryWorkspaceBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_trace_stops_before_private_ancestor_and_search_redacts_parent(self):
        with tempfile.TemporaryDirectory() as folder:
            store = SessionStore(folder)
            private = store.create(cwd='/private', title='private ancestor')
            root = store.create(cwd='/allowed', title='visible root', parent_session=private.id)
            caller = store.create(cwd='/allowed')
            child = store.create(cwd='/allowed', title='visible child', parent_session=root.id)
            await root.append('user/message', {'text': 'needle original'}, surface_op=AppendOp())
            tools = {tool.schema.name: tool for tool in build_session_query_tools(store)}
            ctx = ToolRunContext('fixture', caller.id, '/allowed', 1, 1)
            result = tools['session_trace'].execute({'session_id': child.id}, ctx)
            self.assertEqual([node['id'] for node in result.data['ancestors']], [root.id])
            self.assertTrue(result.data['ancestorBoundary'])
            self.assertTrue(result.data['complete'])
            self.assertNotIn(private.id, str(result.data))
            self.assertNotIn('private ancestor', result.content[0].text)
            search = tools['session_search'].execute({'query': 'needle'}, ctx)
            self.assertEqual(search.data['count'], 1)
            self.assertIsNone(search.data['results'][0]['parentSession'])
            self.assertNotIn(private.id, str(search.data))

    async def test_declared_workspace_controls_both_search_and_parent_visibility(self):
        with tempfile.TemporaryDirectory() as folder:
            store = SessionStore(folder)
            caller = store.create(cwd='/original')
            parent = store.create(cwd='/selected')
            target = store.create(cwd='/selected', parent_session=parent.id)
            await target.append('user/message', {'text': 'needle'}, surface_op=AppendOp())
            tools = {tool.schema.name: tool for tool in build_session_query_tools(store)}
            result = tools['session_search'].execute({'query': 'needle'}, ToolRunContext('fixture', caller.id, '/selected', 1, 1))
            self.assertEqual(result.data['count'], 1)
            self.assertEqual(result.data['results'][0]['parentSession'], parent.id)

    async def test_authorized_lineage_and_missing_parent_remain_explicit(self):
        with tempfile.TemporaryDirectory() as folder:
            store = SessionStore(folder)
            root = store.create(cwd='/allowed')
            child = store.create(cwd='/allowed', parent_session=root.id)
            target = store.create(cwd='/allowed', parent_session=child.id)
            missing = store.create(cwd='/allowed', parent_session='ses_missing')
            tool = next(tool for tool in build_session_query_tools(store) if tool.schema.name == 'session_trace')
            ctx = ToolRunContext('fixture', target.id, '/allowed', 1, 1)
            result = tool.execute({}, ctx).data
            self.assertEqual([row['id'] for row in result['ancestors']], [child.id, root.id])
            self.assertFalse(result['ancestorBoundary'])
            self.assertTrue(result['complete'])
            result = tool.execute({'session_id': missing.id}, ctx).data
            self.assertFalse(result['complete'])
            self.assertEqual(result['unresolvedParentId'], 'ses_missing')
            root.header = replace(root.header, parent_session=target.id)
            store._write_header(root)
            with self.assertRaises(ToolError) as caught:
                tool.execute({}, ctx)
            self.assertEqual(caught.exception.code, 'SESSION_QUERY_INVALID_LINEAGE')

    async def test_same_tools_render_all_query_views_in_current_language(self):
        with tempfile.TemporaryDirectory() as folder:
            store = SessionStore(folder)
            caller = store.create(cwd='/allowed')
            target = store.create(cwd='/allowed', title='Original 用户标题')
            event = await target.append('user/message', {'text': 'needle Original 用户内容'}, surface_op=AppendOp())
            with current_language.set('zh-CN'):
                tools = {tool.schema.name: tool for tool in build_session_query_tools(store)}
            registry = ToolRegistry()
            for tool in tools.values():
                registry.register(tool)
            ctx = ToolRunContext('fixture', caller.id, '/allowed', 1, 1)
            for language in ('en', 'zh-CN', 'en'):
                with current_language.set(language):
                    cases = [
                        ('session_search', {'query': 'needle'}, tr('query.session_results', count=1)),
                        ('session_event_search', {'session_id':target.id, 'query':'needle'}, tr('query.event_results', count=1)),
                        ('session_trace', {'session_id':target.id}, tr('query.ancestors')),
                        ('session_event_trace', {'session_id':target.id, 'seq':event.seq}, tr('query.replacement_chain', value=tr('query.none'))),
                        ('session_event_read', {'session_id':target.id, 'seq':event.seq}, tr('query.event_seq', seq=event.seq)),
                    ]
                    for name, args, expected in cases:
                        result = await registry.execute_call(ToolUseBlock('query-call', name, args), ctx)
                        self.assertFalse(result.is_error)
                        self.assertEqual(result.tool_use_id, 'query-call')
                        self.assertEqual(result.display, result.data)
                        self.assertEqual(result.display['name'], name)
                        self.assertEqual(result.display['renderedText'], result.content[0].text)
                        self.assertIn(expected, result.content[0].text)
                        self.assertIn('Original 用户标题', result.content[0].text)
                    self.assertIn('Original 用户内容', tools['session_event_read'].execute({'session_id':target.id, 'seq':event.seq}, ctx).content[0].text)
                    self.assertEqual(tools['session_search'].execute({'query':'no-match'}, ctx).content[0].text, tr('query.no_sessions'))

    async def test_nonfinite_time_filters_raise_localized_argument_errors(self):
        with tempfile.TemporaryDirectory() as folder:
            store = SessionStore(folder)
            caller = store.create(cwd='/allowed')
            target = store.create(cwd='/allowed')
            tools = {tool.schema.name: tool for tool in build_session_query_tools(store)}
            ctx = ToolRunContext('fixture', caller.id, '/allowed', 1, 1)
            for language in ('en', 'zh-CN', 'en'):
                with current_language.set(language):
                    for name, fields in [('session_search', ('created_at_from', 'created_at_to', 'event_time_from', 'event_time_to')),
                                         ('session_event_search', ('time_from', 'time_to'))]:
                        for field in fields:
                            for value in ('inf', '-inf', 'Infinity', 'NaN', '1e309'):
                                with self.subTest(language=language, tool=name, field=field, value=value):
                                    with self.assertRaises(ToolError) as caught:
                                        args = {'query': 'needle', field: value}
                                        if name == 'session_event_search':
                                            args['session_id'] = target.id
                                        tools[name].execute(args, ctx)
                                    self.assertEqual(caught.exception.code, 'SESSION_QUERY_TOOL_INVALID_ARGUMENT')
                                    self.assertEqual(caught.exception.message, tr('python.session_query_tools.09b88448ac64',
                                        p0=field, p1=repr(value)))

    async def test_filter_errors_use_current_separator_and_preserve_values(self):
        with tempfile.TemporaryDirectory() as folder:
            store = SessionStore(folder)
            caller = store.create(cwd='/allowed')
            tools = {tool.schema.name: tool for tool in build_session_query_tools(store)}
            ctx = ToolRunContext('fixture', caller.id, '/allowed', 1, 1)
            values = ['Original value {name}', 'Other value']
            for language in ('en', 'zh-CN', 'en'):
                with current_language.set(language):
                    with self.assertRaises(ToolError) as caught:
                        tools['session_search'].execute({'query':'needle', 'availability':values}, ctx)
                    self.assertEqual(caught.exception.code, 'SESSION_QUERY_INVALID_FILTER')
                    self.assertEqual(caught.exception.message, tr('python.session_query_tools.6871b527d571',
                        p0='availability', p1=tr('ui.common.list_separator').join(values)))
                    self.assertEqual(values, ['Original value {name}', 'Other value'])

    def test_time_parser_preserves_finite_timestamps_and_iso_dates(self):
        from harness.tools.builtin.session_query_tools import _parse_time
        for value, expected in [(0, 0), (1000.75, 1000), ('1000', 1000),
                                ('1e3', 1000), ('1970-01-01T00:00:01Z', 1000),
                                ('1970-01-01T01:00:01+01:00', 1000),
                                ('1970-01-01T00:00:01', 1000), ('', None), (None, None)]:
            self.assertEqual(_parse_time(value, 'time_from'), expected)
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                for value in (float('inf'), float('-inf'), float('nan')):
                    with self.assertRaises(ToolError) as caught:
                        _parse_time(value, 'time_from')
                    self.assertEqual(caught.exception.code, 'SESSION_QUERY_TOOL_INVALID_ARGUMENT')
                    self.assertEqual(caught.exception.message, tr('python.session_query_tools.09b88448ac64',
                        p0='time_from', p1=repr(value)))

    async def test_lineage_budget_reports_incomplete_only_when_nodes_are_omitted(self):
        from unittest.mock import patch
        for shape in ('siblings', 'chain'):
            for count in (1, 2, 3):
                with self.subTest(shape=shape, count=count), tempfile.TemporaryDirectory() as folder:
                    store = SessionStore(folder)
                    caller = store.create(cwd='/allowed')
                    target = store.create(cwd='/allowed', title='Original root 原文')
                    parent = target
                    for index in range(count):
                        child = store.create(cwd='/allowed', parent_session=parent.id,
                            title=f'Original child {index}')
                        if shape == 'chain':
                            parent = child
                    tool = next(tool for tool in build_session_query_tools(store)
                                if tool.schema.name == 'session_trace')
                    ctx = ToolRunContext('fixture', caller.id, '/allowed', 1, 1)
                    with patch('harness.tools.builtin.session_query_tools.MAX_LINEAGE_NODES', 2):
                        for language in ('en', 'zh-CN', 'en'):
                            with current_language.set(language):
                                result = tool.execute({'session_id': target.id}, ctx)
                                capped = count > 2
                                self.assertEqual(result.data['complete'], not capped)
                                self.assertEqual(result.data['descendantsCapped'], capped)
                                self.assertEqual(len(result.data['descendants']), min(count, 2))
                                self.assertIsNone(result.data['unresolvedParentId'])
                                self.assertFalse(result.data['ancestorBoundary'])
                                notice = tr('query.lineage_capped', limit=2)
                                self.assertEqual(notice in result.content[0].text, capped)
                                self.assertIn('Original root 原文', result.content[0].text)

    async def test_current_step_search_excludes_its_own_events_after_step_end(self):
        with tempfile.TemporaryDirectory() as folder:
            store = SessionStore(folder)
            session = store.create(cwd='/allowed')
            previous = await session.append('user/message', {'text':'needle Original history'}, surface_op=AppendOp())
            await session.append('step/start', {'turn':1, 'step':1})
            await session.append('user/message', {'text':'needle current step'}, surface_op=AppendOp())
            await session.append('step/end', {'turn':1, 'step':1, 'reason':'completed'})
            registry = ToolRegistry()
            for tool in build_session_query_tools(store):
                registry.register(tool)
            ctx = ToolRunContext('fixture', session.id, '/allowed', 1, 1)
            for language in ('en', 'zh-CN', 'en'):
                with current_language.set(language):
                    result = await registry.execute_call(ToolUseBlock('query-fixture', 'session_event_search',
                        {'query':'needle', 'seq_to':10000}), ctx)
                    self.assertFalse(result.is_error)
                    self.assertEqual([row['seq'] for row in result.data['hits']], [previous.seq])
                    self.assertIn('Original history', result.content[0].text)
                    self.assertNotIn('needle current step', result.content[0].text)

    async def test_current_step_surface_uses_only_events_before_its_boundary(self):
        with tempfile.TemporaryDirectory() as folder:
            store = SessionStore(folder)
            session = store.create(cwd='/allowed')
            caller = store.create(cwd='/allowed')
            original = await session.append('user/message', {'text':'needle Original 原文'}, surface_op=AppendOp())
            previous = await session.append('user/message', {'text':'needle Previous 摘要'},
                surface_op=ReplaceOp(original.seq, original.seq))
            await session.append('step/start', {'turn':1, 'step':1})
            current = await session.append('user/message', {'text':'needle Current 摘要'},
                surface_op=ReplaceOp(previous.seq, previous.seq))
            await session.append('step/end', {'turn':1, 'step':1, 'reason':'completed'})
            registry = ToolRegistry()
            for tool in build_session_query_tools(store):
                registry.register(tool)
            for language in ('en', 'zh-CN', 'en'):
                with current_language.set(language):
                    for caller_id, expected in (
                        (session.id, {'current':[previous.seq], 'shadowed':[original.seq]}),
                        (caller.id, {'current':[current.seq], 'shadowed':[original.seq, previous.seq]}),
                    ):
                        ctx = ToolRunContext('fixture', caller_id, '/allowed', 1, 1)
                        for surface, seqs in expected.items():
                            with self.subTest(language=language, caller=caller_id, surface=surface):
                                result = await registry.execute_call(ToolUseBlock('query-fixture', 'session_event_search',
                                    {'session_id':session.id, 'query':'needle', 'surfaces':[surface], 'seq_to':10000}), ctx)
                                self.assertFalse(result.is_error)
                                self.assertEqual([row['seq'] for row in result.data['hits']], seqs)
                                self.assertTrue(all(row['surface'] == surface for row in result.data['hits']))
