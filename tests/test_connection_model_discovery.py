"""模型探测对内置供应商实时拉取，失败时保留内置目录并明确标记。"""
import asyncio
import json
import tempfile
import threading
import unittest
from unittest import mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

from harness import Harness, HarnessConfig
from harness.i18n import tr
from harness.llm.providers import directory, ProviderConnections, normalize_model_catalog
from harness.web.api.provider_connections import build_router
from harness.web.errors import BadRequest
from harness.web.router import Request
from tests.web_fixture import FixtureTransport


def connection_fixture(kind, base_url, protocol=''):
    """探测只提交连接草稿，不写入用户已保存的配置。"""
    return {'id': 'endpoint', 'name': '接口', 'protocol': protocol, 'base_url': base_url, 'enabled': True,
            'auth': {'type': 'api_key', 'env': {}}, 'keys': [{'id': 'first', 'value': 'fixture-discovery-key', 'enabled': True}],
            'models': [{'id': 'model-a', 'model': 'remote-a', 'name': '模型 A', 'input': ['text'], 'enabled': True}]}


class CatalogServer:
    """受控模型接口：按本次需要返回目录或失败状态。"""

    def __init__(self, payload=None, status=200):
        self.payload = payload if payload is not None else {'data': []}
        self.status = status
        self.requests = []
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), self.handler())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def handler(self):
        outer = self
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                outer.requests.append((self.path, self.headers))
                body = json.dumps(outer.payload).encode()
                self.send_response(outer.status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                return
        return Handler

    @property
    def url(self):
        return 'http://127.0.0.1:%d/v1' % self.server.server_address[1]

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class ModelDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        root = Path(self.root.name)
        self.host = Harness(HarnessConfig(api_key='fixture', model='fixture', home=root/'state', cwd=root), transport=FixtureTransport())
        self.router = build_router(SimpleNamespace(harness=self.host, call=asyncio.run))

    def tearDown(self):
        asyncio.run(self.host.close())
        self.root.cleanup()

    def discover(self, kind, base_url, protocol=''):
        handler, params = self.router.resolve('POST', '/api/v1/connection/discover')
        request = Request('POST', '/api/v1/connection/discover', {}, {}, json.dumps({'provider': kind, 'connection': connection_fixture(kind, base_url, protocol)}).encode())
        return json.loads(handler(request, **params).body)

    def test_built_in_provider_probes_endpoint_and_keeps_catalog_capabilities(self):
        """内置供应商实时探测，成功后只返回新目录成员，并保留同名模型的已知能力。"""
        template = next(row for row in directory() if row['id'] == 'qwen-token-plan-cn')
        catalog = {row['id']: row for row in template['models']}
        self.assertNotIn('deepseek-v4.1-flash-1017', catalog)
        server = CatalogServer({'data': [{'id': 'deepseek-v4.1-flash-1017'}, {'id': 'deepseek-v3.2'}]})
        try:
            result = self.discover('qwen-token-plan-cn', server.url)
        finally:
            server.close()
        self.assertEqual([path for path, _ in server.requests], ['/v1/models'])
        self.assertEqual(server.requests[0][1].get('Authorization'), 'Bearer fixture-discovery-key')
        ids = [row['id'] for row in result['models']]
        self.assertIn('deepseek-v4.1-flash-1017', ids)
        self.assertEqual(result['fallback'], False)
        for row in result['models']:
            if row['id'] in catalog:
                self.assertEqual(row, normalize_model_catalog([catalog[row['id']]])[0])
        self.assertEqual(ids, ['deepseek-v4.1-flash-1017', 'deepseek-v3.2'])
        self.assertFalse(self.host.provider_connections.path.exists())

    def test_new_remote_model_inherits_series_thinking_levels(self):
        """探测得到的新模型沿用同系列已知思考档位，不继承窗口和输出上限。"""
        template = next(row for row in directory() if row['id'] == 'qwen-token-plan-cn')
        series = next(row for row in template['models'] if row['id'] == 'deepseek-v4.1-flash')
        server = CatalogServer({'data': [{'id': 'deepseek-v4.1-flash-1017'}, {'id': 'unrelated-model'}]})
        try:
            result = self.discover('qwen-token-plan-cn', server.url)
        finally:
            server.close()
        rows = {row['id']: row for row in result['models']}
        fresh = rows['deepseek-v4.1-flash-1017']
        self.assertEqual(fresh['thinkingLevelMap'], series['thinkingLevelMap'])
        self.assertEqual(fresh['reasoningEfforts'], series['reasoningEfforts'])
        self.assertEqual(fresh['input'], series['input'])
        self.assertNotIn('contextWindow', fresh)
        self.assertNotIn('maxTokens', fresh)
        self.assertNotIn('thinkingLevelMap', rows['unrelated-model'])

    def test_openrouter_discovery_persists_protocol_for_a_new_chat_model(self):
        """新匿名模型通过真实发现路由和配置重开保留端点协议。"""
        server = CatalogServer({'data': [{'id': 'stealth/crouton-alpha', 'context_length': 1048576,
            'architecture': {'input_modalities': ['text', 'image', 'video', 'file', 'audio']}}]})
        try:
            result = self.discover('openrouter', server.url)
        finally:
            server.close()
        self.assertFalse(result['fallback'])
        self.assertEqual(len(result['models']), 1)
        model = result['models'][0]
        self.assertEqual(model['id'], 'stealth/crouton-alpha')
        self.assertEqual(model['api'], 'openai-completions')
        self.assertEqual(model['baseUrl'], server.url)
        self.assertEqual(model['contextWindow'], 1048576)
        self.assertEqual(model['input'], ['text', 'image', 'video'])
        connection = connection_fixture('openrouter', server.url)
        connection['model_catalog'] = result['models']
        connection['models'][0]['model'] = model['id']
        store = self.host.provider_connections
        store.save([{'id': 'supplier', 'type': 'openrouter', 'name': 'OpenRouter', 'connections': [connection]}], store.revision)
        reopened = ProviderConnections(store.home)
        self.assertEqual(reopened.providers[0]['connections'][0]['model_catalog'], [model])
        self.assertEqual(reopened.catalog()[0]['context_window'], 1048576)

    def test_reported_metadata_aliases_reach_discovered_models(self):
        """通过真实发现路由核对容量和输入类型，不把未报告值补造成已知。"""
        server = CatalogServer({'data': [
            {'id': 'new-vision', 'context_length': 131072, 'top_provider': {'max_completion_tokens': 8192}, 'architecture': {'input_modalities': ['text', 'image']}},
            {'id': 'new-video', 'architecture': {'input_modalities': ['text', 'image', 'video']}},
            {'id': 'new-text', 'max_input_tokens': 64000, 'max_output_tokens': 4096},
            {'id': 'unknown'},
        ]})
        try:
            result = self.discover('custom', server.url, 'openai-completions')
        finally:
            server.close()
        self.assertFalse(result['fallback'])
        vision, video, text, unknown = result['models']
        self.assertEqual((vision['contextWindow'], vision['maxTokens'], vision['input']), (131072, 8192, ['text', 'image']))
        self.assertEqual(video['input'], ['text', 'image', 'video'])
        self.assertEqual((text['contextWindow'], text['maxTokens']), (64000, 4096))
        self.assertNotIn('contextWindow', unknown)
        self.assertNotIn('maxTokens', unknown)
        self.assertNotIn('input', unknown)

    def test_invalid_reported_limits_are_not_coerced(self):
        """错误能力字段明确拒绝，不将布尔和字符串转换为容量。"""
        from harness.web.api.provider_connections import merged_models
        for value in (True, '8192', 0, -1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                merged_models([{'id': 'bad', 'context_length': value}], [])
        rows = merged_models([{'id': 'known', 'contextWindow': 100, 'context_length': 200}], [])
        self.assertEqual(rows[0]['contextWindow'], 100)

    def test_probe_failure_keeps_catalog_and_reports_fallback(self):
        server = CatalogServer({'error': 'boom'}, status=500)
        try:
            result = self.discover('qwen-token-plan-cn', server.url)
        finally:
            server.close()
        template = next(row for row in directory() if row['id'] == 'qwen-token-plan-cn')
        self.assertEqual(result['fallback'], True)
        self.assertEqual([row['id'] for row in result['models']], [row['id'] for row in template['models']])

    def test_unsupported_native_protocol_returns_catalog_without_probe(self):
        """协议不由 OpenAI 兼容接口提供时，不发起探测，只返回内置目录。"""
        server = CatalogServer({'data': [{'id': 'anything'}]})
        try:
            result = self.discover('amazon-bedrock', server.url)
        finally:
            server.close()
        self.assertEqual(server.requests, [])
        template = next(row for row in directory() if row['id'] == 'amazon-bedrock')
        self.assertEqual(result['fallback'], True)
        self.assertEqual([row['id'] for row in result['models']], [row['id'] for row in template['models']])

    def test_custom_provider_failure_still_rejects_without_catalog(self):
        server = CatalogServer({'error': 'boom'}, status=500)
        try:
            with self.assertRaises(BadRequest) as error:
                self.discover('custom', server.url, protocol='openai-completions')
        finally:
            server.close()
        self.assertIn(tr('models.discovery.failed'), str(error.exception))

    def test_inherited_endpoint_uses_template_address(self):
        """接口留空地址时沿用模板地址探测，不是请求相对路径。"""
        template = next(row for row in directory() if row['id'] == 'qwen-token-plan-cn')
        server = CatalogServer({'data': [{'id': 'remote-only'}]})
        stub = dict(template, base_url=server.url)
        try:
            with mock.patch('harness.web.api.provider_connections.directory', lambda: [stub]):
                result = self.discover('qwen-token-plan-cn', '')
        finally:
            server.close()
        self.assertEqual([path for path, _ in server.requests], ['/v1/models'])
        self.assertIn('remote-only', [row['id'] for row in result['models']])
        self.assertEqual(result['fallback'], False)
        self.assertTrue(all(row['id'] in {row['id'] for row in template['models']} or row['id'] == 'remote-only' for row in result['models']))

    def test_custom_provider_without_endpoint_still_rejected(self):
        """自定义接口没有地址时仍拒绝，不返回空目录冒充成功。"""
        with self.assertRaises(BadRequest) as error:
            self.discover('custom', '', protocol='openai-completions')
        self.assertIn(tr('models.config.invalid'), str(error.exception))

    def test_explicit_protocol_still_probes_native_anthropic_endpoint(self):
        server = CatalogServer({'data': [{'id': 'claude-fixture'}]})
        try:
            result = self.discover('anthropic', server.url, protocol='anthropic-messages')
        finally:
            server.close()
        self.assertEqual([path for path, _ in server.requests], ['/v1/models?limit=1000'])
        self.assertEqual(server.requests[0][1].get('x-api-key'), 'fixture-discovery-key')
        self.assertIn('claude-fixture', [row['id'] for row in result['models']])

    def test_authoritative_cache_is_persisted_with_choices_and_keeps_new_reasoning_levels(self):
        """保存缓存和选中项后，重开目录不再拼入内置旧模型，新增模型仍有思考档位。"""
        server = CatalogServer({'data': [{'id': 'reasoner-new', 'reasoning': True, 'thinkingLevelMap': {'low': 'low', 'high': 'high', 'max': 'max'}}]})
        try:
            result = self.discover('qwen-token-plan-cn', server.url)
        finally:
            server.close()
        connection = connection_fixture('qwen-token-plan-cn', server.url)
        connection['models'][0]['model'] = 'reasoner-new'
        connection['model_catalog'] = result['models']
        store = self.host.provider_connections
        snapshot = store.save([{'id': 'supplier', 'name': '接口', 'type': 'qwen-token-plan-cn', 'connections': [connection]}], store.revision)
        reopened = ProviderConnections(store.home)
        cached = reopened.snapshot()['providers'][0]['connections'][0]['model_catalog']
        self.assertEqual([row['id'] for row in cached], ['reasoner-new'])
        self.assertEqual(snapshot['providers'][0]['connections'][0]['models'][0]['id'], 'model-a')
        self.assertEqual(reopened.catalog()[0]['reasoning_efforts'], ['low', 'high', 'max'])
        self.assertTrue(reopened.catalog()[0]['supports_reasoning'])
        self.assertNotIn('fixture-discovery-key', json.dumps(reopened.snapshot()))
        connection['model_catalog'] = []
        connection['models'] = []
        cleared = store.save([{'id': 'supplier', 'name': '接口', 'type': 'qwen-token-plan-cn', 'connections': [connection]}], store.revision)
        self.assertEqual(cleared['providers'][0]['connections'][0]['model_catalog'], [])
        self.assertEqual(ProviderConnections(store.home).catalog(), [])

    def test_explicit_new_model_reasoning_is_not_suppressed_by_absent_builtin_metadata(self):
        """手工声明的新增模型档位不依赖内置目录的 reasoning 标志。"""
        connection = connection_fixture('custom', 'https://example.invalid/v1', 'openai-completions')
        connection['models'][0]['reasoning_efforts'] = {'off': None, 'low': 'small', 'high': 'large'}
        store = self.host.provider_connections
        store.save([{'id': 'supplier', 'name': '接口', 'type': 'custom', 'connections': [connection]}], store.revision)
        self.assertEqual(store.catalog()[0]['reasoning_efforts'], ['off', 'low', 'high'])

    def test_empty_remote_catalog_is_success_and_does_not_resurrect_builtin_models(self):
        server = CatalogServer({'data': []})
        try:
            result = self.discover('qwen-token-plan-cn', server.url)
        finally:
            server.close()
        self.assertEqual(result, {'models': [], 'fallback': False})

    def test_unknown_version_does_not_invent_reasoning_from_an_unrelated_prefix(self):
        server = CatalogServer({'data': [{'id': 'deepseek-v4.1-flash-unrelated'}, {'id': 'remote', 'reasoningEfforts': ['low', 'high']}]})
        try:
            result = self.discover('qwen-token-plan-cn', server.url)
        finally:
            server.close()
        self.assertNotIn('thinkingLevelMap', result['models'][0])
        self.assertNotIn('reasoningEfforts', result['models'][0])
        self.assertEqual(result['models'][1]['reasoningEfforts'], ['low', 'high'])

    def test_failed_refresh_preserves_the_saved_fresh_catalog_instead_of_using_builtin_list(self):
        connection = connection_fixture('qwen-token-plan-cn', 'https://example.invalid')
        connection['model_catalog'] = [{'id': 'fresh-only', 'name': '新目录'}]
        store = self.host.provider_connections
        store.save([{'id': 'supplier', 'name': '接口', 'type': 'qwen-token-plan-cn', 'connections': [connection]}], store.revision)
        revision = store.revision
        server = CatalogServer(status=500)
        try:
            connection['base_url'] = server.url
            handler, params = self.router.resolve('POST', '/api/v1/connection/discover')
            request = Request('POST', '/api/v1/connection/discover', {}, {}, json.dumps({'provider': 'qwen-token-plan-cn', 'connection': connection}).encode())
            result = json.loads(handler(request, **params).body)
        finally:
            server.close()
        self.assertEqual(result, {'models': [{'id': 'fresh-only', 'name': '新目录'}], 'fallback': True})
        self.assertEqual(store.revision, revision)

    def test_catalog_metadata_supports_the_registered_provider_capabilities(self):
        """所有当前内置能力元数据都能作为接口缓存保存，保留分段计费和环境地址。"""
        for provider in directory():
            with self.subTest(provider=provider['id']):
                rows = normalize_model_catalog(provider['models'])
                self.assertEqual([row['id'] for row in rows], [row['id'] for row in provider['models']])
                for before, after in zip(provider['models'], rows):
                    for field in ('cost', 'compat', 'thinkingLevelMap', 'reasoningEfforts'):
                        if field in before:
                            self.assertEqual(after[field], before[field])

    def test_partial_catalog_never_causes_retired_model_removal(self):
        server = CatalogServer({'data': [{'id': 'only-first-page'}], 'has_more': True})
        try:
            result = self.discover('qwen-token-plan-cn', server.url)
        finally:
            server.close()
        self.assertTrue(result['fallback'])
        self.assertNotIn('only-first-page', [row['id'] for row in result['models']])

    def test_invalid_capability_metadata_is_rejected_without_replacing_cache(self):
        for value in ([{'id': 'bad', 'reasoningEfforts': [{}]}], [{'id': 'bad', 'cost': {'input': 10 ** 1000}}], [{'id': 'bad', 'name': None}], [{'id': ' bad'}]):
            with self.subTest(value_type=type(value[0].get('reasoningEfforts')).__name__):
                with self.assertRaises(ValueError):
                    normalize_model_catalog(value)
        self.assertEqual(self.host.provider_connections.snapshot()['providers'], [])


if __name__ == '__main__':
    unittest.main()
