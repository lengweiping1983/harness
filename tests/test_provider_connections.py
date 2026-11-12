"""供应商配置、接口共享密钥及真实运行时桥接的隔离回归。"""
import asyncio
from contextlib import aclosing
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from harness.llm.adapter import OpenRouterAdapter
from harness.llm.providers import ProviderConnections, directory, model_identity
from harness.llm.types import LlmCallConfig, UserMessage


def provider_fixture(base_url='https://example.invalid/v1', protocol='openai-completions'):
    return {'id': 'supplier', 'type': 'custom', 'name': '测试供应商', 'connections': [
        {'id': 'endpoint', 'name': '接口', 'protocol': protocol, 'base_url': base_url, 'enabled': True,
         'auth': {'type': 'api_key', 'env': {}}, 'keys': [{'id': 'first', 'value': 'fixture-first', 'enabled': True}, {'id': 'second', 'value': 'fixture-second', 'enabled': True}],
         'models': [{'id': 'model-a', 'model': 'remote-a', 'name': '模型 A', 'input': ['text'], 'enabled': True}, {'id': 'model-b', 'model': 'remote-b', 'name': '模型 B', 'input': ['text'], 'enabled': True}]}]}


class ProviderStorageTests(unittest.TestCase):
    def test_empty_save_shared_keys_stable_identity_and_secret_omission(self):
        with tempfile.TemporaryDirectory() as root:
            store = ProviderConnections(root)
            self.assertEqual(store.snapshot()['providers'], [])
            saved = store.save([provider_fixture()], store.revision)
            connection = saved['providers'][0]['connections'][0]
            self.assertEqual(len(connection['keys']), 2)
            self.assertEqual(len(connection['models']), 2)
            self.assertNotIn('fixture-first', json.dumps(saved))
            self.assertEqual(connection['keys'][0]['mask'], '******irst')
            again = store.save(saved['providers'], saved['revision'])
            self.assertEqual(store.resolve(model_identity('supplier', 'endpoint', 'model-a'))[1]['keys'][0]['value'], 'fixture-first')
            self.assertEqual(ProviderConnections(root).snapshot(), again)
            self.assertEqual(store.catalog()[0]['id'], 'harness/supplier/endpoint/model-a')

    def test_packaged_runtime_runs_without_source_checkout_or_node_modules(self):
        import os
        import shutil
        import subprocess
        from harness.llm.providers import RUNTIME
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / 'runtime'
            shutil.copytree(RUNTIME, target)
            environment = {**os.environ, 'NODE_PATH': '', 'HARNESS_BRIDGE_TEST': '', 'ELECTRON_RUN_AS_NODE': '1'}
            result = subprocess.run([shutil.which('node'), str(target / 'bridge.mjs')], input=json.dumps({'version': 1, 'operation': 'catalog'}), text=True, capture_output=True, cwd=root, env=environment, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            providers = {row['id'] for row in json.loads(result.stdout)['value']}
            self.assertTrue({'amazon-bedrock', 'google-vertex', 'anthropic', 'github-copilot', 'mistral'} <= providers)

    def test_conflicting_writers_rejected_without_overwriting(self):
        with tempfile.TemporaryDirectory() as root:
            first, second = ProviderConnections(root), ProviderConnections(root)
            revision = second.revision
            first.save([provider_fixture()], first.revision)
            with self.assertRaises(ValueError):
                second.save([], revision)
            self.assertEqual(len(ProviderConnections(root).providers), 1)

    def test_failed_key_shared_by_models_and_reset_is_explicit(self):
        with tempfile.TemporaryDirectory() as root:
            store = ProviderConnections(root)
            store.save([provider_fixture()], store.revision)
            _, connection, _ = store.resolve('harness/supplier/endpoint/model-a')
            store.fail_key(connection, connection['keys'][0])
            _, second, _ = store.resolve('harness/supplier/endpoint/model-b')
            self.assertTrue(second['keys'][0]['failed'])
            store.key_status('endpoint', None, reset=True)
            self.assertFalse(store.resolve('harness/supplier/endpoint/model-a')[1]['keys'][0]['failed'])

    def test_malformed_files_and_input_preserve_original(self):
        invalid = [b'{"version":2,"version":2}', b'\xff', b'{"version":1,"providers":[]}']
        for raw in invalid:
            with tempfile.TemporaryDirectory() as root:
                path = Path(root) / 'model-providers.json'
                path.write_bytes(raw)
                with self.assertRaises(ValueError):
                    ProviderConnections(root)
                self.assertEqual(path.read_bytes(), raw)
        with tempfile.TemporaryDirectory() as root:
            store = ProviderConnections(root)
            for mutation in (lambda c: c.update(protocol='unknown'), lambda c: c['keys'][0].update(value='******fake'), lambda c: c['models'][0].update(context_window=True), lambda c: c['models'][0].update(input=[]), lambda c: c.update(compat={'invalid': float('nan')}), lambda c: c.update(headers={'Bad\r\nHeader': 'fixture'})):
                value = provider_fixture(); mutation(value['connections'][0])
                with self.assertRaises(ValueError):
                    store.save([value], store.revision)
            self.assertFalse(store.path.exists())

    def test_source_provider_directory_includes_native_and_special_routes(self):
        entries = {row['id']: row for row in directory()}
        for name in ('deepseek-official', 'deepseek-account', 'openai', 'anthropic', 'google', 'google-vertex', 'amazon-bedrock', 'azure', 'github-copilot', 'openrouter', 'opencode', 'opencode-go', 'opencode-local'):
            self.assertIn(name, entries)
        self.assertTrue(entries['github-copilot']['oauth'])
        self.assertTrue(entries['deepseek-account']['oauth'])


class RuntimeBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_selected_model_is_fixed_and_only_its_connection_keys_rotate(self):
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                """隔离测试不打印请求头。"""
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                key = self.headers.get('Authorization')
                requests.append((body, key))
                if key == 'Bearer fixture-first':
                    self.send_response(401); self.send_header('Content-Type', 'application/json'); self.end_headers(); self.wfile.write(b'{"error":{"message":"rejected"}}'); return
                self.send_response(200); self.send_header('Content-Type', 'text/event-stream'); self.end_headers()
                payload = {'id': 'fixture', 'object': 'chat.completion.chunk', 'created': 0, 'model': body['model'], 'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': '完成'}, 'finish_reason': None}]}
                self.wfile.write(('data: '+json.dumps(payload)+'\n\n').encode())
                payload['choices'] = [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]
                self.wfile.write(('data: '+json.dumps(payload)+'\n\ndata: [DONE]\n\n').encode())
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            with tempfile.TemporaryDirectory() as root:
                store = ProviderConnections(root)
                store.save([provider_fixture(f'http://127.0.0.1:{server.server_port}/v1')], store.revision)
                adapter = OpenRouterAdapter('unused')
                adapter.provider_connections = store
                identity = 'harness/supplier/endpoint/model-b'
                events = []
                async with aclosing(adapter.stream([UserMessage('test')], config=LlmCallConfig(model=identity))) as stream:
                    async for event in stream:
                        events.append(event)
                self.assertEqual(''.join(event.text for event in events if event.kind == 'text'), '完成')
                self.assertEqual(events[-1].kind, 'done')
                self.assertEqual([body['model'] for body, _ in requests], ['remote-b', 'remote-b'])
                self.assertEqual([key for _, key in requests], ['Bearer fixture-first', 'Bearer fixture-second'])
                self.assertFalse(store.snapshot()['providers'][0]['connections'][0]['keys'][0]['available'])
                self.assertEqual(store.leases, {})
        finally:
            await asyncio.to_thread(server.shutdown); server.server_close(); thread.join()

    async def test_partial_output_auth_failure_never_replays_and_exhaustion_is_explicit(self):
        from unittest.mock import patch
        from harness.core.errors import LlmError
        attempts = []
        async def partial(request, **kwargs):
            attempts.append(request)
            yield {'type': 'event', 'event': {'kind': 'text', 'text': '已交付'}}
            raise LlmError('denied', code='fixture', status=401)
        async def rejected(request, **kwargs):
            attempts.append(request)
            if False:
                yield
            raise LlmError('denied', code='fixture', status=401)
        with tempfile.TemporaryDirectory() as root:
            store = ProviderConnections(root)
            store.save([provider_fixture()], store.revision)
            adapter = OpenRouterAdapter('unused'); adapter.provider_connections = store
            config = LlmCallConfig(model='harness/supplier/endpoint/model-a')
            with patch('harness.llm.provider_runtime.bridge', partial):
                with self.assertRaises(LlmError):
                    async with aclosing(adapter.stream([UserMessage('test')], config=config)) as stream:
                        async for _ in stream:
                            pass
            self.assertEqual(len(attempts), 1)
            store.key_status('endpoint', None, reset=True)
            attempts.clear()
            with patch('harness.llm.provider_runtime.bridge', rejected):
                with self.assertRaises(LlmError) as failure:
                    async with aclosing(adapter.stream([UserMessage('test')], config=config)) as stream:
                        async for _ in stream:
                            pass
            self.assertEqual(failure.exception.code, 'llm/key-exhausted')
            self.assertEqual(len(attempts), 2)
            self.assertEqual(store.leases, {})

    async def test_http_200_upstream_idle_timeout_rotates_key(self):
        """HTTP 200 流内的上游空闲超时仍按瞬时故障切换接口 Key。"""
        from unittest.mock import patch
        from harness.core.errors import LlmError

        attempts = []

        async def responding(request, **kwargs):
            attempts.append(request['credential']['key'])
            if len(attempts) == 1:
                raise LlmError('模型请求失败：Upstream idle timeout exceeded', code='llm/provider', status=200,
                               transient=True)
            yield {'type': 'event', 'event': {'kind': 'text', 'text': '完成'}}
            yield {'type': 'event', 'event': {'kind': 'done', 'stop_reason': 'stop'}}

        with tempfile.TemporaryDirectory() as root:
            store = ProviderConnections(root)
            store.save([provider_fixture()], store.revision)
            adapter = OpenRouterAdapter('unused')
            adapter.provider_connections = store
            config = LlmCallConfig(model='harness/supplier/endpoint/model-a')
            with patch('harness.llm.provider_runtime.bridge', responding):
                events = [event async for event in adapter.stream([UserMessage('test')], config=config)]
            self.assertEqual(attempts, ['fixture-first', 'fixture-second'])
            self.assertEqual(''.join(event.text for event in events if event.kind == 'text'), '完成')
            self.assertEqual(events[-1].kind, 'done')
            self.assertEqual(store.leases, {})

    async def test_http_200_failure_event_classifies_idle_timeout_as_transient(self):
        """桥接收到 HTTP 200 失败事件时仍保留瞬时错误分类。"""
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, Mock, patch
        from harness.core.errors import LlmError
        from harness.llm.provider_runtime import bridge

        failure = (json.dumps({'type': 'failure', 'status': 200,
                               'message': 'Upstream idle timeout exceeded'}) + '\n').encode()
        process = SimpleNamespace(
            stdin=SimpleNamespace(write=Mock(), drain=AsyncMock()),
            stdout=SimpleNamespace(readline=AsyncMock(side_effect=[failure, b''])),
            wait=AsyncMock(return_value=0),
        )
        with patch('harness.llm.provider_runtime._start_process', AsyncMock(return_value=process)), \
                patch('harness.llm.provider_runtime._stop_stream_process', AsyncMock()):
            with self.assertRaises(LlmError) as caught:
                _ = [value async for value in bridge({'operation': 'stream'})]
        self.assertTrue(caught.exception.transient)
        self.assertEqual(caught.exception.details['status'], 200)

    async def test_default_model_uses_provider_dispatch_without_explicit_call_config(self):
        from unittest.mock import patch
        requests = []
        async def responding(request, **kwargs):
            requests.append(request)
            yield {'type': 'event', 'event': {'kind': 'text', 'text': '完成'}}
            yield {'type': 'event', 'event': {'kind': 'done', 'stop_reason': 'stop'}}
        with tempfile.TemporaryDirectory() as root:
            store = ProviderConnections(root); store.save([provider_fixture()], store.revision)
            adapter = OpenRouterAdapter('unused', model='harness/supplier/endpoint/model-a')
            adapter.provider_connections = store
            with patch('harness.llm.provider_runtime.bridge', responding):
                result = await adapter.complete([UserMessage('test')])
            self.assertEqual(result.text, '完成')
            self.assertEqual(requests[0]['model']['model'], 'remote-a')
            self.assertEqual(store.leases, {})

    async def test_native_environment_auth_uses_connection_draft(self):
        from harness.llm.provider_runtime import bridge
        for provider, variable in [('openai', 'OPENAI_API_KEY'), ('azure', 'AZURE_OPENAI_API_KEY'), ('google', 'GEMINI_API_KEY'), ('amazon-bedrock', 'AWS_PROFILE')]:
            with self.subTest(provider=provider):
                request = {'operation': 'check', 'provider': provider, 'connection': {'auth': {'env': {variable: 'isolated-fixture'}}}, 'credential': {'type': 'api_key', 'env': {variable: 'isolated-fixture'}}}
                result = [event async for event in bridge(request)]
                self.assertTrue(result[-1]['value'])

    async def test_qwen_snapshot_instruction_role_reaches_actual_request(self):
        """Qwen 与自定义接口不因推理能力升级指令角色，显式配置保留优先级。"""
        from harness.llm.types import DeveloperMessage, SystemMessage
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                """隔离测试不输出凭据。"""

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.append((self.path, body))
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.end_headers()
                chunk = {'id': 'fixture', 'choices': [{'index': 0, 'delta': {'content': '完成'}, 'finish_reason': None}]}
                end = {'id': 'fixture', 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]}
                for value in (chunk, end):
                    self.wfile.write(('data: ' + json.dumps(value) + '\n\n').encode())
                self.wfile.write(b'data: [DONE]\n\n')

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        cases = [
            ('qwen-token-plan-cn', '', True, {}, {}, {}, 'system'),
            ('qwen-token-plan-cn', 'openai-completions', True, {}, {}, {}, 'system'),
            ('qwen-token-plan-cn', '', False, {}, {}, {}, 'system'),
            ('qwen-token-plan', '', True, {}, {}, {}, 'system'),
            ('qwen-token-plan-individual', '', True, {}, {}, {}, 'system'),
            ('qwen-token-plan-cn', '', True, {}, {'supportsDeveloperRole': True}, {}, 'developer'),
            ('qwen-token-plan-cn', '', True, {}, {'supportsDeveloperRole': True}, {'supportsDeveloperRole': False}, 'system'),
            ('custom', 'openai-completions', True, {}, {}, {}, 'system'),
            ('custom', 'openai-completions', False, {}, {}, {}, 'system'),
            ('custom', 'openai-completions', True, {'supportsDeveloperRole': True}, {}, {}, 'developer'),
            ('custom', 'openai-completions', True, {}, {'supportsDeveloperRole': True}, {}, 'developer'),
            ('custom', 'openai-completions', True, {}, {'supportsDeveloperRole': True}, {'supportsDeveloperRole': False}, 'system'),
            ('custom', 'openai-completions', True, {}, {'supportsDeveloperRole': False}, {'supportsDeveloperRole': True}, 'developer'),
            ('openai', 'openai-completions', True, {}, {}, {}, 'developer'),
        ]
        cases = [(*case, effort) for case in cases for effort in ((None, 'high', 'max') if case[0] == 'custom' else ('high',))]
        try:
            for provider, protocol, cached, catalog_compat, connection_compat, model_compat, role, effort in cases:
                with self.subTest(provider=provider, protocol=protocol, cached=cached, role=role, effort=effort), tempfile.TemporaryDirectory() as root:
                    value = provider_fixture(f'http://127.0.0.1:{server.server_port}/v1', protocol)
                    value['type'] = provider
                    connection = value['connections'][0]
                    connection['keys'] = connection['keys'][:1]
                    connection['compat'] = connection_compat
                    model = connection['models'][0]
                    model.update(model='egg-alpha' if provider == 'custom' else 'deepseek-v4.1-flash-1017',
                                 reasoning_efforts={'high': 'high', 'max': 'max'} if provider == 'custom' else {'high': 'high'}, compat=model_compat)
                    connection['models'] = [model]
                    if cached:
                        connection['model_catalog'] = [{'id': model['model'], 'api': 'openai-completions', 'reasoning': True, 'compat': catalog_compat}]
                    store = ProviderConnections(root)
                    store.save([value], store.revision)
                    adapter = OpenRouterAdapter('unused')
                    adapter.provider_connections = ProviderConnections(root)
                    captured = []
                    async def capture(event):
                        if event['phase'] == 'started':
                            captured.append(json.loads(event['body']))
                    events = [event async for event in adapter.stream(
                        [SystemMessage('系统指令'), DeveloperMessage('开发者指引'), UserMessage('test')],
                        config=LlmCallConfig(model='harness/supplier/endpoint/model-a', reasoning_effort=effort),
                        on_request=capture)]
                    self.assertEqual(''.join(event.text for event in events if event.kind == 'text'), '完成')
                    self.assertEqual(len(captured), 1)
                    path, body = requests[-1]
                    self.assertEqual(path, '/v1/chat/completions')
                    self.assertEqual(captured[0], body)
                    self.assertEqual(body['model'], model['model'])
                    self.assertEqual(body['messages'][0], {'role': role, 'content': '系统指令\n\n开发者指引'})
                    self.assertEqual(body['messages'][-1], {'role': 'user', 'content': 'test'})
                    if provider == 'custom' and effort is not None:
                        self.assertEqual(body['reasoning_effort'], effort)
                    if provider == 'custom' and effort is None:
                        self.assertNotIn('reasoning_effort', body)
                        self.assertNotIn('reasoning', body)
                    self.assertEqual(adapter.provider_connections.leases, {})
        finally:
            await asyncio.to_thread(server.shutdown)
            server.server_close()
            thread.join()

    async def test_output_budget_cannot_expand_model_output_capacity(self):
        """上下文余额不能代替输出能力，较小的显式请求预算仍然生效。"""
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                """隔离测试不输出凭据。"""

            def do_POST(self):
                requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.end_headers()
                chunk = {'id': 'fixture', 'choices': [{'index': 0, 'delta': {'content': '完成'}, 'finish_reason': None}]}
                end = {'id': 'fixture', 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]}
                for value in (chunk, end):
                    self.wfile.write(('data: ' + json.dumps(value) + '\n\n').encode())
                self.wfile.write(b'data: [DONE]\n\n')

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        cases = [(None, None, None), (128000, None, None), (384000, None, None), (None, 984198, 984198),
                 (393216, 984198, 393216), (393216, 4096, 4096), (None, 2048, 2048)]
        try:
            for output_capacity, requested, expected in cases:
                with self.subTest(capacity=output_capacity, requested=requested), tempfile.TemporaryDirectory() as root:
                    value = provider_fixture(f'http://127.0.0.1:{server.server_port}/v1')
                    value['type'] = 'qwen-token-plan-cn'
                    connection = value['connections'][0]
                    connection['keys'] = connection['keys'][:1]
                    model = connection['models'][0]
                    model['model'] = 'deepseek-v4.1-flash-1017'
                    connection['models'] = [model]
                    metadata = {'id': model['model'], 'api': 'openai-completions', 'contextWindow': 1000000}
                    if output_capacity is not None:
                        metadata['maxTokens'] = output_capacity
                    connection['model_catalog'] = [metadata]
                    store = ProviderConnections(root)
                    store.save([value], store.revision)
                    adapter = OpenRouterAdapter('unused')
                    adapter.provider_connections = ProviderConnections(root)
                    captured = []
                    async def capture(event):
                        if event['phase'] == 'started':
                            captured.append(json.loads(event['body']))
                    before = len(requests)
                    events = [event async for event in adapter.stream(
                        [UserMessage('test')],
                        config=LlmCallConfig(model='harness/supplier/endpoint/model-a', max_tokens=requested),
                        on_request=capture)]
                    self.assertEqual(''.join(event.text for event in events if event.kind == 'text'), '完成')
                    self.assertEqual(len(requests), before + 1)
                    self.assertEqual(captured, [requests[-1]])
                    self.assertEqual(requests[-1].get('max_completion_tokens'), expected)
        finally:
            await asyncio.to_thread(server.shutdown)
            server.server_close()
            thread.join()

    async def test_messages_and_responses_streams_map_text_tools_and_usage(self):
        from harness.llm.types import ToolSchema
        requests = []
        protocol = ''
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                """隔离测试不输出任何凭据。"""
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.append((self.path, body, self.headers.get('x-api-key'), self.headers.get('Authorization')))
                if protocol == 'anthropic-messages':
                    values = [
                        ('message_start', {'message': {'id': 'msg', 'type': 'message', 'role': 'assistant', 'model': 'remote-a', 'content': [], 'usage': {'input_tokens': 7, 'cache_read_input_tokens': 5, 'cache_creation_input_tokens': 3, 'output_tokens': 0}}}),
                        ('content_block_start', {'index': 0, 'content_block': {'type': 'text', 'text': ''}}),
                        ('content_block_delta', {'index': 0, 'delta': {'type': 'text_delta', 'text': '完成'}}),
                        ('content_block_stop', {'index': 0}),
                        ('content_block_start', {'index': 1, 'content_block': {'type': 'tool_use', 'id': 'call-1', 'name': 'lookup', 'input': {}}}),
                        ('content_block_delta', {'index': 1, 'delta': {'type': 'input_json_delta', 'partial_json': '{"value":42}'}}),
                        ('content_block_stop', {'index': 1}),
                        ('message_delta', {'delta': {'stop_reason': 'tool_use'}, 'usage': {'output_tokens': 9}}), ('message_stop', {})]
                else:
                    message = {'id': 'msg', 'type': 'message', 'role': 'assistant', 'status': 'completed', 'content': [{'type': 'output_text', 'text': '完成', 'annotations': []}]}
                    call = {'id': 'fc', 'type': 'function_call', 'call_id': 'call-1', 'name': 'lookup', 'arguments': '{"value":42}', 'status': 'completed'}
                    values = [
                        ('response.created', {'response': {'id': 'resp', 'status': 'in_progress', 'output': []}}),
                        ('response.output_item.added', {'output_index': 0, 'item': {**message, 'content': []}}),
                        ('response.content_part.added', {'output_index': 0, 'content_index': 0, 'part': {'type': 'output_text', 'text': '', 'annotations': []}}),
                        ('response.output_text.delta', {'output_index': 0, 'content_index': 0, 'delta': '完成'}),
                        ('response.output_item.done', {'output_index': 0, 'item': message}),
                        ('response.output_item.added', {'output_index': 1, 'item': {**call, 'arguments': ''}}),
                        ('response.function_call_arguments.delta', {'output_index': 1, 'delta': '{"value":42}'}),
                        ('response.output_item.done', {'output_index': 1, 'item': call}),
                        ('response.completed', {'response': {'id': 'resp', 'status': 'completed', 'output': [message, call], 'usage': {'input_tokens': 15, 'output_tokens': 9, 'total_tokens': 24, 'input_tokens_details': {'cached_tokens': 5}, 'output_tokens_details': {'reasoning_tokens': 0}}}})]
                self.send_response(200); self.send_header('Content-Type', 'text/event-stream'); self.end_headers()
                for kind, value in values:
                    self.wfile.write(('event: '+kind+'\ndata: '+json.dumps({'type': kind, **value})+'\n\n').encode())
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            for protocol in ('anthropic-messages', 'openai-responses'):
                with self.subTest(protocol=protocol), tempfile.TemporaryDirectory() as root:
                    store = ProviderConnections(root)
                    value = provider_fixture(f'http://127.0.0.1:{server.server_port}/v1', protocol)
                    value['connections'][0]['keys'] = value['connections'][0]['keys'][:1]
                    store.save([value], store.revision)
                    adapter = OpenRouterAdapter('unused'); adapter.provider_connections = store
                    tool = ToolSchema(name='lookup', description='fixture', parameters={'type': 'object', 'properties': {'value': {'type': 'integer'}}})
                    events = [event async for event in adapter.stream([UserMessage('test')], config=LlmCallConfig(model='harness/supplier/endpoint/model-a'), tools=[tool])]
                    self.assertEqual(''.join(event.text for event in events if event.kind == 'text'), '完成')
                    self.assertEqual(next(event.tool_use.input for event in events if event.kind == 'tool_use'), {'value': 42})
                    self.assertEqual(events[-1].stop_reason, 'tool_use')
                    usage = next(event.usage for event in events if event.kind == 'usage')
                    self.assertEqual((usage.input_tokens, usage.output_tokens, usage.cache_read_tokens, usage.cache_reported_input_tokens), (15, 9, 5, 15))
                    self.assertEqual(requests[-1][1]['model'], 'remote-a')
                    self.assertIn('tools', requests[-1][1])
        finally:
            await asyncio.to_thread(server.shutdown); server.server_close(); thread.join()

    async def test_native_google_protocol_keeps_tools_signature_and_thinking_usage(self):
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                """隔离测试不输出请求头。"""
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.append((self.path, body))
                self.send_response(200); self.send_header('Content-Type', 'text/event-stream'); self.end_headers()
                chunks = [
                    {'candidates': [{'content': {'role': 'model', 'parts': [{'text': '思考', 'thought': True, 'thoughtSignature': 'opaque-thought'}]}}]},
                    {'candidates': [{'content': {'role': 'model', 'parts': [{'text': '完成'}, {'functionCall': {'name': 'lookup', 'args': {'value': 42}}, 'thoughtSignature': 'opaque-tool'}]}, 'finishReason': 'STOP'}], 'usageMetadata': {'promptTokenCount': 7, 'candidatesTokenCount': 9, 'thoughtsTokenCount': 3, 'totalTokenCount': 19}}]
                for chunk in chunks:
                    self.wfile.write(('data: '+json.dumps(chunk)+'\n\n').encode())
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            with tempfile.TemporaryDirectory() as root:
                store = ProviderConnections(root)
                value = provider_fixture(f'http://127.0.0.1:{server.server_port}/v1', '')
                value['type'] = 'google'
                store.save([value], store.revision)
                adapter = OpenRouterAdapter('unused'); adapter.provider_connections = store
                events = [event async for event in adapter.stream([UserMessage('test')], config=LlmCallConfig(model='harness/supplier/endpoint/model-a'))]
                self.assertEqual(''.join(event.text for event in events if event.kind == 'text'), '完成')
                self.assertEqual(''.join(event.thinking for event in events if event.kind == 'thinking'), '思考')
                self.assertEqual(next(event.tool_use.input for event in events if event.kind == 'tool_use'), {'value': 42})
                usage = next(event.usage for event in events if event.kind == 'usage')
                self.assertEqual((usage.input_tokens, usage.output_tokens, usage.reasoning_tokens), (7, 12, 3))
                replay = next(event.reasoning_details for event in events if event.kind == 'reasoning_details')
                self.assertEqual(replay[0]['scope'], 'harness/supplier/endpoint/model-a')
                self.assertIn('remote-a:streamGenerateContent', requests[0][0])
        finally:
            await asyncio.to_thread(server.shutdown); server.server_close(); thread.join()

    async def test_new_cached_model_reasoning_reaches_the_actual_request(self):
        """新增目录模型的思考档位通过真实桥接发送，不依赖内置模型存在。"""
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                """隔离请求不输出凭据。"""
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.append(body)
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.end_headers()
                chunks = [
                    {'id': 'fixture', 'model': 'reasoner-new', 'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': '完成'}, 'finish_reason': None}]},
                    {'id': 'fixture', 'model': 'reasoner-new', 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}], 'usage': {'prompt_tokens': 1, 'completion_tokens': 1}},
                ]
                for chunk in chunks:
                    self.wfile.write(('data: ' + json.dumps(chunk) + '\n\n').encode())
                self.wfile.write(b'data: [DONE]\n\n')
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            cases = [
                ('mapped-high', {'thinkingLevelMap': {'low': 'low', 'high': 'high', 'max': 'max'}}, {}, {}, 'high', 'reasoning_effort', 'high'),
                ('listed-max-flat', {'reasoningEfforts': ['low', 'high', 'xhigh', 'max']}, {}, {}, 'max', 'reasoning_effort', 'max'),
                ('listed-xhigh-flat', {'reasoningEfforts': ['low', 'high', 'xhigh', 'max']}, {}, {}, 'xhigh', 'reasoning_effort', 'xhigh'),
                ('listed-with-empty-map', {'reasoningEfforts': ['high', 'max'], 'thinkingLevelMap': {}}, {}, {}, 'max', 'reasoning_effort', 'max'),
                ('listed-with-mapping', {'reasoningEfforts': ['high', 'max'], 'thinkingLevelMap': {'high': 'high', 'max': 'maximum'}}, {}, {}, 'max', 'reasoning_effort', 'maximum'),
                ('listed-max-nested', {'reasoningEfforts': ['high', 'max']}, {}, {'thinkingFormat': 'openrouter'}, 'max', 'reasoning', {'effort': 'max'}),
                ('model-default-nested', {'reasoningEfforts': ['off', 'high', 'max']}, {}, {'thinkingFormat': 'openrouter'}, None, None, None),
                ('explicit-nested-extension', {'reasoningEfforts': ['high', 'max']}, {}, {'thinkingFormat': 'openrouter'}, None, 'reasoning', {'effort': 'max'}, {'reasoning': {'effort': 'max'}}),
                ('explicit-flat-extension', {'reasoningEfforts': ['high', 'max']}, {}, {}, None, 'reasoning_effort', 'max', {'reasoning_effort': 'max'}),
                ('explicit-off-nested', {'reasoningEfforts': ['off', 'high', 'max']}, {}, {'thinkingFormat': 'openrouter'}, 'off', 'reasoning', {'effort': 'none'}),
                ('model-overrides-endpoint', {'reasoningEfforts': ['high', 'max']}, {'compat': {'thinkingFormat': 'openai'}}, {'thinkingFormat': 'openrouter'}, 'max', 'reasoning_effort', 'max'),
                ('explicit-mapping', {'reasoningEfforts': ['high']}, {'reasoning_efforts': {'max': 'maximum'}}, {}, 'max', 'reasoning_effort', 'maximum'),
                ('disabled', {'reasoningEfforts': ['high', 'max']}, {'reasoning_efforts': False}, {}, 'max', None, None),
                ('unknown', {}, {}, {}, 'max', None, None),
            ]
            for name, capabilities, overrides, endpoint_compat, effort, field, parameter, *extensions in cases:
                with tempfile.TemporaryDirectory() as root, self.subTest(case=name):
                    value = provider_fixture(f'http://127.0.0.1:{server.server_port}/v1')
                    connection = value['connections'][0]
                    connection['models'] = connection['models'][:1]
                    remote = 'stealth/crouton-alpha' if field == 'reasoning' else 'egg-alpha'
                    connection['models'][0].update(model=remote, **overrides)
                    connection['compat'] = endpoint_compat
                    connection['model_catalog'] = [{'id': remote, 'name': '新推理模型', 'api': 'openai-completions',
                        **capabilities, 'contextWindow': 32000, 'maxTokens': 1000,
                        'compat': {'supportsReasoningEffort': True, 'supportsStore': False, 'supportsDeveloperRole': False}}]
                    store = ProviderConnections(root)
                    store.save([value], store.revision)
                    store = ProviderConnections(root)
                    before = store.path.read_bytes()
                    levels = store.catalog()[0]['reasoning_efforts']
                    if effort is not None:
                        self.assertEqual(effort in levels, field is not None)
                    adapter = OpenRouterAdapter('unused')
                    adapter.provider_connections = store
                    sent = len(requests)
                    events = [event async for event in adapter.stream([UserMessage('test')], config=LlmCallConfig(model='harness/supplier/endpoint/model-a', reasoning_effort=effort, extra_body=extensions[0] if extensions else {}))]
                    self.assertEqual(''.join(event.text for event in events if event.kind == 'text'), '完成')
                    self.assertEqual(len(requests), sent + 1)
                    self.assertEqual(requests[-1]['model'], remote)
                    for key in ('reasoning', 'reasoning_effort'):
                        if key == field:
                            self.assertEqual(requests[-1][key], parameter)
                        else:
                            self.assertNotIn(key, requests[-1])
                    self.assertEqual(store.path.read_bytes(), before)
                    self.assertFalse(store.leases)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


class DraftAuthorizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelling_bridge_still_commits_a_completed_token_rotation(self):
        from unittest.mock import patch
        from harness.llm.provider_runtime import bridge
        with tempfile.TemporaryDirectory() as root:
            Path(root, 'bridge.mjs').write_text("""
import {createInterface} from 'node:readline';
const input=createInterface({input:process.stdin});let started=false;
input.on('line',line=>{const value=JSON.parse(line);if(!started){started=true;console.log(JSON.stringify({type:'credential_pending'}));console.log(JSON.stringify({type:'event',event:{kind:'start'}}));}else if(value.type==='cancel'){console.log(JSON.stringify({type:'credential',credential:{type:'oauth',access:'rotated-secret'}}));}else if(value.type==='committed'){input.close();process.stdin.destroy();}});
""", encoding='utf-8')
            commits = []
            async def commit(value):
                commits.append(value)
            with patch('harness.llm.provider_runtime.RUNTIME', Path(root)):
                stream = bridge({'operation': 'stream', 'credential': {'type': 'oauth', 'access': 'old'}}, commit=commit)
                event = await anext(stream)
                self.assertEqual(event['event']['kind'], 'start')
                await stream.aclose()
            self.assertEqual(commits, [{'type': 'oauth', 'access': 'rotated-secret'}])

    async def test_multiple_refresh_commits_use_latest_credential_and_keep_secrets_private(self):
        from unittest.mock import patch
        from harness.llm.provider_runtime import credential_bridge
        async def refreshing(request, *, commit, interact):
            await commit({'type': 'oauth', 'access': 'first'})
            await commit({'type': 'oauth', 'access': 'second'})
            yield {'type': 'result', 'value': True}
        with tempfile.TemporaryDirectory() as root:
            store = ProviderConnections(root)
            value = provider_fixture(); value['type'] = 'anthropic'
            value['connections'][0].update(auth={'type': 'oauth', 'env': {}}, keys=[])
            store.save([value], store.revision)
            with patch('harness.llm.provider_runtime.bridge', refreshing):
                result = [event async for event in credential_bridge(store, 'endpoint', {'operation': 'check'})]
            self.assertTrue(result[0]['value'])
            self.assertEqual(store.providers[0]['connections'][0]['auth']['credential']['access'], 'second')
            self.assertNotIn('second', json.dumps(store.snapshot()))

    async def test_login_stays_in_draft_until_matching_revision_is_saved(self):
        from unittest.mock import patch
        from harness.web.api.provider_connections import ProviderAuthorizations
        async def authorize(request, *, commit, interact):
            await commit({'type': 'oauth', 'access': 'private-access', 'refresh': 'private-refresh', 'expires': 9999999999999})
            yield {'type': 'result', 'value': {'authorized': True}}
        with tempfile.TemporaryDirectory() as root:
            store = ProviderConnections(root)
            manager = ProviderAuthorizations(store)
            value = provider_fixture()
            value['type'] = 'anthropic'
            connection = value['connections'][0]
            connection.update(protocol='', auth={'type': 'oauth', 'env': {}}, keys=[])
            with patch('harness.web.api.provider_connections.bridge', authorize):
                operation = await manager.start('supplier', 'endpoint', 'oauth', draft=value)
                await manager.operations[operation['id']]['task']
            self.assertEqual(manager.snapshot(operation['id'])['status'], 'completed')
            self.assertNotIn('private-', json.dumps(manager.snapshot(operation['id'])))
            self.assertFalse(store.path.exists())
            connection['auth']['authorization_id'] = operation['id']
            invalid_value = json.loads(json.dumps(value)); invalid_value['connections'][0]['id'] = 'different'
            with self.assertRaises(ValueError):
                store.save([invalid_value], store.revision, authorizations=manager.operations)
            snapshot = store.save([value], store.revision, authorizations=manager.operations)
            self.assertNotIn('private-', json.dumps(snapshot))
            self.assertNotIn('authorization_id', store.path.read_text())
            self.assertTrue(snapshot['providers'][0]['connections'][0]['auth']['configured'])
            self.assertEqual(ProviderConnections(root).resolve('harness/supplier/endpoint/model-a')[1]['auth']['credential']['refresh'], 'private-refresh')
            await manager.close()
