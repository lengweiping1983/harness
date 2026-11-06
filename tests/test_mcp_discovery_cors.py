"""验证公开发现信息的跨域读取不会开放受保护的 MCP 调用。"""
import tempfile
import json
import gzip
import unittest
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from harness.core.language import current_language
from harness.i18n import tr

import httpx

from harness.execution.config import ExecutionConfig, ProjectConfig
from harness.mcp_server.auth import SCOPES
from harness.mcp_server.server import create_http_app
from harness.web.errors import BadRequest
from harness.web.server import Handler
from harness.web.router import Response


class MCPDiscoveryCorsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        config = ExecutionConfig(home=root / 'state', projects={'project': ProjectConfig(root)})
        app = create_http_app(config, public_url='https://executor.example', manage_runtime=False)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                       base_url='https://executor.example')
        self.addAsyncCleanup(self.client.aclose)

    async def test_cross_origin_discovery_contains_only_public_metadata(self):
        expected = {
            '/.well-known/oauth-authorization-server': {
                'issuer': 'https://executor.example',
                'authorization_endpoint': 'https://executor.example/authorize',
                'token_endpoint': 'https://executor.example/token',
                'registration_endpoint': 'https://executor.example/register',
                'revocation_endpoint': 'https://executor.example/revoke',
                'scopes_supported': SCOPES,
                'response_types_supported': ['code'],
                'grant_types_supported': ['authorization_code', 'refresh_token'],
                'code_challenge_methods_supported': ['S256'],
                'token_endpoint_auth_methods_supported': ['none', 'client_secret_post', 'client_secret_basic'],
                'revocation_endpoint_auth_methods_supported': ['none', 'client_secret_post', 'client_secret_basic'],
                'resource_indicators_supported': True,
            },
        }
        resource = {'resource': 'https://executor.example/mcp',
                    'authorization_servers': ['https://executor.example'],
                    'scopes_supported': SCOPES, 'bearer_methods_supported': ['header'],
                    'resource_name': 'Harness Executor'}
        for path in ('/.well-known/oauth-protected-resource', '/.well-known/oauth-protected-resource/mcp'):
            expected[path] = resource
        for path, metadata in expected.items():
            with self.subTest(path=path):
                response = await self.client.get(path, headers={'Origin': 'https://client.example'})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers['access-control-allow-origin'], '*')
                self.assertEqual(response.headers['cache-control'], 'no-store')
                self.assertNotIn('access-control-allow-credentials', response.headers)
                self.assertEqual(response.json(), metadata)

    async def test_discovery_does_not_authorize_mcp_requests(self):
        response = await self.client.post('/mcp', headers={'Origin': 'https://client.example'},
            json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})
        self.assertEqual(response.status_code, 401)
        self.assertIn('Bearer', response.headers['www-authenticate'])
        self.assertNotIn('access-control-allow-origin', response.headers)


class WebCorsTests(unittest.TestCase):
    def test_web_cors_only_exposes_responses_to_allowed_origins(self):
        handler = object.__new__(Handler)
        handler._web_ctx = SimpleNamespace(shared_access=None)
        handler.server = SimpleNamespace(server_address=('127.0.0.1', 8765))
        for origin, allowed in [('http://127.0.0.1:8765', True),
                                ('http://localhost:5173', False),
                                ('http://127.0.0.1:5273', False),
                                ('https://client.example', False)]:
            with self.subTest(origin=origin):
                handler.headers = Message()
                handler.headers['Host'] = '127.0.0.1:8765'
                handler.headers['Origin'] = origin
                self.assertEqual(handler._origin_allowed(), allowed)
                response = handler._apply_cors(Response(status=204))
                self.assertEqual(response.headers.get('Access-Control-Allow-Origin'), origin if allowed else None)
                if allowed:
                    self.assertEqual(response.headers['Vary'], 'Origin')
                else:
                    with self.assertRaises(BadRequest) as denied:
                        handler._dispatch(SimpleNamespace(method='GET', path='/api/v1/sessions'))
                    self.assertEqual(denied.exception.status, 403)

    def test_error_responses_keep_origin_request_id_and_compression_variance(self):
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                for origin, allowed in (('http://127.0.0.1:8765', True), ('https://client.example', False)):
                    for compressed in (False, True):
                        with self.subTest(language=language, origin=origin, compressed=compressed):
                            handler = object.__new__(Handler)
                            handler._web_ctx = SimpleNamespace(shared_access=None)
                            handler.server = SimpleNamespace(server_address=('127.0.0.1', 8765))
                            handler.headers = Message()
                            handler.headers['Host'] = '127.0.0.1:8765'
                            handler.headers['Origin'] = origin
                            if compressed:
                                handler.headers['Accept-Encoding'] = 'gzip'
                            handler._request_id = 'original-request'
                            handler._send = Mock()
                            message = tr('web.query_invalid')
                            details = {'diagnostic':'Original 原文 {name}' * 200} if compressed else None
                            handler._error(BadRequest(message, code='request/invalid-query', details=details))
                            handler._send.assert_called_once()
                            response = handler._send.call_args.args[0]
                            self.assertEqual(response.headers.get('X-Request-ID'), 'original-request')
                            self.assertEqual(response.headers.get('Access-Control-Allow-Origin'), origin if allowed else None)
                            vary = {part.strip().lower() for part in response.headers.get('Vary', '').split(',')}
                            if allowed:
                                self.assertIn('origin', vary)
                            self.assertEqual('accept-encoding' in vary, compressed)
                            body = gzip.decompress(response.body) if compressed else response.body
                            self.assertEqual(json.loads(body), {'error':message, 'code':'request/invalid-query',
                                **({'details':details} if details is not None else {})})

    def test_preflight_uses_the_same_origin_policy_and_no_origin_preserves_native_clients(self):
        for origin, site, allowed in (
                ('http://127.0.0.1:5273', 'same-origin', True),
                ('http://localhost:5173', 'same-site', False),
                ('https://evil.example', 'cross-site', False),
                ('http://[', 'cross-site', False),
                ('null', 'cross-site', False),
                ('', '', True), ('', 'same-origin', True), ('', 'cross-site', False), ('', 'same-site', False),
                ('', 'CROSS-SITE', False), ('', ' Same-Site ', False), ('', ' SAME-ORIGIN ', True)):
            with self.subTest(origin=origin, site=site):
                handler = object.__new__(Handler)
                handler._web_ctx = SimpleNamespace(shared_access=None)
                handler.server = SimpleNamespace(server_address=('127.0.0.1', 8765))
                handler.headers = Message()
                # Vite 保留浏览器 Host，后端实际端口与请求入口端口可以不同。
                handler.headers['Host'] = '127.0.0.1:5273'
                if origin:
                    handler.headers['Origin'] = origin
                if site:
                    handler.headers['Sec-Fetch-Site'] = site
                handler._send = Mock()
                handler.do_OPTIONS()
                response = handler._send.call_args.args[0]
                self.assertEqual(response.status, 204 if allowed else 403)
                self.assertEqual(response.headers.get('Access-Control-Allow-Origin'), origin if allowed and origin else None)
                self.assertEqual('Access-Control-Allow-Methods' in response.headers, bool(allowed and origin))
                if not allowed:
                    with self.assertRaises(BadRequest) as denied:
                        handler._dispatch(SimpleNamespace(method='POST', path='/api/v1/plugin-packages/preview'))
                    self.assertEqual(denied.exception.code, 'request/origin')
