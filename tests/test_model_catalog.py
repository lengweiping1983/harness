"""模型目录只投影已配置来源，不把环境配置合成为可选模型。"""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from harness.app.config import HarnessConfig
from harness.core.language import current_language
from harness.llm.providers import ProviderConnections
from harness.web.api.catalog import build_router
from harness.web.api.settings import SettingsStore
from harness.web.models import model_catalog
from harness.web.router import Request


class ModelCatalogTests(unittest.TestCase):
    def catalog_response(self, config, settings=None):
        """通过设置和模型菜单共用的真实目录路由读取。"""
        ctx = SimpleNamespace(harness=SimpleNamespace(config=config), _ui_settings=settings)
        handler, params = build_router(ctx).resolve('GET', '/api/v1/models')
        response = handler(Request('GET', '/api/v1/models', {}, {}, b''), **params)
        self.assertEqual(response.status, 200)
        return json.loads(response.body)

    def assert_empty(self, payload):
        """空目录不能包含默认模型或虚构的提供商。"""
        self.assertEqual(payload['models'], [])
        self.assertEqual(payload['active'], '')
        self.assertEqual(payload['providers'], [])

    def test_environment_credentials_and_model_do_not_populate_empty_directory(self):
        for key_variable in ('OPENROUTER_API_KEY', 'HARNESS_API_KEY', 'HARNESS_API_KEYS'):
            with self.subTest(key_variable=key_variable), tempfile.TemporaryDirectory() as folder:
                home = Path(folder)
                environment = {'HARNESS_HOME': folder, 'HARNESS_MODEL': 'private/Original-model',
                               'HARNESS_BASE_URL': 'https://example.invalid/v1', key_variable: 'fixture-key'}
                with patch.dict('os.environ', environment, clear=True):
                    config = HarnessConfig.from_env()
                config.provider_connections = ProviderConnections(home)
                settings = SettingsStore(home)
                settings.update({'default_model': config.model, 'model_visibility': {config.model: True}})
                original = {path.name: path.read_bytes() for path in home.iterdir()}
                for language in ('en', 'zh-CN', 'en'):
                    with current_language.set(language):
                        self.assert_empty(self.catalog_response(config, settings))
                self.assertEqual(config.model, 'private/Original-model')
                self.assertEqual(config.api_key, 'fixture-key')
                self.assertEqual({path.name: path.read_bytes() for path in home.iterdir()}, original)

    def test_single_model_and_routes_without_provider_directory_stay_out_of_catalog(self):
        for api_key in ('', 'fixture-key'):
            config = SimpleNamespace(model='private/Original-model', api_key=api_key, reasoning_effort='high',
                                     model_routes=[{'model': 'private/Other-model'}, {'model': 'private/Original-model'}])
            self.assert_empty(self.catalog_response(config))
            self.assertEqual(model_catalog(config)['reasoning_effort'], 'high')
            config.chatgpt_account = SimpleNamespace(logged_in=False)
            self.assert_empty(self.catalog_response(config))

    def test_configured_model_keeps_identity_capabilities_and_visibility_then_removes_cleanly(self):
        with tempfile.TemporaryDirectory() as folder:
            store = ProviderConnections(folder)
            provider = {'id': 'supplier', 'type': 'custom', 'name': '提供商 Original', 'connections': [
                {'id': 'endpoint', 'name': '接口 Original', 'protocol': 'openai-completions',
                 'base_url': 'https://example.invalid/v1', 'enabled': True,
                 'auth': {'type': 'api_key', 'env': {}},
                 'keys': [{'id': 'key', 'value': 'fixture-key', 'enabled': True}],
                 'models': [{'id': 'chosen', 'model': 'remote-original', 'name': '模型 Original', 'enabled': True,
                             'context_window': 12000, 'max_output_tokens': 4000, 'input': ['text'],
                             'reasoning_efforts': {'high': 'high'}},
                            {'id': 'disabled', 'model': 'remote-disabled', 'name': 'Disabled', 'enabled': False}]}]}
            store.save([provider], store.revision)
            identity = 'harness/supplier/endpoint/chosen'
            config = SimpleNamespace(provider_connections=store, model='private/env-model', api_key='fixture-key')
            payload = self.catalog_response(config)
            self.assertEqual(payload['active'], '')
            self.assertEqual([row['id'] for row in payload['models']], [identity])
            settings = SettingsStore()
            settings.update({'model_visibility': {identity: False}})
            self.assertFalse(self.catalog_response(config, settings)['models'][0]['visible'])
            config.model = identity
            payload = self.catalog_response(config, settings)
            self.assertEqual(payload['active'], identity)
            row = payload['models'][0]
            self.assertTrue(row['active'])
            self.assertTrue(row['visible'])
            self.assertEqual(row['label'], '模型 Original')
            self.assertEqual(row['description'], 'remote-original')
            self.assertEqual((row['context_window'], row['max_output']), (12000, 4000))
            self.assertEqual(row['reasoning_efforts'], ['high'])
            store.save([], store.revision)
            self.assert_empty(self.catalog_response(config, settings))
            config.provider_connections = ProviderConnections(folder)
            self.assert_empty(self.catalog_response(config, settings))

    def test_subscription_pool_retains_only_eligible_models_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as folder:
            row = {'id': 'chatgpt/original', 'label': 'Account 原文', 'provider': 'chatgpt'}
            eligible = SimpleNamespace(logged_in=True, status=Mock(return_value={'models': [row]}))
            disabled = SimpleNamespace(logged_in=True, status=Mock())
            logged_out = SimpleNamespace(logged_in=False, status=Mock())
            duplicate = SimpleNamespace(logged_in=True, status=Mock())
            slots = [(SimpleNamespace(enabled=enabled, account=account), repeated) for account, enabled, repeated in (
                (eligible, True, False), (eligible, True, False), (disabled, False, False),
                (logged_out, True, False), (duplicate, True, True))]
            config = SimpleNamespace(provider_connections=ProviderConnections(folder), model=row['id'],
                                     api_key='fixture-key', subscription_pool=SimpleNamespace(_unique=lambda: slots))
            payload = self.catalog_response(config)
            self.assertEqual(payload['models'], [{**row, 'active': True, 'visible': True}])
            self.assertEqual(payload['active'], row['id'])
            self.assertEqual(payload['providers'], ['chatgpt'])
            for account in (disabled, logged_out, duplicate):
                account.status.assert_not_called()
            config.subscription_pool = SimpleNamespace(_unique=lambda: [])
            self.assert_empty(self.catalog_response(config))

    def test_explicit_account_without_provider_directory_remains_available(self):
        row = {'id': 'chatgpt/original', 'label': 'Account 原文', 'provider': 'chatgpt'}
        config = SimpleNamespace(model='private/env-model', api_key='fixture-key', chatgpt_account=SimpleNamespace(
            logged_in=True, status=lambda: {'models': [row], 'selected': row['id']}))
        payload = self.catalog_response(config)
        self.assertEqual(payload['models'], [{**row, 'active': True, 'visible': True}])
        self.assertEqual(payload['active'], row['id'])
        config.chatgpt_account.logged_in = False
        self.assert_empty(self.catalog_response(config))
