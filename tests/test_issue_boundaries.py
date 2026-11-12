"""授权投影、配置边界及重定向的业务回归。"""
import asyncio
import gc
import io
import json
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.response import addinfourl

from harness.app.runtime import RunController
from harness.llm.context_windows import load_context_windows
from harness.llm.routes import load_models
from harness.mcp_server.server import create_server
from harness.net.http import ipv4_preferred_get
from harness.tools.registry import ToolDefinition, ToolRegistry
from harness.tools.schema import validate


class BoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_mcp_listing_honors_allow_and_deny(self):
        registry = ToolRegistry(allow=['allowed', 'denied'], deny=['denied'])
        for name in ('allowed', 'denied', 'excluded'):
            registry.register(ToolDefinition({'name': name}, lambda args, ctx: None))
        with patch('harness.mcp_server.server.Server') as server:
            create_server(SimpleNamespace(language='en'), runtime_holder={'runtime': SimpleNamespace(registry=registry)}, manage_runtime=False)
        result = await server.call_args.kwargs['on_list_tools'](None, None)
        self.assertEqual([tool.name for tool in result.tools], ['allowed'])

    def test_learning_cache_rejects_invalid_keys_without_rewriting(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'windows.json'
            content = json.dumps({'version': 1, 'windows': {'broken': 100, '|m': 100, 'url|': 100, 'url|m': 100}})
            path.write_text(content)
            with self.assertRaises(ValueError):
                load_context_windows(path)
            self.assertEqual(path.read_text(), content)

    def test_corrupt_credentials_return_clear_error_without_secrets_or_rewrite(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            path = home / 'connection-secrets.json'
            for content in ('{"secret-value":', '[]', '{"version":1}', '{"version":1,"models":[null]}', '{"version":1,"models":[]}'):
                path.write_text(content)
                with self.assertRaises(ValueError) as error:
                    load_models(home, 'm', 'https://example.test', ['fallback'])
                self.assertNotIn('secret-value', str(error.exception))
                self.assertEqual(path.read_text(), content)
            path.unlink()
            self.assertEqual(load_models(home, 'm', 'https://example.test', ['fallback'])[0]['model'], 'm')

    def test_unknown_keyword_and_invalid_type_have_distinct_contracts(self):
        self.assertEqual(validate('ok', {'unsupported': True}), [])
        self.assertTrue(validate('ok', {'type': 'unsupported'}))
        self.assertTrue(validate(1, {'type': 'string'}))

    async def test_idle_session_locks_are_reclaimed_without_splitting_waiters(self):
        with tempfile.TemporaryDirectory() as folder:
            controller = RunController(SimpleNamespace(config=SimpleNamespace(home=Path(folder))))
            lock = controller._locks.setdefault('session', asyncio.Lock())
            await lock.acquire()
            waiter = asyncio.create_task(lock.acquire())
            await asyncio.sleep(0)
            self.assertIs(controller._locks.setdefault('session', asyncio.Lock()), lock)
            lock.release()
            await waiter
            lock.release()
            del lock
            gc.collect()
            self.assertEqual(len(controller._locks), 0)

    def test_https_redirect_is_validated_and_uses_ipv4_connection(self):
        visited = []
        def open_response(handler, connection, req, **kwargs):
            visited.append((connection.__name__, req.full_url))
            headers = Message()
            headers['Location'] = '/final'
            status = 302 if len(visited) == 1 else 200
            response = addinfourl(io.BytesIO(b'ok'), headers, req.full_url, status)
            response.msg = 'Found' if status == 302 else 'OK'
            return response
        with patch('urllib.request.AbstractHTTPHandler.do_open', open_response):
            checked = []
            response = ipv4_preferred_get('https://example.test/start', headers={}, timeout=1, url_validator=checked.append)
            try:
                self.assertEqual(response.read(), b'ok')
            finally:
                response.close()
        self.assertEqual(checked, ['https://example.test/final'])
        self.assertEqual(visited, [('IPv4PreferredHTTPSConnection', 'https://example.test/start'), ('IPv4PreferredHTTPSConnection', 'https://example.test/final')])
        def denied(url):
            raise ValueError('blocked')
        visited.clear()
        with patch('urllib.request.AbstractHTTPHandler.do_open', open_response), self.assertRaisesRegex(ValueError, 'blocked'):
            ipv4_preferred_get('https://example.test/start', headers={}, timeout=1, url_validator=denied)
        self.assertEqual(len(visited), 1)
