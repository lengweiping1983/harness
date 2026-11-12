"""本机匿名模型通道的范围、资料绑定、流式错误和子进程归属回归。"""
import asyncio
from contextlib import aclosing
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from harness.core.errors import LlmError
from harness.core.language import current_language
from harness.llm.adapter import OpenRouterAdapter
from harness.llm.capabilities import CapabilityRegistry
from harness.llm.routes import ModelRoutes
from harness.llm.providers import ProviderConnections
from harness.llm.types import AssistantMessage, LlmCallConfig, StreamEvent, ToolSchema, ToolUseBlock, UserMessage
from harness.llm.opencode_local import BASE_URL, OpenCodeLocalTransport, executable_digest, load_profile, bootstrap_profile, refresh_profile


class OpenCodeLocalTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.profile = Path(self.directory.name)/'profile.json'
        self.executable = Path(sys.executable)
        self.data = {'version':1, 'executable_sha256':executable_digest(self.executable),
            'system':[{'role':'system','content':'official context'}],
            'headers':{'user-agent':'opencode/test','x-opencode-client':'desktop','x-opencode-project':'fixture'}}
        self.save()

    def save(self):
        self.profile.write_text(json.dumps(self.data),encoding='utf-8')

    def transport(self):
        return OpenCodeLocalTransport(str(self.executable),str(self.profile))

    async def test_profile_rejects_changed_executable_and_preserves_file(self):
        self.data['executable_sha256']='0'*64
        self.save()
        original=self.profile.read_bytes()
        with self.assertRaises(LlmError):self.transport()
        self.assertEqual(self.profile.read_bytes(),original)

    async def test_profile_diagnostics_distinguish_local_failures_without_spawning(self):
        """同一入口随语言切换报告真实原因，保留资料且不泄露正文。"""
        from harness.i18n import tr
        from harness.llm.opencode_local import configured_transport
        for reason in ('missing', 'invalid', 'executableUnavailable', 'executableChanged'):
            self.save()
            executable = self.executable
            if reason == 'missing':
                self.profile.unlink()
            elif reason == 'invalid':
                self.profile.write_bytes(b'private invalid content')
            elif reason == 'executableUnavailable':
                executable = self.profile.with_name('missing-runtime')
            else:
                self.data['executable_sha256'] = '0' * 64
                self.save()
            original = self.profile.read_bytes() if self.profile.exists() else None
            with patch.dict(os.environ, {'HARNESS_OPENCODE_EXECUTABLE':str(executable),
                    'HARNESS_OPENCODE_PROFILE':str(self.profile)}), \
                    patch('harness.llm.opencode_local.asyncio.create_subprocess_exec') as spawn:
                for language in ('en', 'zh-CN', 'en'):
                    with self.subTest(reason=reason, language=language), current_language.set(language):
                        with self.assertRaises(LlmError) as caught:
                            configured_transport(required=True)
                        error = caught.exception
                        self.assertEqual(error.details['reason'], reason)
                        self.assertFalse(error.transient)
                        copy = error.details['copy']
                        self.assertEqual(error.message, tr(copy['key'], **copy['params']))
                        self.assertNotIn('private invalid content', str(error.to_dict()))
                        self.assertEqual(self.profile.read_bytes() if self.profile.exists() else None, original)
                spawn.assert_not_called()

    async def test_refresh_validates_then_backs_up_original_and_reloads_transport(self):
        """显式刷新保留原始字节，替换后的资料可经真实配置入口加载。"""
        from harness.llm.opencode_local import configured_transport
        backup = self.profile.with_suffix('.backup')
        self.profile.write_bytes(b'original invalid profile')
        async def capture(executable, destination):
            self.assertEqual(self.profile.read_bytes(), b'original invalid profile')
            self.assertFalse(backup.exists())
            destination.write_text(json.dumps({**self.data, 'executable':str(executable)}), encoding='utf-8')
        with patch('harness.llm.opencode_local.bootstrap_profile', side_effect=capture) as bootstrap:
            await refresh_profile(self.executable, self.profile, backup)
        bootstrap.assert_awaited_once()
        self.assertEqual(backup.read_bytes(), b'original invalid profile')
        if os.name != 'nt':
            self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        with patch.dict(os.environ, {'HARNESS_OPENCODE_EXECUTABLE':'', 'HARNESS_OPENCODE_PROFILE':str(self.profile)}):
            self.assertIsInstance(configured_transport(required=True), OpenCodeLocalTransport)

    async def test_refresh_failure_cancel_and_invalid_capture_preserve_original(self):
        """捕获失败、取消和无效新资料均不创建备份或改写已有资料。"""
        original = self.profile.read_bytes()
        backup = self.profile.with_suffix('.backup')
        for failure in ('failed', 'cancelled', 'invalid'):
            async def capture(executable, destination):
                if failure == 'failed':
                    raise LlmError('fixture', code='llm/opencode-local-bootstrap')
                if failure == 'cancelled':
                    raise asyncio.CancelledError
                destination.write_bytes(b'invalid')
            with self.subTest(failure=failure), patch('harness.llm.opencode_local.bootstrap_profile', side_effect=capture):
                with self.assertRaises((LlmError, asyncio.CancelledError)):
                    await refresh_profile(self.executable, self.profile, backup)
                self.assertEqual(self.profile.read_bytes(), original)
                self.assertFalse(backup.exists())

    async def test_refresh_refuses_existing_backup_and_changed_profile(self):
        """备份冲突不启动捕获，准备期间原件变化则拒绝覆盖。"""
        backup = self.profile.with_suffix('.backup')
        original = self.profile.read_bytes()
        backup.write_bytes(b'existing backup')
        with patch('harness.llm.opencode_local.bootstrap_profile') as capture:
            with self.assertRaises(LlmError):
                await refresh_profile(self.executable, self.profile, backup)
            capture.assert_not_called()
        self.assertEqual(backup.read_bytes(), b'existing backup')
        self.assertEqual(self.profile.read_bytes(), original)
        backup.unlink()
        async def changed(executable, destination):
            destination.write_bytes(original)
            self.profile.write_bytes(b'concurrent change')
        with patch('harness.llm.opencode_local.bootstrap_profile', side_effect=changed):
            with self.assertRaises(LlmError) as caught:
                await refresh_profile(self.executable, self.profile, backup)
            self.assertEqual(caught.exception.code, 'llm/opencode-local-refresh')
        self.assertEqual(self.profile.read_bytes(), b'concurrent change')
        self.assertFalse(backup.exists())

    async def test_profile_read_permission_error_retains_errno_and_resource(self):
        """无法读取资料时报告读取权限问题，不误报版本变化。"""
        original = self.profile.read_bytes()
        with patch('harness.llm.opencode_local._profile_json', side_effect=PermissionError(13, 'fixture')):
            for language in ('en', 'zh-CN', 'en'):
                with current_language.set(language), self.assertRaises(LlmError) as caught:
                    load_profile(self.executable, self.profile)
                self.assertEqual(caught.exception.details['reason'], 'unreadable')
                self.assertEqual(caught.exception.details['errno'], 13)
                self.assertEqual(caught.exception.details['copy']['key'], 'ui.llm.opencodeLocalProfileUnreadable')
        self.assertEqual(self.profile.read_bytes(), original)

    async def test_refresh_replace_failure_preserves_original_and_backup(self):
        """最终替换被拒绝时保留可核对原件与备份，不重放捕获。"""
        backup = self.profile.with_suffix('.backup')
        original = self.profile.read_bytes()
        async def capture(executable, destination):
            destination.write_bytes(original)
        with patch('harness.llm.opencode_local.bootstrap_profile', side_effect=capture) as capture_mock, \
                patch('harness.llm.opencode_local.os.replace', side_effect=PermissionError(13, 'fixture')):
            with self.assertRaises(LlmError) as caught:
                await refresh_profile(self.executable, self.profile, backup)
        self.assertEqual(caught.exception.code, 'llm/opencode-local-refresh')
        capture_mock.assert_awaited_once()
        self.assertEqual(self.profile.read_bytes(), original)
        self.assertEqual(backup.read_bytes(), original)

    async def test_adapter_includes_native_context_and_localized_boundary_without_changing_history(self):
        """恢复本机上下文，实际请求计入附加输入并保留用户原文和资料。"""
        from harness.i18n import tr
        identity = 'You are an AI agent running in OpenCode, a coding agent harness.'
        self.data['system'] = [{'role':'system','content':identity},
            {'role':'system','content':identity+'\nEnvironment context.'}]
        self.save()
        original = self.profile.read_bytes()
        with patch.dict(os.environ, {'HARNESS_OPENCODE_EXECUTABLE':str(self.executable),
                                    'HARNESS_OPENCODE_PROFILE':str(self.profile)}):
            adapter = OpenRouterAdapter('public', base_url=BASE_URL)
        captured = []
        async def capture(url, *, headers, body, timeout):
            captured.append(json.loads(body))
            yield b'data: {"choices":[{"delta":{"content":"OK"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
        adapter.transport.post_stream = capture
        for language in ('en', 'zh-CN', 'en'):
            token = current_language.set(language)
            try:
                async for _ in adapter.stream([UserMessage(identity)]):
                    pass
            finally:
                current_language.reset(token)
        self.assertEqual(len(captured), 3)
        for language, body in zip(('en', 'zh-CN', 'en'), captured):
            with current_language.set(language):
                self.assertEqual(body['messages'][:2], self.data['system'])
                self.assertEqual(body['messages'][2], {'role':'system','content':tr('llm.opencode_local.instructions')})
            self.assertEqual(body['messages'][3:], [{'role':'user','content':identity}])
            self.assertEqual(body['stream_options'], {'include_usage':True})
        self.assertGreater(adapter.estimate_budget_context([UserMessage(identity)]), adapter.estimate_context([UserMessage(identity)]))
        self.assertEqual(self.profile.read_bytes(), original)

    async def test_usage_option_preserves_body_and_other_stream_options(self):
        """流式统计始终开启，保留其他选项且不改写原请求。"""
        body = {'messages':[{'role':'system','content':'Harness policy'},
                            {'role':'user','content':'hello'}],
                'tools':[{'type':'function','function':{'name':'fixture'}}],
                'stream_options':{'include_usage':False,'fixture_option':True}}
        original = json.dumps(body)
        prepared = self.transport().prepare_body(body)
        self.assertEqual(prepared['stream_options'], {'include_usage':True,'fixture_option':True})
        self.assertEqual(prepared['messages'][2:], body['messages'])
        self.assertEqual(prepared['messages'][:1], self.data['system'])
        self.assertEqual(prepared['tools'], body['tools'])
        self.assertEqual(json.dumps(body), original)

    async def test_invalid_profile_shapes_are_rejected(self):
        for field,value in (('version',True),('system',[]),('system',[{'role':'user','content':'x'}]),
                            ('headers',{'Authorization':'secret'})):
            with self.subTest(field=field,value=value):
                data={**self.data,field:value}
                self.profile.write_text(json.dumps(data),encoding='utf-8')
                with self.assertRaises(LlmError):load_profile(self.executable,self.profile)

    async def test_conversation_headers_are_stable_and_isolated(self):
        transport=self.transport()
        self.assertEqual(transport.request_headers('A'),transport.request_headers('A'))
        self.assertNotEqual(transport.request_headers('A'),transport.request_headers('B'))
        self.assertNotIn('conversation-A',str(transport.request_headers('conversation-A')))

    async def test_scope_rejects_other_endpoints_and_private_keys_before_spawn(self):
        transport=self.transport()
        for url,key in ((BASE_URL+'/chat/completions','private'),('https://other.test/chat/completions','public')):
            with self.assertRaises(LlmError):
                async for _ in transport.post_stream(url,headers={'Authorization':'Bearer '+key},body=b'{}',timeout=1):pass

    async def test_invalid_helper_input_rejected_before_spawn_in_current_language(self):
        from harness.i18n import tr
        cases = [(b'{}', value) for value in (True, 0, -1, '1', float('nan'), float('inf'))]
        cases += [(value, 1) for value in (b'[]', b'null', b'invalid', b'{"value":NaN}',
                    ('{"value":' + '[' * 1500 + '0' + ']' * 1500 + '}').encode(),
                    b'{"value":"\\ud800"}')]
        transport = self.transport()
        with patch('harness.llm.opencode_local.asyncio.create_subprocess_exec') as spawn:
            for language in ('en', 'zh-CN', 'en'):
                with current_language.set(language):
                    for body, timeout in cases:
                        with self.subTest(language=language, body=body[:40], timeout=timeout):
                            with self.assertRaises(LlmError) as error:
                                async for _ in transport.post_stream(BASE_URL+'/chat/completions',
                                    headers={'Authorization':'Bearer public'}, body=body, timeout=timeout):pass
                            self.assertEqual(error.exception.code, 'llm/opencode-local-runtime')
                            self.assertEqual(error.exception.message, tr('llm.opencode_local.runtime_failed'))
            self.assertEqual(spawn.call_count, 0)

    async def test_explicit_env_selects_only_public_official_endpoint(self):
        with patch.dict(os.environ,{'HARNESS_OPENCODE_EXECUTABLE':str(self.executable),
                                    'HARNESS_OPENCODE_PROFILE':str(self.profile)}):
            self.assertIsInstance(OpenRouterAdapter('public',base_url=BASE_URL).transport,OpenCodeLocalTransport)
            self.assertNotIsInstance(OpenRouterAdapter('private',base_url=BASE_URL).transport,OpenCodeLocalTransport)
            self.assertNotIsInstance(OpenRouterAdapter('public',base_url='https://example.test').transport,OpenCodeLocalTransport)

    async def test_prepared_default_profile_selects_native_transport_without_env(self):
        home=Path(self.directory.name)
        destination=home/'.harness/opencode-local/profile.json'
        destination.parent.mkdir(parents=True)
        destination.write_text(json.dumps({**self.data,'executable':str(self.executable)}),encoding='utf-8')
        with patch.dict(os.environ,{},clear=True), patch('harness.llm.opencode_local.Path.home',return_value=home):
            adapter=OpenRouterAdapter('public',base_url=BASE_URL)
            self.assertIsInstance(adapter.transport,OpenCodeLocalTransport)
            messages=[]
            baseline=adapter.estimate_context(messages)
            self.assertGreater(adapter.estimate_budget_context(messages),baseline)

    def local_provider(self, **changes):
        """设置页保存的真实供应商结构，验证经过模型身份分发的本机入口。"""
        connection = {'id':'local-endpoint', 'name':'Local', 'base_url':BASE_URL, 'enabled':True,
            'auth':{'type':'opencode_local','env':{}}, 'keys':[],
            'models':[{'id':'free','model':'deepseek-v4.1-flash-free','name':'Free','enabled':True}]}
        connection.update(changes)
        return {'id':'local','type':'opencode-local','name':'OpenCode Local','connections':[connection]}

    async def test_settings_local_auth_requires_profile_before_any_network_request(self):
        """本机认证不能在资料缺失时静默降级，英中英错误与租约保持正确。"""
        from harness.i18n import tr
        store = ProviderConnections(Path(self.directory.name)/'providers')
        store.save([self.local_provider()], store.revision)
        adapter = OpenRouterAdapter('unused', base_url='https://example.invalid', capabilities=CapabilityRegistry(discover=False))
        adapter.provider_connections = store
        with patch.dict(os.environ, {}, clear=True), patch('harness.llm.opencode_local.Path.home', return_value=Path(self.directory.name)), \
                patch('harness.llm.transport.UrllibTransport.post_stream', side_effect=AssertionError('HTTP fallback')), \
                patch('harness.llm.provider_runtime.bridge', side_effect=AssertionError('runtime fallback')):
            for language in ('en', 'zh-CN', 'en'):
                with current_language.set(language):
                    with self.assertRaises(LlmError) as caught:
                        async for _ in adapter.stream([UserMessage('hello')], config=LlmCallConfig(model='harness/local/local-endpoint/free')):
                            pass
                    self.assertEqual(caught.exception.message, tr('ui.llm.opencodeLocalProfileMissing',
                        profile=str(Path(self.directory.name)/'.harness/opencode-local/profile.json')))
                    self.assertEqual(caught.exception.details['reason'], 'missing')
                self.assertEqual(caught.exception.code, 'llm/opencode-local-profile')
                self.assertFalse(store.leases)

    async def test_settings_local_auth_reaches_prepared_transport_and_preserves_timeout(self):
        """真实设置分发传递模型、正文、用量、对话身份及宿主网络超时。"""
        store = ProviderConnections(Path(self.directory.name)/'providers')
        store.save([self.local_provider()], store.revision)
        adapter = OpenRouterAdapter('unused', base_url='https://example.invalid', network_timeout=17, capabilities=CapabilityRegistry(discover=False))
        adapter.provider_connections = store
        captured = []
        async def capture(transport, url, *, headers, body, timeout):
            captured.append((transport, url, headers, json.loads(body), timeout))
            yield b'data: {"choices":[{"delta":{"content":"OK"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
        with patch.dict(os.environ, {'HARNESS_OPENCODE_EXECUTABLE':str(self.executable), 'HARNESS_OPENCODE_PROFILE':str(self.profile)}), \
                patch.object(OpenCodeLocalTransport, 'post_stream', capture):
            for identity in ('A', 'A', 'B'):
                events = [event async for event in adapter.stream([UserMessage('Harness 原文')],
                    config=LlmCallConfig(model='harness/local/local-endpoint/free'), cache_session_id=identity)]
                self.assertEqual(''.join(event.text for event in events if event.kind == 'text'), 'OK')
        for transport, url, headers, body, timeout in captured:
            self.assertIsInstance(transport, OpenCodeLocalTransport)
            self.assertEqual(url, BASE_URL+'/chat/completions')
            self.assertEqual(headers['Authorization'], 'Bearer public')
            self.assertEqual(body['model'], 'deepseek-v4.1-flash-free')
            self.assertEqual(body['messages'][:1], self.data['system'])
            self.assertEqual(body['messages'][2:], [{'role':'user','content':'Harness 原文'}])
            self.assertEqual(body['stream_options']['include_usage'], True)
            self.assertEqual(timeout, 17)
        self.assertEqual(captured[0][2]['X-Harness-Conversation'], captured[1][2]['X-Harness-Conversation'])
        self.assertNotEqual(captured[0][2]['X-Harness-Conversation'], captured[2][2]['X-Harness-Conversation'])
        self.assertFalse(store.leases)

    async def test_tool_alias_roundtrip_preserves_history_schema_and_fragmented_response(self):
        """只修改本机协议名称，分片聚合后还原实际工具，历史及其他参数不变。"""
        tools = [ToolSchema('spawn_agent', 'Harness 原文', {'type':'object','properties':{'task':{'type':'string'}}}),
                 ToolSchema('read', 'Read', {'type':'object'})]
        messages = [UserMessage('spawn_agent 原文'), AssistantMessage(content=[ToolUseBlock('old', 'spawn_agent', {'task':'old'})])]
        captured = []
        traces = []
        transport = self.transport()
        async def capture(url, *, headers, body, timeout):
            captured.append(json.loads(body))
            yield b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"new","function":{"name":"sub","arguments":"{\\"task\\":"}}]}}]}\n\n'
            yield b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"name":"agent","arguments":"\\"new\\"}"}}]},"finish_reason":"tool_calls"}]}\n\ndata: [DONE]\n\n'
        transport.post_stream = capture
        adapter = OpenRouterAdapter('public', base_url=BASE_URL, transport=transport)
        async def trace(value):
            traces.append(value)
            return 'fixture-trace'
        events = [event async for event in adapter.stream(messages, tools=tools, on_request=trace)]
        self.assertEqual([event.tool_use for event in events if event.kind=='tool_use'], [ToolUseBlock('new','spawn_agent',{'task':'new'})])
        body = captured[0]
        started = next(item for item in traces if item['phase']=='started')
        self.assertEqual(json.loads(started['body']), body)
        self.assertGreaterEqual(started['estimated_input_tokens'], transport.context_tokens)
        self.assertEqual([tool['function']['name'] for tool in body['tools']], ['subagent', 'read'])
        self.assertEqual(body['tools'][0]['function']['description'], 'Harness 原文')
        self.assertEqual(body['tools'][0]['function']['parameters'], tools[0].parameters)
        self.assertEqual(body['messages'][-1]['tool_calls'][0]['function']['name'], 'subagent')
        self.assertEqual(messages[1].content[0].name, 'spawn_agent')
        self.assertEqual(tools[0].name, 'spawn_agent')
        self.assertEqual(body['messages'][-2]['content'], 'spawn_agent 原文')
        original = StreamEvent(kind='tool_use',tool_use=ToolUseBlock('external','subagent',{}))
        self.assertIs(transport.restore_event(original, [tools[1]]), original)

    async def test_tool_alias_collision_is_rejected_before_request(self):
        """真实同名外部工具不能被本机映射错误地分发给spawn_agent。"""
        tools = [ToolSchema(name, 'Original', {'type':'object'}) for name in ('spawn_agent','subagent')]
        adapter = OpenRouterAdapter('public', base_url=BASE_URL, transport=self.transport())
        with self.assertRaises(LlmError) as caught:
            async for _ in adapter.stream([UserMessage('hello')], tools=tools):pass
        self.assertEqual(caught.exception.code, 'llm/opencode-local-tools')

    async def test_settings_local_auth_rejects_changed_endpoint_without_bridge_fallback(self):
        store = ProviderConnections(Path(self.directory.name)/'providers')
        store.save([self.local_provider(base_url='https://example.invalid/v1')], store.revision)
        adapter = OpenRouterAdapter('unused', base_url='https://example.invalid', capabilities=CapabilityRegistry(discover=False))
        adapter.provider_connections = store
        with patch('harness.llm.provider_runtime.bridge', side_effect=AssertionError('runtime fallback')):
            with self.assertRaises(LlmError) as caught:
                async for _ in adapter.stream([UserMessage('hello')], config=LlmCallConfig(model='harness/local/local-endpoint/free')):
                    pass
        self.assertEqual(caught.exception.code, 'llm/opencode-local-scope')
        self.assertFalse(store.leases)

    async def test_cancel_before_headers_waits_for_process_exit(self):
        async def consume(transport):
            stream=transport.post_stream(BASE_URL+'/chat/completions',headers={'Authorization':'Bearer public'},body=b'{}',timeout=10)
            task=asyncio.create_task(anext(stream))
            await asyncio.sleep(.1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
            await stream.aclose()
        await self.run_child('import sys,time;sys.stdin.read();time.sleep(30)',consume)

    async def test_cancel_during_startup_owns_late_process(self):
        entered,release=asyncio.Event(),asyncio.Event()
        original=asyncio.create_subprocess_exec
        children=[]
        async def delayed(*args,**kwargs):
            process=await original(sys.executable,'-u','-c','import time;time.sleep(30)',**kwargs)
            children.append(process)
            entered.set()
            await release.wait()
            return process
        with patch('harness.llm.opencode_local.asyncio.create_subprocess_exec',side_effect=delayed):
            stream=self.transport().post_stream(BASE_URL+'/chat/completions',headers={'Authorization':'Bearer public'},body=b'{}',timeout=10)
            task=asyncio.create_task(anext(stream))
            try:
                await asyncio.wait_for(entered.wait(),2)
                task.cancel()
                await asyncio.sleep(0)
                self.assertFalse(task.done())
            finally:
                release.set()
            with self.assertRaises(asyncio.CancelledError):await task
            await stream.aclose()
        self.assertTrue(all(process.returncode is not None for process in children))

    async def test_start_deadline_owns_late_handle_and_reclaims_before_timeout_returns(self):
        from harness.llm.opencode_local import _start_process, _stop_stream_process
        entered, release = asyncio.Event(), asyncio.Event()
        original = asyncio.create_subprocess_exec
        children = []
        async def delayed(*args, **kwargs):
            process = await original(sys.executable, '-c', 'import time;time.sleep(30)', **kwargs)
            children.append(process);entered.set();await release.wait();return process
        with patch('harness.llm.opencode_local.asyncio.create_subprocess_exec', side_effect=delayed):
            task = asyncio.create_task(_start_process('fixture', stop=_stop_stream_process,
                start_timeout=.02, stdout=asyncio.subprocess.DEVNULL))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                await asyncio.sleep(.05)
                self.assertFalse(task.done())
                self.assertIsNone(children[0].returncode)
                release.set()
                with self.assertRaises(TimeoutError):await task
                self.assertIsNotNone(children[0].returncode)
            finally:
                release.set();await asyncio.gather(task, return_exceptions=True)
                for child in children:await _stop_stream_process(child)

    async def test_invalid_native_profile_releases_route_lease(self):
        adapter=OpenRouterAdapter('fixture',base_url='https://example.test')
        adapter.route_pool=ModelRoutes([{'id':'native','model':'fixture','base_url':BASE_URL,
            'keys':[{'id':'public','value':'public'}]}])
        with patch.dict(os.environ,{'HARNESS_OPENCODE_EXECUTABLE':str(self.executable),
                                    'HARNESS_OPENCODE_PROFILE':str(self.profile.with_name('missing.json'))}):
            with self.assertRaises(LlmError):
                async for _ in adapter.stream([UserMessage('hello')]):pass
        self.assertFalse(adapter.route_pool.in_use)
        self.assertFalse(adapter.route_pool.leases)

    async def run_child(self,script,consumer):
        """真实 Python 子进程替代 Bun，只控制供应商响应，不访问网络。"""
        original=asyncio.create_subprocess_exec
        children=[]
        async def spawn(*args,**kwargs):
            self.assertEqual(kwargs['env']['BUN_BE_BUN'],'1')
            process=await original(sys.executable,'-u','-c',script,**kwargs)
            children.append(process)
            return process
        with patch('harness.llm.opencode_local.asyncio.create_subprocess_exec',side_effect=spawn):
            await consumer(self.transport())
        self.assertTrue(children)
        self.assertTrue(all(process.returncode is not None for process in children))

    async def test_raw_sse_stream_and_child_exit(self):
        async def consume(transport):
            chunks=[chunk async for chunk in transport.post_stream(BASE_URL+'/chat/completions',
                headers={'Authorization':'Bearer public'},body=b'{"messages":[]}',timeout=2)]
            self.assertEqual(b''.join(chunks),b'data: [DONE]\n\n')
        await self.run_child('import sys;sys.stdin.read();sys.stdout.buffer.write(b\'{"status":200}\\ndata: [DONE]\\n\\n\');sys.stdout.buffer.flush()',consume)

    async def test_429_preserves_status_and_cooldown(self):
        async def consume(transport):
            with self.assertRaises(LlmError) as caught:
                async for _ in transport.post_stream(BASE_URL+'/chat/completions',headers={'Authorization':'Bearer public'},body=b'{}',timeout=2):pass
            self.assertEqual(caught.exception.details['status'],429)
            self.assertEqual(caught.exception.details['retry_after'],3600)
            self.assertTrue(caught.exception.transient)
        await self.run_child('import sys;sys.stdin.read();print(\'{"status":429,"retry_after":"3600"}\');print("provider quota")',consume)

    async def test_free_quota_rejection_preserves_provider_reason_without_retry_limit_masking(self):
        """免费配额拒绝通过真实适配器保留原文，不重试也不替换成等待上限。"""
        from harness.i18n import tr
        reason = json.dumps({'type':'error','error':{'type':'FreeUsageLimitError','message':'Free usage limit reached.'}})
        async def consume(transport):
            adapter = OpenRouterAdapter('public', base_url=BASE_URL, transport=transport, max_retries=2)
            for language in ('en', 'zh-CN', 'en'):
                with current_language.set(language):
                    with self.assertRaises(LlmError) as caught:
                        async for _ in adapter.stream([UserMessage('hello')]):pass
                    self.assertEqual(caught.exception.message, tr('llm.opencode_local.request_rejected', reason=reason+os.linesep))
                self.assertEqual(caught.exception.code, 'llm/opencode-local-rejected')
                self.assertEqual(caught.exception.details['status'], 429)
                self.assertEqual(caught.exception.details['retry_after'], 80000)
                self.assertEqual(caught.exception.details['attempts'], 1)
                self.assertFalse(caught.exception.transient)
        await self.run_child('import sys;sys.stdin.read();print(\'{"status":429,"retry_after":"80000"}\');print('+repr(reason)+')',consume)

    async def test_short_free_cooldown_remains_transient(self):
        """供应商短时冷却仍可进入既有重试策略，不一概认定永久拒绝。"""
        reason = json.dumps({'error':{'type':'FreeUsageLimitError','message':'Wait briefly.'}})
        async def consume(transport):
            with self.assertRaises(LlmError) as caught:
                async for _ in transport.post_stream(BASE_URL+'/chat/completions',headers={'Authorization':'Bearer public'},body=b'{}',timeout=2):pass
            self.assertTrue(caught.exception.transient)
            self.assertEqual(caught.exception.details['retry_after'], 1)
            self.assertEqual(caught.exception.details['status'], 429)
        await self.run_child('import sys;sys.stdin.read();print(\'{"status":429,"retry_after":"1"}\');print('+repr(reason)+')',consume)

    async def test_partial_stream_close_kills_owned_process(self):
        async def consume(transport):
            async with aclosing(transport.post_stream(BASE_URL+'/chat/completions',headers={'Authorization':'Bearer public'},body=b'{}',timeout=10)) as stream:
                self.assertEqual(await anext(stream),b'data: partial\n\n')
        await self.run_child('import sys,time;sys.stdin.read();sys.stdout.buffer.write(b\'{"status":200}\\ndata: partial\\n\\n\');sys.stdout.buffer.flush();time.sleep(30)',consume)

    async def test_active_stream_can_outlast_network_idle_timeout(self):
        async def consume(transport):
            chunks=[chunk async for chunk in transport.post_stream(BASE_URL+'/chat/completions',
                headers={'Authorization':'Bearer public'},body=b'{}',timeout=.1)]
            self.assertEqual(b''.join(chunks),b'x'*60)
        await self.run_child('import sys,time;sys.stdin.read();print(\'{"status":200}\',flush=True);'
                            '[(sys.stdout.buffer.write(b"x"),sys.stdout.buffer.flush(),time.sleep(.1)) for _ in range(60)]',consume)

    async def test_ambiguous_or_deep_helper_metadata_is_localized_and_process_reclaimed(self):
        from harness.i18n import tr
        values = [b'[]', b'null', b'{"status":200,"status":500}',
                  b'{"status":200,"extra":' + b'['*1500 + b'0' + b']'*1500 + b'}']
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                for metadata in values:
                    async def consume(transport):
                        with self.assertRaises(LlmError) as error:
                            async for _ in transport.post_stream(BASE_URL+'/chat/completions',
                                    headers={'Authorization':'Bearer public'}, body=b'{}', timeout=2):pass
                        self.assertEqual(error.exception.code, 'llm/opencode-local-runtime')
                        self.assertEqual(error.exception.message, tr('ui.llm.opencodeLocalRuntimeFailed'))
                    script = 'import sys;sys.stdin.read();sys.stdout.buffer.write('+repr(metadata+b'\n')+');sys.stdout.flush()'
                    await self.run_child(script, consume)

    async def test_network_failure_before_content_retries_through_real_adapter(self):
        """无内容的瞬时断流可重试，成功工具调用仅交付一次。"""
        counter = Path(self.directory.name)/'attempts'
        diagnostic = json.dumps({'failure':'network', 'phase':'connect', 'network_error':'ConnectionResetError'})
        success = b'{"status":200}\ndata: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_fixture","function":{"name":"fixture","arguments":"{}"}}]},"finish_reason":"tool_calls"}]}\n\ndata: [DONE]\n\n'
        script = ('import pathlib,sys;sys.stdin.read();'
                  f'p=pathlib.Path({str(counter)!r});n=int(p.read_text()) if p.exists() else 0;p.write_text(str(n+1));'
                  f'sys.stderr.write({diagnostic!r}) if n==0 else sys.stdout.buffer.write({success!r});'
                  'sys.exit(1 if n==0 else 0)')
        async def consume(transport):
            adapter = OpenRouterAdapter('public', base_url=BASE_URL, transport=transport,
                max_retries=1, retry_base_delay=0)
            events = [event async for event in adapter.stream([UserMessage('hello')])]
            calls = [event.tool_use for event in events if event.kind == 'tool_use']
            self.assertEqual([(call.id, call.name, call.input) for call in calls], [('call_fixture','fixture',{})])
            self.assertEqual(events[-1].kind, 'done')
            self.assertEqual(counter.read_text(), '2')
        await self.run_child(script, consume)

    async def test_partial_network_failure_does_not_replay_or_deliver_incomplete_tool(self):
        """已交付正文后禁止原请求重放，不把未完整的工具参数交给执行器。"""
        counter = Path(self.directory.name)/'attempts'
        diagnostic = json.dumps({'failure':'network', 'phase':'read', 'network_error':'ConnectionResetError'})
        partial = b'{"status":200}\ndata: {"choices":[{"delta":{"content":"kept","tool_calls":[{"index":0,"id":"call_partial","function":{"name":"fixture","arguments":"{"}}]}}]}\n\n'
        script = ('import pathlib,sys;sys.stdin.read();'
                  f'p=pathlib.Path({str(counter)!r});p.write_text(p.read_text()+"x" if p.exists() else "x");'
                  f'sys.stdout.buffer.write({partial!r});sys.stdout.flush();sys.stderr.write({diagnostic!r});sys.exit(1)')
        async def consume(transport):
            adapter = OpenRouterAdapter('public', base_url=BASE_URL, transport=transport, max_retries=2)
            events = []
            with self.assertRaises(LlmError) as caught:
                async for event in adapter.stream([UserMessage('hello')]):events.append(event)
            self.assertTrue(caught.exception.transient)
            self.assertTrue(caught.exception.details['partial_output'])
            self.assertEqual(caught.exception.details['attempts'], 1)
            self.assertEqual(caught.exception.details['network_error'], 'ConnectionResetError')
            self.assertEqual(''.join(event.text for event in events if event.kind == 'text'), 'kept')
            self.assertFalse(any(event.kind in ('tool_use','done') for event in events))
            self.assertEqual(counter.read_text(), 'x')
        await self.run_child(script, consume)

    async def test_idle_timeout_diagnostic_localizes_and_preserves_trace_fields(self):
        """同一传输的英中英错误消费保留资源身份、阶段和实际配置秒数。"""
        from harness.i18n import tr
        from harness.llm.failure_diagnostics import failure_diagnostics
        record = json.dumps({'failure':'idle_timeout', 'phase':'read', 'network_error':None})
        async def consume(transport):
            for language in ('en', 'zh-CN', 'en'):
                with current_language.set(language):
                    with self.assertRaises(LlmError) as caught:
                        async for _ in transport.post_stream(BASE_URL+'/chat/completions',
                                headers={'Authorization':'Bearer public'}, body=b'{}', timeout=2):pass
                    error = caught.exception
                    self.assertEqual(error.message, tr('ui.llm.opencodeLocalIdleTimeout', seconds=2))
                    self.assertEqual(error.details['copy'], {'key':'ui.llm.opencodeLocalIdleTimeout','params':{'seconds':2}})
                    self.assertEqual(failure_diagnostics(error)['phase'], 'read')
                    self.assertEqual(failure_diagnostics(error)['seconds'], 2)
                    self.assertTrue(error.transient)
        await self.run_child('import sys;sys.stdin.read();print(\'{"status":200}\',flush=True);'
                             f'sys.stderr.write({record!r});sys.exit(1)', consume)

    async def test_oversized_and_invalid_diagnostics_are_drained_without_retry_or_text_leak(self):
        """错误管道超过容量仍排空回收，不将第三方原文或歧义JSON纳入诊断。"""
        records = ["'private'*40000", repr('{"failure":"network","failure":"runtime"}'),
                   repr('{"failure":"network","phase":"connect","network_error":"secret"}')]
        for record in records:
            async def consume(transport):
                with self.assertRaises(LlmError) as caught:
                    async for _ in transport.post_stream(BASE_URL+'/chat/completions',
                            headers={'Authorization':'Bearer public'}, body=b'{}', timeout=2):pass
                self.assertFalse(caught.exception.transient)
                self.assertNotIn('private', str(caught.exception))
                self.assertNotIn('secret', str(caught.exception))
            await self.run_child('import sys;sys.stdin.read();'
                                 f'sys.stderr.write({record});sys.exit(1)', consume)

    async def test_unknown_nonzero_exit_remains_terminal_and_keeps_bounded_trace_fields(self):
        """未知退出不能因可重试优化而重放，追踪只保存确认的数字证据。"""
        from harness.llm.failure_diagnostics import failure_diagnostics
        async def consume(transport):
            with self.assertRaises(LlmError) as caught:
                async for _ in transport.post_stream(BASE_URL+'/chat/completions',
                        headers={'Authorization':'Bearer public'}, body=b'{}', timeout=2):pass
            error = caught.exception
            self.assertFalse(error.transient)
            self.assertEqual(error.details['runtime_exit_code'], 7)
            self.assertEqual(error.details['response_bytes'], 1)
            self.assertEqual(error.details['copy']['key'], 'ui.llm.opencodeLocalRuntimeExit')
            self.assertEqual(failure_diagnostics(error), {'phase':'read','runtime_exit_code':7,
                'response_bytes':1,'transient':False})
        await self.run_child('import sys;sys.stdin.read();print(\'{"status":200}\',flush=True);'
                             'sys.stdout.write("x");sys.stdout.flush();sys.exit(7)',consume)

    async def test_spawn_failure_reports_localized_start_guidance_without_retry(self):
        """启动失败与网络中断区分，保留当前语言资源身份。"""
        from harness.i18n import tr
        transport = self.transport()
        with patch('harness.llm.opencode_local.asyncio.create_subprocess_exec', side_effect=OSError('private path')):
            for language in ('en','zh-CN','en'):
                with current_language.set(language):
                    with self.assertRaises(LlmError) as caught:
                        async for _ in transport.post_stream(BASE_URL+'/chat/completions',
                                headers={'Authorization':'Bearer public'},body=b'{}',timeout=2):pass
                    self.assertEqual(caught.exception.message, tr('ui.llm.opencodeLocalStartFailed'))
                    self.assertFalse(caught.exception.transient)
                    self.assertEqual(caught.exception.details['copy'], {'key':'ui.llm.opencodeLocalStartFailed'})
                    self.assertNotIn('private',str(caught.exception))

    async def test_malformed_helper_output_fails_without_retry(self):
        async def consume(transport):
            with self.assertRaises(LlmError) as caught:
                async for _ in transport.post_stream(BASE_URL+'/chat/completions',headers={'Authorization':'Bearer public'},body=b'{}',timeout=2):pass
            self.assertEqual(caught.exception.code,'llm/opencode-local-runtime')
        await self.run_child('import sys;sys.stdin.read();print("invalid")',consume)


    @unittest.skipIf(os.name == 'nt', '后代存在性需要Windows原生进程树验收')
    async def test_bootstrap_reclaims_descendant_after_cli_has_exited(self):
        original = asyncio.create_subprocess_exec
        pid_path = Path(self.directory.name)/'descendant.pid'
        destination = Path(self.directory.name)/'captured-profile.json'
        child = 'import time;time.sleep(30)'
        script = ("import json,pathlib,subprocess,sys;"
                  f"p=subprocess.Popen([sys.executable,'-c',{child!r}]);"
                  f"pathlib.Path({str(pid_path)!r}).write_text(str(p.pid));"
                  f"pathlib.Path('profile.json').write_text(json.dumps({self.data!r}))")
        async def spawn(*args, **kwargs):
            native_command = json.loads(args[-1])
            self.assertIn('--standalone', native_command)
            self.assertEqual(kwargs['env']['PWD'], str(kwargs['cwd']))
            self.assertEqual(kwargs['env']['TMP'], kwargs['env']['TMPDIR'])
            self.assertEqual(kwargs['env']['TEMP'], kwargs['env']['TMPDIR'])
            for name in ('XDG_DATA_HOME', 'XDG_STATE_HOME', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'OPENCODE_TEST_HOME', 'TMPDIR'):
                self.assertTrue(Path(kwargs['env'][name]).is_relative_to(kwargs['cwd']))
            self.assertNotIn('OPENCODE_CONFIG_CONTENT', kwargs['env'])
            command = list(args)
            command[-1] = json.dumps([sys.executable, '-c', script])
            return await original(*command, **kwargs)
        with patch('harness.llm.opencode_local.asyncio.create_subprocess_exec', side_effect=spawn):
            await bootstrap_profile(self.executable, destination)
        self.assertEqual(load_profile(self.executable, destination)['system'], self.data['system'])
        pid = int(pid_path.read_text())
        for _ in range(50):
            try:os.kill(pid, 0)
            except ProcessLookupError:break
            await asyncio.sleep(.02)
        else:
            state = Path(f'/proc/{pid}/stat')
            self.assertTrue(state.exists() and state.read_text().split()[2] == 'Z')

    async def test_bootstrap_cancel_during_startup_owns_late_process_and_temporary_files(self):
        """取消启动等待后仍接管已创建进程，临时项目在清理结束后才删除。"""
        entered, release = asyncio.Event(), asyncio.Event()
        original = asyncio.create_subprocess_exec
        children, roots = [], []
        destination = Path(self.directory.name) / 'new-profile.json'

        async def delayed(*args, **kwargs):
            if args[0] == 'taskkill':
                return await original(*args, **kwargs)
            process = await original(sys.executable, '-u', '-c', 'import time;time.sleep(30)', **kwargs)
            children.append(process)
            roots.append(Path(kwargs['cwd']))
            entered.set()
            await release.wait()
            return process

        with patch('harness.llm.opencode_local.asyncio.create_subprocess_exec', side_effect=delayed):
            task = asyncio.create_task(bootstrap_profile(self.executable, destination))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                task.cancel()
                await asyncio.sleep(0)
                task.cancel()
                await asyncio.sleep(0)
                self.assertFalse(task.done())
                self.assertTrue(roots[0].exists())
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertTrue(all(process.returncode is not None for process in children))
                self.assertFalse(destination.exists())
                self.assertFalse(roots[0].exists())
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)
                # 基线失败时也只清理本测试创建且仍持有句柄的进程。
                from harness.tools.processes import stop_process
                for process in children:
                    if process.returncode is None:
                        await stop_process(process)
