"""可信工具范围同时约束目录与实际调用，复用原执行设施及动态权限。"""
import asyncio
import json
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path
from unittest.mock import Mock

from harness import Harness, HarnessConfig
from harness.core.errors import ToolError
from harness.core.language import current_language
from harness.i18n import tr
from harness.llm.types import ToolUseBlock
from harness.tools.registry import ToolDefinition, ToolRegistry, ToolRunContext
from tests.agent_configuration_fixture import RecordingTransport


class ToolScopeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.registry = ToolRegistry()
        self.executed = []
        for name in ('inspect', 'execute'):
            self.registry.register(ToolDefinition(
                {'name': name, 'description': 'Original description', 'parameters': {'type': 'object'}},
                lambda args, ctx, name=name: self.executed.append(name) or name, effect='read'))
        self.context = ToolRunContext(call_id='', session_id='fixture', cwd=None, turn=1, step=1)

    async def call(self, registry, name):
        return await registry.execute_call(ToolUseBlock(id='call', name=name, input={}), self.context)

    async def test_scoped_catalog_and_actual_execution_reject_unadvertised_tool(self):
        scoped = self.registry.restricted(['inspect'])
        self.assertEqual(scoped.names(), ['inspect'])
        self.assertEqual([schema.name for schema in scoped.schemas()], ['inspect'])
        self.assertTrue((await self.call(scoped, 'execute')).is_error)
        self.assertEqual(self.executed, [])
        self.assertFalse((await self.call(scoped, 'inspect')).is_error)
        self.assertEqual(self.executed, ['inspect'])
        self.assertEqual(self.registry.names(), ['execute', 'inspect'])

    async def test_nested_scope_never_broadens_parent_or_global_policy(self):
        parent = self.registry.restricted(['inspect'])
        child = parent.restricted(['inspect', 'execute'])
        self.assertEqual(child.names(), ['inspect'])
        self.assertTrue((await self.call(child, 'execute')).is_error)
        global_policy = ToolRegistry(allow=['inspect'])
        global_policy.register(self.registry.get('inspect'))
        global_policy.register(self.registry.get('execute'))
        self.assertEqual(global_policy.restricted(['inspect', 'execute']).names(), ['inspect'])

    async def test_newly_added_host_guard_is_not_bypassed_by_preexisting_view(self):
        scoped = self.registry.restricted(['inspect'])
        guarded = Mock(return_value='Original host refusal')
        self.registry.add_guard(guarded)
        self.assertTrue((await self.call(scoped, 'inspect')).is_error)
        self.assertEqual(guarded.call_count, 1)
        self.assertEqual(self.executed, [])

    async def test_replacing_definition_during_permission_wait_refuses_stale_execution(self):
        scoped = self.registry.restricted(['inspect'])
        entered, release = asyncio.Event(), asyncio.Event()

        async def guard(*args):
            entered.set()
            await release.wait()

        self.registry.add_guard(guard)
        call = asyncio.create_task(self.call(scoped, 'inspect'))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            self.registry.register(ToolDefinition(
                {'name': 'inspect', 'description': 'Replacement', 'parameters': {'type': 'object'}},
                lambda args, ctx: self.executed.append('replacement'), effect='read'), replace=True)
        finally:
            release.set()
        result = await asyncio.wait_for(call, 2)
        self.assertTrue(result.is_error)
        self.assertEqual(self.executed, [])

    async def test_new_registrations_and_removals_follow_live_host_catalog(self):
        scoped = self.registry.restricted(['later'])
        self.assertEqual(scoped.names(), [])
        self.registry.register(ToolDefinition(
            {'name': 'later', 'description': 'Later', 'parameters': {'type': 'object'}},
            lambda args, ctx: 'original', effect='read'))
        self.assertEqual(scoped.names(), ['later'])
        self.assertFalse((await self.call(scoped, 'later')).is_error)
        self.registry.unregister('later')
        self.assertEqual(scoped.names(), [])
        self.assertTrue((await self.call(scoped, 'later')).is_error)

    async def test_empty_scope_is_explicit_denial_and_error_candidates_do_not_leak_parent(self):
        scoped = self.registry.restricted([])
        self.assertEqual(scoped.schemas(), [])
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                result = await self.call(scoped, 'missing')
                self.assertTrue(result.is_error)
                self.assertNotIn('inspect', result.content[0].text)
                self.assertNotIn('execute', result.content[0].text)

    async def test_runtime_services_and_result_observers_remain_owned_by_host(self):
        scoped = self.registry.restricted(['inspect'])
        observed = []
        self.registry.add_result_observer(lambda call, ctx, result: observed.append(call.name))
        self.assertFalse((await self.call(scoped, 'inspect')).is_error)
        self.assertEqual(observed, ['inspect'])

    async def test_parent_and_view_serialize_writes_to_same_declared_path(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'shared.txt'
            first_entered, second_attempted, second_entered, release = (asyncio.Event() for _ in range(4))

            async def write(args, ctx):
                if ctx.call_id == 'first':
                    first_entered.set()
                    await release.wait()
                else:
                    second_entered.set()
                return 'recorded'

            def before(args, ctx):
                if ctx.call_id == 'second':
                    second_attempted.set()

            scoped = self.registry.restricted(['write'])
            self.registry.add_pre_hook(before)
            self.registry.register(ToolDefinition(
                {'name': 'write', 'description': 'Fixture write', 'parameters': {'type': 'object'}},
                write, effect='workspace_write', write_paths=lambda args, ctx: [target]))
            first = asyncio.create_task(self.registry.execute_call(
                ToolUseBlock(id='first', name='write', input={}), self.context))
            second = None
            try:
                await asyncio.wait_for(first_entered.wait(), 2)
                second = asyncio.create_task(scoped.execute_call(
                    ToolUseBlock(id='second', name='write', input={}), self.context))
                await asyncio.wait_for(second_attempted.wait(), 2)
                self.assertFalse(second_entered.is_set())
            finally:
                release.set()
                results = await asyncio.gather(*[task for task in (first, second) if task is not None])
            self.assertTrue(second_entered.is_set())
            self.assertTrue(all(not result.is_error for result in results))

    async def test_actual_model_consumer_and_hallucinated_call_use_same_scope(self):
        class ScopedTransport(RecordingTransport):
            async def post_stream(self, url, *, headers, body, timeout):
                self.requests.append(json.loads(body))
                delta = ({'tool_calls': [{'index': 0, 'id': 'forbidden', 'type': 'function',
                                         'function': {'name': 'execute', 'arguments': '{}'}}]}
                         if len(self.requests) == 1 else {'content': 'Scoped response'})
                yield ('data: ' + json.dumps({'choices': [{'delta': delta, 'finish_reason':
                        'tool_calls' if 'tool_calls' in delta else 'stop'}]}) + '\n\n').encode('utf-8')
                yield b'data: [DONE]\n\n'

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            flags = {item.name: False for item in fields(HarnessConfig) if item.name.startswith('enable_')}
            transport = ScopedTransport()
            host = Harness(HarnessConfig(api_key='fixture-only', home=root / 'state', cwd=root,
                model='fixture-model', language='en', tool_schema_mode='all', max_steps=2, max_retries=0, **flags),
                transport=transport, codex_gateway_home=root / 'gateway')
            try:
                for name in ('inspect', 'execute'):
                    host.tools.register(self.registry.get(name))
                session = host.create_session()
                host._agent_for(session).tools = host.tools.restricted(['inspect'])
                result = await host.run_async(session, 'Use the configured tools')
                self.assertEqual(result.reason, 'completed')
                self.assertEqual(self.executed, [])
                self.assertEqual(len(transport.requests), 2)
                for request in transport.requests:
                    self.assertEqual([item['function']['name'] for item in request['tools']], ['inspect'])
                ordinary = host.create_session()
                names = [schema.name for schema in host._agent_for(ordinary)._eligible_tool_schemas()]
                self.assertIn('execute', names)
            finally:
                await host.close()

    def test_scope_input_validation_and_snapshot_are_bound_to_current_language(self):
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                for invalid in (None, 'inspect', b'inspect', [None], [''], ['\ud800'], ['x\0y']):
                    with self.assertRaises(ToolError) as caught:
                        self.registry.restricted(invalid)
                    self.assertEqual(caught.exception.message, tr('tool.scope.invalid'))
                with self.assertRaises(ToolError):
                    self.registry.restricted('inspect' for _ in range(4097))
        names = ['inspect']
        scoped = self.registry.restricted(names)
        names.append('execute')
        self.assertEqual(scoped.names(), ['inspect'])
