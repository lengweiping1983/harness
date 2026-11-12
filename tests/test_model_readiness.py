"""模型就绪状态沿实际凭据判定、状态路由及标题消费者验证。"""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from harness.app.config import HarnessConfig
from harness.app.harness import Harness
from harness.app.session_titles import SessionTitles
from harness.core.events import EventBus
from harness.llm.providers import ProviderConnections
from harness.llm.types import LlmCallConfig
from harness.session.events import AppendOp
from harness.session.store import SessionStore
from harness.web.api.catalog import build_router
from harness.web.router import Request


class ModelReadinessTests(unittest.IsolatedAsyncioTestCase):
    async def test_status_and_titles_share_credentials_without_a_primary_api_key(self):
        with tempfile.TemporaryDirectory() as folder:
            app = Harness.__new__(Harness)
            app.config = HarnessConfig(home=folder, cwd=folder, api_key='', language='en')
            app.provider_connections = ProviderConnections(folder)
            app.chatgpt = SimpleNamespace(logged_in=False)
            secondary = SimpleNamespace(logged_in=False)
            app.codex_gateway = SimpleNamespace(slots={'secondary': SimpleNamespace(account=secondary)})
            app.agent = None
            app._agents = {}
            app.tools = SimpleNamespace(names=lambda: [])
            app.skills = None
            app.bus = EventBus()
            app.sessions = SessionStore(Path(folder) / 'sessions', bus=app.bus)
            app.list_sessions = lambda: [header.to_dict() for header in app.sessions.list_sessions()]
            app.adapter = SimpleNamespace(complete=AsyncMock(return_value=SimpleNamespace(text='Generated', has_tool_use=False)))
            app._session_llm_config = lambda session: LlmCallConfig(model='harness/supplier/endpoint/chosen')
            ctx = SimpleNamespace(harness=app, hub=SimpleNamespace(observer_count=lambda: 0), loop=asyncio.get_running_loop())
            handler, parameters = build_router(ctx).resolve('GET', '/api/v1/status')
            titles = SessionTitles(app)
            try:
                for source in ('none', 'connection', 'subscription', 'secondary', 'key'):
                    with self.subTest(source=source):
                        app.config.api_key = 'fixture-key' if source == 'key' else ''
                        app.chatgpt.logged_in = source == 'subscription'
                        secondary.logged_in = source == 'secondary'
                        providers = [{'id': 'supplier', 'type': 'custom', 'name': 'Fixture', 'connections': [{
                            'id': 'endpoint', 'name': 'Fixture', 'protocol': 'openai-completions',
                            'base_url': 'https://example.invalid/v1', 'enabled': True,
                            'auth': {'type': 'api_key', 'env': {}},
                            'keys': [{'id': 'key', 'value': 'fixture-key', 'enabled': True}],
                            'models': [{'id': 'chosen', 'model': 'remote', 'name': 'Remote', 'enabled': True}],
                        }]}] if source == 'connection' else []
                        app.provider_connections.save(providers, app.provider_connections.revision)
                        before = {path: path.read_bytes() for path in Path(folder).rglob('*') if path.is_file()}
                        response = handler(Request('GET', '/api/v1/status', {}, {}, b''), **parameters)
                        payload = json.loads(response.body)
                        self.assertEqual(response.status, 200)
                        self.assertEqual(payload['has_model_credentials'], source != 'none')
                        self.assertEqual(payload['config']['has_api_key'], source == 'key')
                        self.assertEqual({path: path.read_bytes() for path in before}, before)
                        app.adapter.complete.reset_mock()
                        session = app.sessions.create(metadata={'auto_title': 'awaiting'})
                        await session.append('user/message', {'text': 'Original task'}, surface_op=AppendOp())
                        await asyncio.gather(*tuple(titles.tasks.values()))
                        self.assertEqual(session.header.metadata['auto_title'], 'fallback' if source == 'none' else 'generated')
                        self.assertEqual(app.adapter.complete.await_count, 0 if source == 'none' else 1)
            finally:
                await titles.close()
