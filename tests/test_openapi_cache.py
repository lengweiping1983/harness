"""验证 OpenAPI 请求缓存的语言、路由和响应隔离边界。"""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from harness.core.language import current_language
from harness.web.api.v1 import build_router
from harness.web.protocol import SCHEMAS, openapi
from harness.web.router import Request


class OpenApiCacheTests(unittest.TestCase):
    def setUp(self):
        self.templates = ['GET /api/v1/openapi.json']
        self.ctx = SimpleNamespace(router=lambda: SimpleNamespace(templates=lambda: self.templates))
        self.router = build_router(self.ctx)
        self.request = Request('GET', '/api/v1/openapi.json', {}, {}, b'')

    def read(self, language='en', router=None):
        token = current_language.set(language)
        try:
            handler, params = (router or self.router).resolve(self.request.method, self.request.path)
            return handler(self.request, **params)
        finally:
            current_language.reset(token)

    def test_repeated_requests_skip_generation_and_serialization(self):
        with patch('harness.web.protocol.openapi', wraps=openapi) as generate:
            first = self.read()
            first.headers['Content-Type'] = 'changed'
            first.body = b'changed'
            with patch('harness.web.api.v1.json_response', side_effect=AssertionError('重复序列化')):
                second = self.read()
            self.assertEqual(generate.call_count, 1)
        self.assertEqual(second.status, 200)
        self.assertEqual(second.headers, {'Content-Type': 'application/json; charset=utf-8',
                                          'Cache-Control': 'no-store'})
        token = current_language.set('en')
        try:
            self.assertEqual(json.loads(second.body), openapi(self.templates))
        finally:
            current_language.reset(token)

    def test_language_roundtrip_and_route_changes_use_separate_entries(self):
        with patch('harness.web.protocol.openapi', wraps=openapi) as generate:
            english = self.read()
            chinese = self.read('zh-CN')
            self.assertNotEqual(english.body, chinese.body)
            self.assertIs(self.read().body, english.body)
            self.assertEqual(generate.call_count, 2)
            self.templates.append('GET /api/v1/cache-test')
            changed = self.read()
            self.assertIn('/api/v1/cache-test', json.loads(changed.body)['paths'])
            self.assertEqual(generate.call_count, 3)
            other = build_router(self.ctx)
            self.assertEqual(self.read(router=other).body, changed.body)
            self.assertEqual(generate.call_count, 4)

    def test_public_document_remains_an_independent_mutable_snapshot(self):
        document = openapi(self.templates)
        document['components']['schemas'].clear()
        self.assertTrue(SCHEMAS)
        self.assertEqual(len(openapi(self.templates)['components']['schemas']), len(SCHEMAS))


if __name__ == '__main__':
    unittest.main()
