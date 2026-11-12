"""当前路由、人格代号与双语协议快照保持一致，不声明旧格式。"""
import asyncio
import ast
import gzip
import itertools
import json
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from harness import Harness, HarnessConfig
from harness.agents.personality import STYLE_AXES, STYLE_CODES
from harness.core.language import current_language
from harness.i18n import tr
from harness.tools.schema import validate
from harness.web.api.personalities import build_router
from harness.web.context import WebContext
from harness.web.errors import BadRequest
from harness.web.protocol import openapi
from harness.web.router import Request
from tests.web_fixture import FixtureTransport


class ProtocolCurrentBoundariesTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        flags = {field.name: False for field in fields(HarnessConfig) if field.name.startswith('enable_')}
        self.host = Harness(HarnessConfig(api_key='fixture-only', home=root/'state', cwd=root,
            model='fixture', **flags), transport=FixtureTransport())
        self.addCleanup(lambda: asyncio.run(self.host.close()))
        loop = asyncio.new_event_loop()
        self.addCleanup(loop.close)
        self.templates = WebContext(self.host, loop).router().templates()
        self.router = build_router(SimpleNamespace(harness=self.host, call=asyncio.run))

    def invoke(self, method, path, body=None):
        handler, params = self.router.resolve(method, path)
        request = Request(method, path, {}, {}, b'' if body is None else json.dumps(body).encode())
        return json.loads(handler(request, **params).body)

    def test_runtime_settings_reject_removed_generation_limits_without_writing(self):
        from harness.web.api.v1 import build_router as runtime_router
        router = runtime_router(SimpleNamespace(harness=self.host, call=asyncio.run))
        schema = openapi(self.templates)['components']['schemas']['SettingsPatch']
        removed = ('timeout', 'max_tokens', 'max_response_bytes')
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                handler, params = router.resolve('GET', '/api/v1/settings')
                snapshot = json.loads(handler(Request('GET', '/api/v1/settings', {}, {}, b''), **params).body)
                for name in removed:
                    self.assertNotIn(name, snapshot['config'])
                    self.assertTrue(validate({name: 1}, schema))
                    handler, params = router.resolve('PATCH', '/api/v1/settings')
                    with self.assertRaises(BadRequest) as caught:
                        handler(Request('PATCH', '/api/v1/settings', {}, {}, json.dumps({name: 1, 'max_steps': 3}).encode()), **params)
                    self.assertEqual(caught.exception.code, 'settings/invalid')
                self.assertIsNone(self.host.config.max_steps)
                self.assertFalse((self.host.config.home / 'runtime-settings.json').exists())

    def test_current_style_preferences_and_all_real_profile_codes_match_protocol(self):
        document = openapi(self.templates)
        schemas = document['components']['schemas']
        choices = schemas['RecommendPersonality']['properties']['choices']
        response = document['paths']['/api/v1/personalities/recommend']['post']['responses']['200']['content']['application/json']['schema']['properties']['code']
        self.assertEqual(choices['minItems'], 4)
        self.assertEqual(choices['maxItems'], 4)
        resolved = set()
        for combination in itertools.product(*STYLE_AXES):
            for choice, item_schema in zip(combination, choices['prefixItems']):
                self.assertEqual(validate(choice, item_schema), [])
            self.assertEqual(validate(list(combination), choices), [])
            code = self.invoke('POST', '/api/v1/personalities/recommend', {'choices': combination})['code']
            self.assertEqual(validate(code, response), [])
            resolved.add(code)
        self.assertEqual(resolved, set(STYLE_CODES))
        catalog = self.invoke('GET', '/api/v1/personalities')
        code_schema = schemas['PersonalityProfile']['properties']['code']['anyOf'][0]
        for profile in catalog['profiles']:
            self.assertEqual(validate(profile['code'], code_schema), [])
        for legacy in ('INTJ', 'ENFP'):
            self.assertTrue(validate(legacy, code_schema))
        with self.assertRaises(BadRequest):
            self.invoke('POST', '/api/v1/personalities/recommend', {'choices': ['I', 'N', 'T', 'J']})
        self.assertTrue(validate('proactive', choices['prefixItems'][1]))

    def test_session_style_codes_and_null_clear_use_current_schema(self):
        schemas = openapi(self.templates)['components']['schemas']
        session = self.host.create_session()
        path = '/api/v1/sessions/'+session.id+'/personality'
        current = self.invoke('GET', path)
        self.assertIsNone(current['code'])
        for code in ('systems-planner', 'demo-partner', None):
            current = self.invoke('PATCH', path, {'code': code, 'expected_version': current['version']})
            self.assertEqual(current['code'], code)
            self.assertEqual(current['effective'], code)
            for name in ('SessionPersonality', 'SetSessionPersonality'):
                branches = schemas[name]['properties']['code']['anyOf']
                self.assertTrue(any(not validate(code, branch) for branch in branches))

    def test_shared_decision_body_is_not_overwritten_by_local_answer_body(self):
        paths = openapi(self.templates)['paths']
        shared = paths['/api/v1/sessions/{session_id}/questions/{question_id}/answer']['post']['requestBody']['content']['application/json']['schema']
        self.assertEqual(set(shared['required']), {'answers', 'request_id', 'expected_revision'})
        self.assertFalse(shared['additionalProperties'])
        self.assertEqual(shared['properties']['expected_revision']['pattern'], '^[0-9a-f]{64}$')
        local = paths['/api/v1/questions/answer']['post']['requestBody']['content']['application/json']['schema']
        self.assertEqual(local['$ref'], '#/components/schemas/SubmitAnswers')

    def test_json_consumers_publish_request_fields_and_bodyless_actions_remain_bodyless(self):
        paths = openapi(self.templates)['paths']
        root = Path(__file__).resolve().parents[1]
        checked = []
        for source in (root / 'harness/web/api').glob('*.py'):
            module = ast.parse(source.read_text(encoding='utf-8'))
            for function in ast.walk(module):
                if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                consumes_json = any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == 'request'
                    and node.func.attr == 'json' for node in ast.walk(function))
                if not consumes_json:
                    continue
                for decorator in function.decorator_list:
                    if (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)
                            and decorator.func.attr in ('post', 'put', 'patch', 'delete')
                            and decorator.args and isinstance(decorator.args[0], ast.Constant)):
                        path, method = decorator.args[0].value, decorator.func.attr
                        if path not in paths:
                            continue
                        with self.subTest(path=path, method=method):
                            body = paths[path][method].get('requestBody')
                            self.assertIsNotNone(body, f'{source.name}:{function.lineno}')
                            self.assertIn('schema', body['content']['application/json'])
                            checked.append((method, path))
        self.assertGreater(len(checked), 50)
        for path in ('/api/v1/mcp-service/stop', '/api/v1/chatgpt/login'):
            self.assertNotIn('requestBody', paths[path]['post'])
        for path in ('/api/v1/screenshot', '/api/v1/voice/preparations', '/api/v1/sessions/{session_id}/browser'):
            self.assertFalse(paths[path]['post']['requestBody']['required'])
        self.assertTrue(paths['/api/v1/sessions/{session_id}']['patch']['requestBody']['required'])

    def test_calendar_schedule_contract_accepts_all_current_kinds(self):
        document = openapi(self.templates)
        schedules = [{'kind': 'once', 'at': 1800000000}, {'kind': 'interval', 'seconds': 60},
            {'kind': 'daily', 'time': '09:00'}, {'kind': 'weekly', 'time': '09:00', 'days': [0, 4]},
            {'kind': 'monthly', 'time': '09:00', 'monthdays': [31]},
            {'kind': 'yearly', 'time': '09:00', 'months': [2], 'monthdays': [29]}]
        from harness.app.schedules import validate_schedule
        for path in ('/api/v1/automations', '/api/v1/automations/preview'):
            schema = document['paths'][path]['post']['requestBody']['content']['application/json']['schema']['properties']['schedule']
            for schedule in schedules:
                self.assertEqual(sum(not validate(schedule, branch) for branch in schema['oneOf']), 1, schedule)
                self.assertEqual(validate_schedule({'schedule': schedule})[0], schedule)
            for schedule in ({'kind': 'monthly', 'time': '09:00'},
                    {'kind': 'yearly', 'time': '09:00', 'months': [13], 'monthdays': [1]}):
                self.assertFalse(any(not validate(schedule, branch) for branch in schema['oneOf']))

    def test_new_request_schema_refs_resolve_and_snapshots_are_independent(self):
        document = openapi(self.templates)
        def check(value):
            if isinstance(value, dict):
                if '$ref' in value:
                    self.assertIn(value['$ref'].rsplit('/', 1)[-1], document['components']['schemas'])
                for child in value.values():
                    check(child)
            elif isinstance(value, list):
                for child in value:
                    check(child)
        check(document)
        body = document['paths']['/api/v1/attachments']['post']['requestBody']['content']['application/json']['schema']
        body['properties'].clear()
        self.assertIn('files', openapi(self.templates)['paths']['/api/v1/attachments']['post']['requestBody']['content']['application/json']['schema']['properties'])

    def test_large_catalogs_negotiate_gzip_without_changing_payload_or_cache_policy(self):
        loop = asyncio.new_event_loop()
        self.addCleanup(loop.close)
        router = WebContext(self.host, loop).router()
        fixture = [{'id': 'fixture', 'models': [{'id': 'Original 原文'}] * 300}]
        with patch('harness.web.api.provider_connections.directory', return_value=fixture):
            for path in ('/api/v1/connection/providers', '/api/v1/openapi.json'):
                handler, params = router.resolve('GET', path)
                plain = handler(Request('GET', path, {}, {}, b''), **params)
                compressed = handler(Request('GET', path, {}, {'Accept-Encoding': 'gzip;q=0.5'}, b''), **params)
                refused = handler(Request('GET', path, {}, {'Accept-Encoding': 'gzip;q=0,*;q=1'}, b''), **params)
                self.assertEqual(compressed.headers['Content-Encoding'], 'gzip')
                self.assertEqual(compressed.headers['Vary'], 'Accept-Encoding')
                self.assertEqual(gzip.decompress(compressed.body), plain.body)
                self.assertEqual(refused.body, plain.body)
                self.assertNotIn('Content-Encoding', refused.headers)
                self.assertEqual(compressed.headers['Cache-Control'], 'no-store')
                self.assertLess(len(compressed.body), len(plain.body))

    def test_queue_edit_declares_both_text_and_expected_version(self):
        document = openapi(self.templates)
        operation = document['paths']['/api/v1/sessions/{session_id}/runs/{run_id}']['patch']
        body = operation['requestBody']
        self.assertTrue(body['required'])
        name = body['content']['application/json']['schema']['$ref'].rsplit('/', 1)[-1]
        schema = document['components']['schemas'][name]
        self.assertEqual(validate({'text': 'original 中文', 'expected_version': 'a'*64}, schema), [])
        # 空正文是否可保存由已有附件引用决定，不在无状态schema中拒绝。
        self.assertEqual(validate({'text': '', 'expected_version': 'a'*64}, schema), [])
        for invalid in ({'text': 'original'}, {'expected_version': 'a'*64}, {'text': None, 'expected_version': 'a'*64}):
            self.assertTrue(validate(invalid, schema))

    def test_protocol_descriptions_follow_language_and_snapshots_are_independent(self):
        snapshots = []
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                document = openapi(self.templates)
                snapshots.append(document)
                self.assertEqual(document['info']['title'], tr('protocol.title'))
                schemas = document['components']['schemas']
                self.assertEqual(schemas['ConversationHistory']['properties']['through']['description'], tr('python.protocol.f46f03a1ca79'))
                self.assertEqual(schemas['SetSessionPermission']['properties']['permission_policy']['description'], tr('python.protocol.7fda4c42e659'))
                self.assertEqual(schemas['CommandOutput']['description'], tr('python.protocol.7be989ba5663'))
                from harness.web.protocol import _SCHEMA_DESCRIPTION_KEYS
                for path, key in _SCHEMA_DESCRIPTION_KEYS.items():
                    target = schemas[path[0]]
                    for property_name in path[1:]:
                        target = target['properties'][property_name]
                    self.assertEqual(target['description'], tr(key))
                self.assertEqual(document['paths']['/api/v1/access/login']['post']['responses']['403']['description'], tr('protocol.access_rejected'))
        self.assertEqual(snapshots[0], snapshots[2])
        self.assertNotEqual(snapshots[0]['components']['schemas']['CommandOutput']['description'], snapshots[1]['components']['schemas']['CommandOutput']['description'])
        snapshots[0]['components']['schemas']['PersonalityProfile']['properties']['code']['anyOf'][0]['enum'].clear()
        self.assertEqual(len(snapshots[2]['components']['schemas']['PersonalityProfile']['properties']['code']['anyOf'][0]['enum']), 16)
        self.assertEqual(len(openapi(self.templates)['components']['schemas']['PersonalityProfile']['properties']['code']['anyOf'][0]['enum']), 16)
