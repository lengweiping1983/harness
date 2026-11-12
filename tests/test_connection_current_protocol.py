"""连接设置仅接受当前模型列表，容量字段与实际回执同步。"""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from harness import Harness, HarnessConfig
from harness.core.language import current_language
from harness.i18n import tr
from harness.web.api.connection import build_router
from harness.web.errors import BadRequest
from harness.web.protocol import SCHEMAS
from harness.web.router import Request
from harness.tools.schema import validate
from tests.web_fixture import FixtureTransport


class ConnectionCurrentProtocolTests(unittest.TestCase):
    def test_current_models_capacity_roundtrip_and_old_fields_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            host = Harness(HarnessConfig(api_key='fixture', model='fixture', home=root/'state', cwd=root), transport=FixtureTransport())
            try:
                router = build_router(SimpleNamespace(harness=host, call=asyncio.run))
                def invoke(body):
                    handler, params = router.resolve('PATCH', '/api/v1/connection')
                    request = Request('PATCH', '/api/v1/connection', {}, {}, json.dumps(body).encode())
                    return json.loads(handler(request, **params).body)
                for body in ({}, {'model':'old'}, {'base_url':'https://example.test'}, {'api_key':'secret'}, {'models':[]}, {'models':[], 'model':'mixed'}):
                    for language in ('en', 'zh-CN'):
                        with current_language.set(language), self.subTest(body=body, language=language):
                            with self.assertRaises(BadRequest) as error:
                                invoke(body)
                            self.assertIn(tr('models.config.invalid'), str(error.exception))
                    self.assertTrue(validate(body, SCHEMAS['SetConnection']))
                from tests.test_provider_connections import provider_fixture
                revision = host.provider_connections.revision
                result = invoke({'providers': [provider_fixture()], 'revision': revision})
                saved = result['routing']['providers'][0]['connections'][0]
                self.assertEqual(len(saved['models']), 2)
                self.assertEqual(len(saved['keys']), 2)
                self.assertNotIn('value', saved['keys'][0])
                self.assertEqual(validate(result['routing'], SCHEMAS['ProviderRouting']), [])
                self.assertEqual(host.config.model, 'harness/supplier/endpoint/model-a')
                empty = invoke({'providers': [], 'revision': result['routing']['revision']})
                self.assertEqual(empty['routing']['providers'], [])
            finally:
                asyncio.run(host.close())
