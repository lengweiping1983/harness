"""接续桥随托管 MCP 配置启停，凭据仅按需揭示，外部监听不被接管。"""
import json
from pathlib import Path
import socket
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from harness.app.mcp_service import McpService
from harness.app.chat_companion import ChatCompanionService
from harness.core.language import current_language
from harness.execution.config import ExecutionConfig
from harness.execution.runtime import ExecutionRuntime
from harness.web.api.mcp_service import build_router
from harness.web.router import Request


class ManagedCompanionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        project = self.root / 'project'; project.mkdir()
        self.manager = McpService(self.root / 'private')
        self.manager.home.mkdir(parents=True)
        config = {'home': str(self.manager.home / 'state'), 'language': 'en', 'projects': {'fixture': {'root': str(project)}}}
        (self.manager.home / 'config.json').write_text(json.dumps(config), encoding='utf-8')
        self.runtime = ExecutionRuntime(ExecutionConfig.load(self.manager.home / 'config.json'))
        self.addAsyncCleanup(self.runtime.close)
        self.addCleanup(self.manager.stop)
        with socket.socket() as available:
            available.bind(('127.0.0.1', 0)); self.port = available.getsockname()[1]

    def running_manager(self):
        self.manager._state['status'] = 'running'
        self.manager._process = Mock(poll=Mock(return_value=None), stdout=None)
        self.manager._tunnel = Mock(poll=Mock(return_value=None), stdout=None)

    async def test_probe_reuses_only_the_current_companion_protocol(self):
        with patch('harness.app.chat_companion.build_opener') as opener:
            response = opener.return_value.open.return_value.__enter__.return_value
            for version in (1, 2):
                value = {'protocol': 'harness-chat-companion', 'protocol_version': version, 'executor': 'ready'}
                response.read.return_value = json.dumps(value).encode('utf-8')
                result = self.manager.companion._probe(self.port, 'fixture-token')
                self.assertEqual(result, value if version == 2 else None)

    async def test_apply_starts_owned_bridge_and_stop_releases_listener_without_exposing_secret(self):
        self.running_manager()
        state = self.manager.configure_companion({'enabled': True, 'port': self.port})
        self.assertEqual(state['companion']['status'], 'running')
        self.assertEqual(state['companion']['executor'], 'ready')
        token = self.manager.companion_token()
        self.assertNotIn(token, json.dumps(state))
        self.assertTrue(self.manager.companion.thread.is_alive())
        self.manager.configure_companion({'enabled': False, 'port': self.port})
        self.assertIsNone(self.manager.companion.server)
        self.assertEqual(self.manager.snapshot()['companion']['status'], 'disabled')
        with socket.socket() as released:
            self.assertNotEqual(released.connect_ex(('127.0.0.1', self.port)), 0)

    async def test_same_configuration_external_bridge_is_reused_and_never_stopped(self):
        self.running_manager()
        self.manager.configure_companion({'enabled': True, 'port': self.port})
        other = ChatCompanionService(self.manager.home, self.manager.source)
        try:
            other.start()
            self.assertIsNone(other.server)
            self.assertTrue(other.snapshot(True)['external'])
            other.stop()
            self.assertEqual(self.manager.companion.snapshot(True)['status'], 'running')
        finally:
            other.stop()

    async def test_corrupt_settings_are_preserved_and_errors_follow_reading_language(self):
        path = self.manager.home / 'companion.json'; path.write_bytes(b'{broken')
        self.manager.companion.start()
        seen = []
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                state = self.manager.companion.snapshot()
                self.assertEqual(state['status'], 'failed')
                self.assertEqual(state['error_copy'], {'key': 'ui.companion.invalidConfig', 'params': {}})
                seen.append(state['error'])
        self.assertEqual(seen[0], seen[2]); self.assertNotEqual(seen[0], seen[1])
        self.assertEqual(path.read_bytes(), b'{broken')
        self.assertIsNone(self.manager.companion.server)

    async def test_token_requires_explicit_local_post_and_status_never_contains_it(self):
        self.running_manager()
        self.manager.configure_companion({'enabled': True, 'port': self.port})
        ctx = SimpleNamespace(mcp_service=self.manager, harness=SimpleNamespace(config=SimpleNamespace(home=self.root / 'private')))
        router = build_router(ctx)
        handler, params = router.resolve('POST', '/api/v1/mcp-service/companion/token')
        allowed = Request('POST', '/api/v1/mcp-service/companion/token', {}, {'Host':'localhost:8765', 'Origin':'http://localhost:8765'}, b'', client_ip='127.0.0.1')
        token = json.loads(handler(allowed, **params).body)['token']
        self.assertNotIn(token, json.dumps(self.manager.snapshot()))
        from harness.web.errors import ApiError
        for address, origin in [('192.0.2.1','http://localhost:8765'), ('127.0.0.1','https://evil.example')]:
            blocked = Request('POST', allowed.path, {}, {'Host':'localhost:8765','Origin':origin}, b'', client_ip=address)
            with self.assertRaises(ApiError):
                handler(blocked, **params)

    async def test_generated_pairing_token_and_port_are_reused_after_reconfiguration(self):
        key = self.manager.home / 'state' / 'companion-key.json'
        self.assertFalse(key.exists())
        self.assertEqual(self.manager.companion.snapshot()['token_mask'], '')
        self.running_manager()
        self.manager.configure_companion({'enabled': True, 'port': self.port})
        token = self.manager.companion_token()
        self.assertEqual(len(token), 64)
        original = key.read_bytes()
        with socket.socket() as available:
            available.bind(('127.0.0.1', 0))
            next_port = available.getsockname()[1]
        changed = self.manager.configure_companion({'enabled': True, 'port': next_port})
        self.assertEqual(changed['companion']['endpoint'], f'http://127.0.0.1:{next_port}/')
        self.assertEqual(changed['companion']['status'], 'running')
        self.assertEqual(self.manager.companion_token(), token)
        self.assertEqual(key.read_bytes(), original)
        self.manager.stop()
        recreated = ChatCompanionService(self.manager.home, self.manager.source)
        self.assertEqual(recreated.settings()[0], {'enabled': True, 'port': next_port})
        self.assertEqual(recreated.token(), token)

    async def test_foreign_port_collision_fails_only_companion_without_stopping_mcp(self):
        self.running_manager()
        with socket.socket() as foreign:
            foreign.bind(('127.0.0.1', 0)); foreign.listen()
            state = self.manager.configure_companion({'enabled': True, 'port': foreign.getsockname()[1]})
            self.assertEqual(state['status'], 'running')
            self.assertEqual(state['companion']['status'], 'failed')
            self.assertIsNone(self.manager.companion.server)
            self.assertGreater(foreign.fileno(), -1)

    async def test_corrupt_dispatch_database_is_preserved_without_stopping_mcp(self):
        self.running_manager()
        path = self.manager.home / 'state' / 'companion.sqlite3'
        path.write_bytes(b'original corrupt companion data')
        state = self.manager.configure_companion({'enabled': True, 'port': self.port})
        self.assertEqual(state['status'], 'running')
        self.assertEqual(state['companion']['status'], 'failed')
        self.assertEqual(path.read_bytes(), b'original corrupt companion data')
