"""匿名免费模型的配置、目录、实际流分发与凭据隔离回归。"""
import asyncio
from contextlib import aclosing
from io import BytesIO
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from harness import Harness, HarnessConfig
from harness.core.language import current_language
from harness.llm.adapter import OpenRouterAdapter
from harness.llm.kilo_free import BASE_URL, free_models
from harness.llm.providers import ProviderConnections, directory
from harness.llm.types import LlmCallConfig, UserMessage, ToolSchema
from harness.web.api.provider_connections import build_router
from harness.web.router import Request


def provider_fixture():
    """固定匿名接口，全部状态位于调用方的临时目录。"""
    return {'id': 'free', 'type': 'kilo-free', 'name': 'Kilo Free', 'connections': [{
        'id': 'endpoint', 'name': 'Kilo Free', 'base_url': BASE_URL, 'protocol': '', 'enabled': True,
        'auth': {'type': 'anonymous', 'env': {}}, 'keys': [],
        'models': [{'id': 'auto', 'model': 'kilo-auto/free', 'name': 'Auto Free', 'enabled': True,
                    'input': ['text'], 'reasoning_efforts': {'off': None, 'high': 'high'}}]}]}


class CaptureTransport:
    """提供完整工具帧，捕获实际线上头与正文。"""
    def __init__(self):
        self.requests = []
        self.closed = 0

    async def post_stream(self, url, *, headers, body, timeout):
        self.requests.append((url, headers, json.loads(body)))
        try:
            yield b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-one","function":{"name":"read","arguments":"{\\"path\\":\\"demo.txt\\"}"}}]},"finish_reason":"tool_calls"}]}\n\n'
            yield b'data: [DONE]\n\n'
        finally:
            self.closed += 1


class KiloFreeTests(unittest.TestCase):
    def test_anonymous_configuration_persists_without_credentials(self):
        with tempfile.TemporaryDirectory() as root:
            store = ProviderConnections(root)
            store.save([provider_fixture()], store.revision)
            self.assertEqual(ProviderConnections(root).catalog()[0]['id'], 'harness/free/endpoint/auto')
            self.assertEqual(store.providers[0]['connections'][0]['keys'], [])
            template = next(row for row in directory() if row['id'] == 'kilo-free')
            self.assertFalse(template['api_key'])
            self.assertFalse(template['oauth'])
            self.assertEqual(template['models'][0]['id'], 'kilo-auto/free')
            template['models'][0]['input'].append('image')
            self.assertEqual(next(row for row in directory() if row['id'] == 'kilo-free')['models'][0]['input'], ['text'])
            for field, value in [('base_url', 'https://elsewhere.invalid'), ('protocol', 'anthropic-messages'), ('auth', {'type': 'environment', 'env': {}}), ('keys', [{'value': 'secret'}])]:
                provider = provider_fixture()
                provider['connections'][0][field] = value
                before = store.path.read_bytes()
                with self.assertRaises(ValueError):
                    store.save([provider], store.revision)
                self.assertEqual(store.path.read_bytes(), before)

    def test_listing_uses_explicit_free_marker_and_reported_capabilities(self):
        rows = free_models([
            {'id': 'paid', 'isFree': False}, {'id': 'looks:free'}, {'id': 'wrong', 'isFree': 'true'},
            {'id': 'real', 'isFree': True, 'context_length': 64000, 'top_provider': {'max_completion_tokens': 2048},
             'architecture': {'input_modalities': ['text', 'image']},
             'opencode': {'variants': {'instant': {'reasoning': {'enabled': False}}, 'thinking': {'reasoning': {'effort': 'high'}}}}},
            {'id': 'unknown', 'isFree': True}])
        self.assertEqual([row['id'] for row in rows], ['real', 'unknown'])
        self.assertEqual(rows[0]['contextWindow'], 64000)
        self.assertEqual(rows[0]['maxTokens'], 2048)
        self.assertEqual(rows[0]['input'], ['text', 'image'])
        self.assertEqual(rows[0]['thinkingLevelMap'], {'off': None, 'high': 'high'})
        self.assertNotIn('contextWindow', rows[1])
        self.assertNotIn('thinkingLevelMap', rows[1])
        self.assertEqual(free_models([]), [])
        for bad in ([{'id': 'x', 'isFree': True}] * 2, [{'id': True, 'isFree': True}], [{'id': 'x', 'isFree': True, 'context_length': True}]):
            with self.assertRaises(ValueError):
                free_models(bad)

    def test_discovery_route_filters_free_models_without_authorization_or_writes(self):
        with tempfile.TemporaryDirectory() as root:
            app = Harness(HarnessConfig(home=Path(root)/'state', cwd=Path(root), enable_setup=True))
            try:
                router = build_router(SimpleNamespace(harness=app, call=asyncio.run))
                handler, params = router.resolve('POST', '/api/v1/connection/discover')
                request = Request('POST', '/api/v1/connection/discover', {}, {}, json.dumps({
                    'provider': 'kilo-free', 'connection': provider_fixture()['connections'][0]}).encode())
                payload = {'data': [{'id': 'paid'}, {'id': 'free', 'isFree': True}]}
                opener = SimpleNamespace(open=lambda req, timeout: self.capture_listing(req, payload))
                with patch('harness.web.api.provider_connections.urllib.request.build_opener', return_value=opener):
                    result = json.loads(handler(request, **params).body)
                self.assertEqual([row['id'] for row in result['models']], ['free'])
                self.assertFalse(result['fallback'])
                self.assertFalse(app.provider_connections.path.exists())
            finally:
                asyncio.run(app.close())

    def test_http_save_readback_and_restart_keep_individual_free_models(self):
        """经真实 HTTP 保存具体模型，再重启宿主核对选择、目录和分组保持。"""
        from harness.web.server import serve, stop
        from harness.web.protocol import SCHEMAS
        from harness.tools.schema import validate
        import urllib.request
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = Harness(HarnessConfig(home=root/'state', cwd=root, enable_setup=True,
                enable_mcp=False, enable_automations=False), codex_gateway_home=root/'gateway')
            server = serve(app, port=0, background=True, dist=root)
            def invoke(path, body=None):
                request = urllib.request.Request(f'http://127.0.0.1:{server.server_port}/api/v1{path}',
                    data=json.dumps(body).encode() if body is not None else None,
                    headers={'Content-Type': 'application/json'})
                with urllib.request.urlopen(request, timeout=5) as response:
                    self.assertEqual(response.status, 200)
                    return json.load(response)
            try:
                current = invoke('/connection')
                provider = provider_fixture()
                connection = provider['connections'][0]
                catalog = free_models([{'id': 'kilo-auto/free', 'isFree': True},
                    {'id': 'vendor/coder:free', 'name': 'Coder', 'isFree': True, 'context_length': 64000},
                    {'id': 'paid', 'isFree': False}])
                connection['model_catalog'] = catalog
                connection['models'] = [{'id': 'coder', 'model': 'vendor/coder:free', 'name': 'Coder', 'enabled': True, 'input': ['text']}]
                body = {'providers': [provider], 'revision': current['revision']}
                request = urllib.request.Request(f'http://127.0.0.1:{server.server_port}/api/v1/connection',
                    data=json.dumps(body).encode(), method='PATCH', headers={'Content-Type': 'application/json'})
                with urllib.request.urlopen(request, timeout=5) as response:
                    saved = json.load(response)
                selected = saved['routing']['providers'][0]['connections'][0]
                self.assertEqual(validate(selected['auth'], SCHEMAS['ProviderAuth']), [])
                self.assertEqual(selected['auth']['type'], 'anonymous')
                self.assertEqual(selected['keys'], [])
                self.assertEqual(selected['models'][0]['model'], 'vendor/coder:free')
                self.assertEqual(invoke('/connection'), saved['routing'])
                models = invoke('/models')
                self.assertEqual(models['active'], 'harness/free/endpoint/coder')
                entry = next(row for row in models['models'] if row['id'] == models['active'])
                self.assertEqual(entry['provider'], 'Kilo Free')
                self.assertEqual(entry['label'], 'Coder')
                self.assertEqual(entry['context_window'], 64000)
            finally:
                stop(server)
            reopened = Harness(HarnessConfig(home=root/'state', cwd=root, enable_setup=True,
                enable_mcp=False, enable_automations=False), codex_gateway_home=root/'gateway')
            try:
                self.assertEqual(reopened.provider_connections.snapshot(), saved['routing'])
                self.assertEqual(reopened.config.model, 'harness/free/endpoint/coder')
            finally:
                asyncio.run(reopened.close())

    def capture_listing(self, request, payload):
        """核对实际请求身份后返回受控目录。"""
        self.assertEqual(request.full_url, BASE_URL + '/models')
        self.assertFalse(request.has_header('Authorization'))
        return BytesIO(json.dumps(payload).encode())

    def test_provider_stream_preserves_tools_and_sends_no_credentials(self):
        async def run():
            with tempfile.TemporaryDirectory() as root:
                store = ProviderConnections(root)
                store.save([provider_fixture()], store.revision)
                transport = CaptureTransport()
                adapter = OpenRouterAdapter('host-secret', transport=transport)
                adapter.provider_connections = store
                for effort in ('high', 'off'):
                    call = LlmCallConfig(model='harness/free/endpoint/auto', reasoning_effort=effort,
                        extra_headers={'authorization': 'injected-secret', 'X-Trace': 'kept'})
                    tool_schema = ToolSchema(name='read', description='读取测试文件', parameters={'type': 'object', 'properties': {'path': {'type': 'string'}}})
                    async with aclosing(adapter.stream([UserMessage('test')], config=call, tools=[tool_schema])) as stream:
                        events = [event async for event in stream]
                    tool = next(event.tool_use for event in events if event.kind == 'tool_use')
                    self.assertEqual((tool.name, tool.id, tool.input), ('read', 'call-one', {'path': 'demo.txt'}))
                    self.assertEqual(events[-1].kind, 'done')
                    url, headers, body = transport.requests[-1]
                    self.assertEqual(url, BASE_URL + '/chat/completions')
                    self.assertFalse(any(name.lower() == 'authorization' for name in headers))
                    self.assertEqual(headers['X-Trace'], 'kept')
                    self.assertEqual(body['model'], 'kilo-auto/free')
                    self.assertEqual(body['tools'][0]['function']['name'], 'read')
                    self.assertEqual(body['reasoning'], {'enabled': False} if effort == 'off' else {'effort': 'high'})
                self.assertEqual(transport.closed, 2)
                self.assertEqual(store.leases, {})
        asyncio.run(run())

    def test_invalid_catalog_errors_follow_actual_language(self):
        messages = []
        for language in ('en', 'zh-CN', 'en'):
            token = current_language.set(language)
            try:
                with self.assertRaises(ValueError) as failure:
                    free_models([{'id': None, 'isFree': True}])
                messages.append(str(failure.exception))
            finally:
                current_language.reset(token)
        self.assertEqual(messages[0], messages[2])
        self.assertNotEqual(messages[0], messages[1])
