"""MCP 工作区能力发现的协议、授权、目录隔离与线程收尾。"""
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import threading
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx
import mcp.types as types
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from harness.core.language import current_language
from harness.execution.config import ExecutionConfig, ProjectConfig
from harness.execution.runtime import ExecutionRuntime
from harness.execution.scheduling import ExecutionGate
from harness.i18n import tr
from harness.mcp_server.server import create_http_app, create_server
from harness.tools.builtin import workspace_tools


class McpWorkspaceDependenciesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.project, self.other = self.root / 'project', self.root / 'other'
        self.project.mkdir()
        self.other.mkdir()
        (self.project / '.venv').mkdir()
        (self.other / 'node_modules').mkdir()
        for name in ('sample', 'disabled'):
            path = self.project / '.agents/skills' / name / 'SKILL.md'
            path.parent.mkdir(parents=True)
            path.write_text(f'---\nname: {name}\ndescription: Original skill 原文\n---\nInstructions\n', encoding='utf-8')
        self.config = ExecutionConfig(self.root / 'state', {
            'project': ProjectConfig(self.project, verification={'check': ('python', '-V')}),
            'hidden': ProjectConfig(self.other, principals=('other',))},
            skill_home=self.root / 'isolated-skills', language='en')
        self.runtime = ExecutionRuntime(self.config)
        self.runtime.esc_state.set_enabled('skill', 'disabled', False)
        self.addAsyncCleanup(self.runtime.close)
        for target, value in (('_node_runtimes', []), ('_python_packages', ([{'name': 'FixturePkg', 'version': '1'}], 1))):
            patcher = patch.object(workspace_tools, target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def test_no_argument_discovers_only_authorized_workspace_without_creating_a_conversation(self):
        result = await self.runtime.call('local', 'load_workspace_dependencies', {})
        self.assertFalse(result['is_error'], result)
        data = result['data']
        self.assertEqual(data['cwd'], str(self.project))
        self.assertEqual(data['project']['project_id'], 'project')
        self.assertFalse(data['project']['writable'])
        self.assertFalse(data['project']['commands'])
        self.assertEqual(data['project']['verification'], ['check'])
        self.assertIn('sample', [item['name'] for item in data['project']['skills']])
        self.assertNotIn('disabled', [item['name'] for item in data['project']['skills']])
        self.assertIn('load_workspace_dependencies', data['tools'])
        self.assertEqual(data['python']['packages'], [{'name': 'FixturePkg', 'version': '1'}])
        self.assertEqual(data['execution_environment']['filesystem_isolation'], 'host')
        self.assertIsNone(result['session_id'])
        self.assertEqual(self.runtime.store.db.execute('SELECT count(*) FROM sessions').fetchone()[0], 0)
        context = await self.runtime.call('local', 'harness_context', {})
        self.assertEqual(data['project'], context['data']['projects'][0])
        self.assertNotIn(str(self.other), json.dumps(data))

    async def test_multiple_projects_require_selection_and_session_identity_is_enforced(self):
        self.runtime.config = replace(self.config, projects={**self.config.projects,
            'second': ProjectConfig(self.other, writable=True)})
        session = self.runtime.store.open_session('local', 'second', 'Fixture', str(self.other))
        sid = session['session_id']
        with patch('harness.execution.runtime.collect_workspace_dependencies', wraps=workspace_tools.collect_workspace_dependencies) as probe:
            for args, code in (({}, 'execution/workspace-selection-required'),
                               ({'project_id': 'hidden'}, 'execution/project-denied'),
                               ({'project_id': 'second', 'session_id': sid}, 'execution/workspace-selection-invalid')):
                result = await self.runtime.call('local', 'load_workspace_dependencies', args)
                self.assertEqual(result['data']['error']['code'], code)
            probe.assert_not_called()
            for args in ({'project_id': 'second'}, {'session_id': sid}, {'project_id': 'project'}):
                result = await self.runtime.call('local', 'load_workspace_dependencies', {**args, 'include_packages': False})
                self.assertFalse(result['is_error'], result)
                expected = self.project if args.get('project_id') == 'project' else self.other
                self.assertEqual(result['data']['cwd'], str(expected))
                self.assertEqual(result['data']['project']['root'], str(expected))
                self.assertNotIn('packages', result['data']['python'])
            before = probe.call_count
            denied = await self.runtime.call('other', 'load_workspace_dependencies', {'session_id': sid})
            self.assertTrue(denied['is_error'])
            self.runtime.config = replace(self.runtime.config, projects={**self.runtime.config.projects,
                'second': ProjectConfig(self.project)})
            changed = await self.runtime.call('local', 'load_workspace_dependencies', {'session_id': sid})
            self.assertEqual(changed['data']['error']['code'], 'execution/project-changed')
            self.assertEqual(probe.call_count, before)

    async def test_invalid_arguments_are_rejected_before_probe_in_each_language(self):
        with patch('harness.execution.runtime.collect_workspace_dependencies') as probe:
            for language in ('en', 'zh-CN', 'en'):
                with current_language.set(language):
                    for args in ({'include_packages': 'false'}, {'include_packages': 1}, {'include_packages': None},
                                 {'cwd': str(self.other)}, {'project_id': ''}, {'session_id': 'wrong'}):
                        with self.subTest(language=language, args=args):
                            result = await self.runtime.call('local', 'load_workspace_dependencies', args)
                            self.assertTrue(result['is_error'], result)
                            self.assertEqual(any('\u4e00' <= c <= '\u9fff' for c in result['data']['error']['message']),
                                             language == 'zh-CN')
            probe.assert_not_called()

    async def test_probe_failure_preserves_diagnostic_and_never_returns_partial_success(self):
        with patch('harness.execution.runtime.collect_workspace_dependencies',
                   side_effect=RuntimeError('Original probe failure 原文')) as probe:
            for language in ('en', 'zh-CN', 'en'):
                with current_language.set(language):
                    result = await self.runtime.call('local', 'load_workspace_dependencies', {})
                    self.assertTrue(result['is_error'])
                    self.assertEqual(result['data']['error']['code'], 'execution/dependency-probe-failed')
                    self.assertEqual(result['data']['error']['message'],
                                     tr('python.workspace_tools.484acd7994ec', p0='Original probe failure 原文'))
                    self.assertNotIn('python', result['data'])
            self.assertEqual(probe.call_count, 3)

    async def test_waiting_probe_keeps_original_project_binding_and_rechecks_authorization(self):
        for replacement, code in ((ProjectConfig(self.other), 'execution/project-changed'),
                                  (ProjectConfig(self.project, principals=('other',)), 'execution/project-denied')):
            self.runtime.config = self.config
            gate = ExecutionGate()
            self.runtime._locks[str(self.project)] = gate
            with patch('harness.execution.runtime.collect_workspace_dependencies') as probe:
                async with gate.hold():
                    call = asyncio.create_task(self.runtime.call('local', 'load_workspace_dependencies', {}))
                    # 外层调用先登记所属任务，下一次调度才进入资源域等待。
                    await asyncio.sleep(0)
                    await asyncio.sleep(0)
                    self.runtime.config = replace(self.config, projects={**self.config.projects, 'project': replacement})
                result = await call
                self.assertEqual(result['data']['error']['code'], code)
                probe.assert_not_called()

    async def test_real_http_catalog_and_call_share_configured_language_and_structured_content(self):
        for language in ('en', 'zh-CN', 'en'):
            app = create_http_app(replace(self.config, language=language),
                runtime_holder={'runtime': self.runtime}, manage_runtime=False)
            outer = 'zh-CN' if language == 'en' else 'en'
            with current_language.set(outer):
                async with app.router.lifespan_context(app), httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1:8876',
                        headers={'Accept': 'application/json, text/event-stream'}) as client:
                    async def rpc(method, params):
                        response = await client.post('/mcp', json={'jsonrpc': '2.0', 'id': method,
                            'method': method, 'params': params})
                        self.assertEqual(response.status_code, 200, response.text)
                        value = response.json()
                        self.assertNotIn('error', value)
                        return value['result']
                    initialized = await rpc('initialize', {'protocolVersion': types.LATEST_PROTOCOL_VERSION,
                        'capabilities': {}, 'clientInfo': {'name': 'workspace-fixture', 'version': '1'}})
                    self.assertIn('load_workspace_dependencies', initialized['instructions'])
                    catalog = await rpc('tools/list', {})
                    tool = next(item for item in catalog['tools'] if item['name'] == 'load_workspace_dependencies')
                    self.assertTrue(tool['annotations']['readOnlyHint'])
                    self.assertTrue(tool['annotations']['idempotentHint'])
                    self.assertFalse(tool['annotations']['destructiveHint'])
                    self.assertFalse(tool['inputSchema']['additionalProperties'])
                    with current_language.set(language):
                        self.assertEqual(tool['description'], tr('execution.catalog.load_workspace_dependencies'))
                    result = await rpc('tools/call', {'name': 'load_workspace_dependencies', 'arguments': {}})
                    self.assertFalse(result['isError'], result)
                    self.assertEqual(json.loads(result['content'][0]['text']), result['structuredContent'])
                    data = result['structuredContent']['data']
                    self.assertEqual(data['cwd'], str(self.project))
                    self.assertEqual(data['python']['packages'][0]['name'], 'FixturePkg')
                    with current_language.set(language):
                        self.assertEqual(data['runtime_scope'], tr('workspace.runtime_scope'))
                self.assertEqual(current_language.get(), outer)

    async def test_stdio_cli_discovers_real_service_environment_and_selected_workspace(self):
        config = self.root / 'executor.json'
        config.write_text(json.dumps({'home': 'stdio-state', 'skill_home': 'stdio-skills', 'language': 'en',
            'projects': {'fixture': {'root': str(self.project)}}}), encoding='utf-8')
        server = StdioServerParameters(command=sys.executable,
            args=['-m', 'harness.mcp_server', 'serve', '--config', str(config), '--transport', 'stdio'],
            cwd=str(Path(__file__).resolve().parents[1]))
        async with stdio_client(server) as (reader, writer), ClientSession(reader, writer) as client:
            await client.initialize()
            catalog = await client.list_tools()
            self.assertIn('load_workspace_dependencies', [tool.name for tool in catalog.tools])
            result = await client.call_tool('load_workspace_dependencies', {'include_packages': False})
            self.assertFalse(result.is_error, result)
            data = result.structured_content['data']
            self.assertEqual(data['cwd'], str(self.project))
            self.assertEqual(Path(data['python']['executable']).resolve(), Path(sys.executable).resolve())
            self.assertEqual(data['python']['source'], 'harness_process')
            self.assertNotIn('packages', data['python'])
            self.assertIn(str(self.project / '.venv'), [item['path'] for item in data['workspace']['dependency_roots']])
            self.assertEqual(json.loads(result.content[0].text), result.structured_content)

    async def test_oauth_requires_project_read_scope_and_uses_token_principal(self):
        captured = {}
        def server(*args, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace()
        provider = SimpleNamespace(public_url='https://executor.example')
        with patch('harness.mcp_server.server.Server', side_effect=server):
            create_server(self.config, provider=provider, runtime_holder={'runtime': self.runtime}, manage_runtime=False)
        listing = await captured['on_list_tools'](None, None)
        tool = next(item for item in listing.tools if item.name == 'load_workspace_dependencies')
        self.assertEqual(tool.meta['securitySchemes'], [{'type': 'oauth2', 'scopes': ['project.read']}])
        params = types.CallToolRequestParams(name='load_workspace_dependencies', arguments={})
        with patch('harness.execution.runtime.collect_workspace_dependencies', wraps=workspace_tools.collect_workspace_dependencies) as probe:
            for token in (None, SimpleNamespace(subject='local', scopes=['command.run'])):
                with patch('harness.mcp_server.server.get_access_token', return_value=token):
                    denied = await captured['on_call_tool'](None, params)
                    self.assertTrue(denied.is_error)
                    self.assertIn('project.read', denied.meta['mcp/www_authenticate'])
            probe.assert_not_called()
            with patch('harness.mcp_server.server.get_access_token',
                       return_value=SimpleNamespace(subject='other', scopes=['project.read'])):
                allowed = await captured['on_call_tool'](None, params)
            self.assertFalse(allowed.is_error)
            self.assertEqual(allowed.structured_content['data']['project']['project_id'], 'hidden')

    async def test_disconnect_and_repeated_close_cancellation_wait_for_original_probe(self):
        entered, release = threading.Event(), threading.Event()
        def probe(**kwargs):
            entered.set()
            if not release.wait(5):
                raise TimeoutError('Fixture was not released')
            return {'cwd': str(kwargs['cwd'])}
        with patch('harness.execution.runtime.collect_workspace_dependencies', side_effect=probe) as collect:
            call = asyncio.create_task(self.runtime.call('local', 'load_workspace_dependencies', {}))
            closing = None
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                call.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await call
                closing = asyncio.create_task(self.runtime.close())
                await asyncio.sleep(0)
                closing.cancel()
                await asyncio.sleep(0)
                closing.cancel()
                await asyncio.sleep(0)
                self.assertFalse(closing.done())
            finally:
                release.set()
                if closing is not None:
                    with self.assertRaises(asyncio.CancelledError):
                        await closing
                await asyncio.gather(call, return_exceptions=True)
            collect.assert_called_once_with(include_packages=True, cwd=self.project)
