"""Agent 与团队保存使用真实路由回执，不把空响应或否定确认当作成功。"""
import asyncio
from dataclasses import fields
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from harness import Harness, HarnessConfig
from harness.core.language import current_language
from harness.tools.schema import validate
from harness.web.api.plugins import build_router
from harness.web.context import WebContext
from harness.web.protocol import openapi
from harness.web.response_contracts import response_contracts
from harness.web.router import Request
from tests.web_fixture import FixtureTransport


class AgentSaveProtocolTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        flags = {field.name: False for field in fields(HarnessConfig) if field.name.startswith('enable_')}
        flags['enable_agents'] = True
        self.host = Harness(HarnessConfig(api_key='fixture-only', home=root/'state', cwd=root,
            model='fixture', **flags), transport=FixtureTransport())
        self.addCleanup(lambda: asyncio.run(self.host.close()))
        self.loop = asyncio.new_event_loop()
        self.addCleanup(self.loop.close)
        self.document = openapi(WebContext(self.host, self.loop).router().templates())
        self.router = build_router(SimpleNamespace(harness=self.host, call=asyncio.run))

    def invoke(self, method, path, body=None):
        handler, params = self.router.resolve(method, path)
        request = Request(method, path, {}, {}, b'' if body is None else json.dumps(body).encode())
        return json.loads(handler(request, **params).body)

    def schema(self, name):
        def expand(value):
            if isinstance(value, dict):
                if '$ref' in value:
                    return expand(self.document['components']['schemas'][value['$ref'].rsplit('/', 1)[-1]])
                return {key: expand(item) for key, item in value.items()}
            if isinstance(value, list):
                return [expand(item) for item in value]
            return value
        return expand(self.document['components']['schemas'][name])

    def test_real_agent_and_team_create_and_update_match_documented_receipts(self):
        for kind in ('agent', 'team'):
            with self.subTest(kind=kind):
                body = {'name': 'fixture-'+kind, 'description': 'Original description 原文',
                        'system_prompt': 'Original instructions 原文', 'agent_type': kind, 'scope': 'user'}
                if kind == 'team':
                    body['members'] = [{'name': 'lead', 'role': 'lead', 'prompt': 'Coordinate 原文'},
                                       {'name': 'worker', 'role': 'member', 'prompt': 'Investigate 原文'}]
                created = self.invoke('POST', '/api/v1/plugins/agents/create', body)
                self.assertEqual(validate(created, self.schema('AgentCreatedResponse')), [])
                self.assertIs(created['created'], True)
                self.assertEqual(created['agent']['name'], body['name'])
                self.assertEqual(created['agent']['agent_type'], kind)
                snapshot = self.invoke('GET', '/api/v1/plugins/agents/'+body['name']+'/edit')
                updated = self.invoke('PATCH', '/api/v1/plugins/agents/'+body['name'],
                    {**body, 'system_prompt': 'Updated instructions 原文', 'expected_revision': snapshot['revision']})
                self.assertEqual(validate(updated, self.schema('AgentUpdatedResponse')), [])
                self.assertIs(updated['updated'], True)
                self.assertNotEqual(updated['revision'], snapshot['revision'])
                self.assertEqual(updated['agent']['system_prompt'], 'Updated instructions 原文')
                self.assertEqual(updated['refresh_errors'], [])

    def test_missing_negative_or_damaged_receipts_fail_in_both_languages(self):
        agent = {'name': 'fixture', 'agent_type': 'team'}
        created = {'created': True, 'agent': agent}
        updated = {'updated': True, 'agent': agent, 'revision': 'a'*64}
        invalid = {
            'AgentCreatedResponse': [{}, {'created': False, 'agent': agent}, {'created': 1, 'agent': agent},
                {'created': True}, {**created, 'agent': {}}, {**created, 'agent': {'name': '', 'agent_type': 'team'}},
                {**created, 'agent': {**agent, 'agent_type': 'unknown'}}, {**created, 'refresh_errors': 'failed'}],
            'AgentUpdatedResponse': [{}, {'updated': False, 'agent': agent, 'revision': 'a'*64},
                {'updated': True, 'agent': agent}, {**updated, 'revision': 'invalid'}, {**updated, 'agent': None},
                {**updated, 'refresh_errors': [{'session_id': 'owner', 'code': 'failure'}]}],
        }
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                self.assertEqual(validate(created, self.schema('AgentCreatedResponse')), [])
                self.assertEqual(validate(updated, self.schema('AgentUpdatedResponse')), [])
                for name, values in invalid.items():
                    for value in values:
                        with self.subTest(language=language, schema=name, value=value):
                            self.assertTrue(validate(value, self.schema(name)))

    def test_real_routes_and_generated_browser_contracts_share_the_same_sources(self):
        paths = self.document['paths']
        self.assertEqual(paths['/api/v1/plugins/agents/create']['post']['responses']['200']['content']['application/json']['schema'],
                         {'$ref': '#/components/schemas/AgentCreatedResponse'})
        self.assertEqual(paths['/api/v1/plugins/agents/{name}']['patch']['responses']['200']['content']['application/json']['schema'],
                         {'$ref': '#/components/schemas/AgentUpdatedResponse'})
        generated = response_contracts(self.document)
        contracts = json.loads(generated.split('export const responseContracts = ', 1)[1].splitlines()[0])
        self.assertIn(['^/api/v1/plugins/agents/create$', 'POST', 200, {'$ref': 'AgentCreatedResponse'}], contracts)
        self.assertIn(['^/api/v1/plugins/agents/[^/]+$', 'PATCH', 200, {'$ref': 'AgentUpdatedResponse'}], contracts)
