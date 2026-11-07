"""固定隧道、限流保护与双平台启动的隔离回归。"""
import io
import errno
import json
from pathlib import Path
import socket
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, Mock, patch

from harness.app.mcp_errors import McpServiceError
from harness.app.mcp_service import McpService, _TunnelOutput, _tunnel_environment
from harness.app.mcp_tunnel import DEFAULT_TUNNEL, public_tunnel, read_tunnel, validate_tunnel
from harness.core.language import current_language


class NamedTunnelTests(unittest.TestCase):
    def settings(self, port=18765):
        return {'mode': 'named', 'public_url': 'https://mcp.example.test', 'origin_port': port, 'token': 'private-fixture-token'}

    def test_new_configuration_defaults_to_12345_and_saved_ports_are_preserved(self):
        self.assertEqual(DEFAULT_TUNNEL['origin_port'], 12345)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'tunnel.json'
            settings, raw = read_tunnel(path)
            self.assertEqual(settings['origin_port'], 12345)
            self.assertIsNone(raw)
            self.assertFalse(path.exists())
            original = json.dumps(self.settings(18765)).encode('utf-8')
            path.write_bytes(original)
            settings, raw = read_tunnel(path)
            self.assertEqual(settings['origin_port'], 18765)
            self.assertEqual(raw, original)
            self.assertEqual(path.read_bytes(), original)

    @unittest.skipIf(sys.platform == 'win32', 'POSIX 地址复用语义')
    def test_closed_origin_connection_does_not_block_named_restart(self):
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
            listener.listen()
            listener.settimeout(2)
            with socket.create_connection(('127.0.0.1', port), timeout=2) as client:
                connection, _ = listener.accept()
                connection.close()
                self.assertEqual(client.recv(1), b'')
        # 服务端主动关闭留下 TIME_WAIT，原探测确实会拒绝这个可复用端口。
        with socket.socket() as probe, self.assertRaises(OSError) as caught:
            probe.bind(('127.0.0.1', port))
        self.assertEqual(caught.exception.errno, errno.EADDRINUSE)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / 'project'
            project.mkdir()
            service = McpService(root / 'private')
            service._launch_tunnel = self.settings(port)
            try:
                with patch.object(service, '_python', return_value='fixture-python'), \
                     patch.object(service, '_cloudflared', side_effect=RuntimeError('fixture after port check')) as prepare, \
                     patch.object(service, '_spawn') as spawn:
                    service._launch(project, 'project', None)
                    prepare.assert_called_once()
                    spawn.assert_not_called()
                self.assertEqual(service.snapshot()['error'], 'fixture after port check')
            finally:
                service.stop()

    def test_origin_probe_reuses_addresses_only_on_posix_and_preserves_other_errors(self):
        for platform in ('darwin', 'win32'):
            for failure in (None, PermissionError(errno.EACCES, 'fixture denied')):
                with self.subTest(platform=platform, failure=failure), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    project = root / 'project'
                    project.mkdir()
                    service = McpService(root / 'private')
                    service._launch_tunnel = self.settings(12345)
                    sockets = [MagicMock() for _ in range(3)]
                    for sock in sockets:
                        sock.__enter__.return_value = sock
                        sock.getsockname.return_value = ('127.0.0.1', 12345)
                    origin = sockets[0]
                    origin.bind.side_effect = failure
                    try:
                        with patch('harness.app.mcp_service.sys.platform', platform), \
                             patch('harness.app.mcp_service.socket.socket', side_effect=sockets), \
                             patch.object(service, '_private_home'), \
                             patch.object(service, '_python', return_value='fixture-python'), \
                             patch.object(service, '_cloudflared', side_effect=RuntimeError('fixture after port check')) as prepare:
                            service._launch(project, 'project', None)
                        if platform == 'darwin':
                            origin.setsockopt.assert_called_once_with(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                        else:
                            origin.setsockopt.assert_not_called()
                        origin.bind.assert_called_once_with(('127.0.0.1', 12345))
                        if failure is None:
                            origin.listen.assert_called_once()
                            prepare.assert_called_once()
                        else:
                            prepare.assert_not_called()
                            origin.listen.assert_not_called()
                            snapshot = service.snapshot()
                            self.assertIsNone(snapshot['error_copy'])
                            self.assertEqual(snapshot['error'], str(failure))
                    finally:
                        service.stop()

    def test_saved_settings_survive_recreation_and_start_without_reentering_secrets(self):
        for mode in ('quick', 'named'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                project = root / 'project'
                project.mkdir()
                service = McpService(root / 'private')
                settings = {**self.settings(), 'mode': mode}
                try:
                    with patch.object(service, '_launch') as launch, patch.object(service, '_spawn') as spawn:
                        service.start(project, tunnel=settings)
                        service._thread.join(timeout=3)
                        self.assertFalse(service._thread.is_alive())
                        launch.assert_called_once()
                        spawn.assert_not_called()
                        saved = service.snapshot()
                    self.assertEqual(saved['status'], 'starting')
                finally:
                    service.stop()
                self.assertEqual(saved['tunnel']['mode'], mode)
                self.assertEqual(saved['tunnel']['public_url'], settings['public_url'])
                self.assertTrue(saved['tunnel']['token_mask'])
                self.assertNotIn(settings['token'], json.dumps(saved))
                recreated = McpService(root / 'private')
                self.assertEqual(recreated.snapshot()['tunnel'], saved['tunnel'])
                launched = []
                try:
                    with patch.object(recreated, '_launch', side_effect=lambda *args: launched.append(recreated._launch_tunnel)):
                        recreated.start(project)
                        recreated._thread.join(timeout=3)
                        self.assertFalse(recreated._thread.is_alive())
                    self.assertEqual(launched, [settings])
                    recreated.stop()
                    self.assertEqual(read_tunnel(service.home / 'tunnel.json')[0], settings)
                finally:
                    recreated.stop()

    def test_start_is_local_only_and_preserves_corrupt_configuration(self):
        from types import SimpleNamespace
        from harness.web.api.mcp_service import build_router
        from harness.web.errors import ApiError, BadRequest
        from harness.web.router import Request
        with tempfile.TemporaryDirectory() as directory:
            service = McpService(directory)
            ctx = SimpleNamespace(mcp_service=service, harness=SimpleNamespace(config=SimpleNamespace(home=directory)),
                                  workspaces=Mock(return_value=Mock()))
            handler, params = build_router(ctx).resolve('POST', '/api/v1/mcp-service/start')
            def request(host='localhost:8765'):
                return Request('POST', '/api/v1/mcp-service/start', {}, {'Host': host},
                               json.dumps({'scope': 'all', 'tunnel': self.settings()}).encode(), client_ip='127.0.0.1')
            with self.assertRaises(ApiError):
                handler(request('foreign.example'), **params)
            self.assertFalse(service.home.exists())
            try:
                with patch.object(service, '_launch'):
                    response = handler(request(), **params)
                    service._thread.join(timeout=3)
                self.assertEqual(response.headers['Cache-Control'], 'no-store')
                self.assertNotIn(self.settings()['token'], response.body.decode())
            finally:
                service.stop()
            path = service.home / 'tunnel.json'
            path.write_bytes(b'corrupt original')
            with self.assertRaises(BadRequest):
                handler(request(), **params)
            self.assertEqual(path.read_bytes(), b'corrupt original')

    def test_managed_restart_reuses_the_original_login_password(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        from harness.execution.config import ExecutionConfig, ProjectConfig
        from harness.mcp_server.auth import LocalOAuthProvider
        from harness.mcp_server.managed import _run
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / 'project'
            project.mkdir()
            config = ExecutionConfig(Path(directory) / 'state', {'project': ProjectConfig(project)})
            args = SimpleNamespace(public_url='https://mcp.example.test', port=18765, local_port=18766)
            server = SimpleNamespace(started=True, serve=AsyncMock())
            with patch('harness.mcp_server.server.create_http_app'), patch('uvicorn.Server', return_value=server), patch('threading.Thread'):
                _run(config, args)
                secret = config.home.parent / 'password.json'
                original = secret.read_bytes()
                password = json.loads(original)['password']
                with patch.object(LocalOAuthProvider, 'initialize_login', side_effect=AssertionError('must reuse saved password')):
                    _run(config, args)
                self.assertEqual(secret.read_bytes(), original)
                service = McpService(Path(directory).parent)
                service.home = config.home.parent
                with patch.object(service, 'snapshot', return_value={'status': 'running'}):
                    self.assertEqual(service.password(), password)

    def test_current_configuration_is_strict_and_secrets_are_not_public(self):
        valid = self.settings()
        for key, values in {'mode': ['invalid', None], 'public_url': ['http://mcp.example.test', 'https://user:password@mcp.example.test', 'https://mcp.example.test/mcp', 'https://mcp.example.test?x=1'], 'origin_port': [True, '18765', 0, 65536], 'token': ['a b', None]}.items():
            for value in values:
                with self.subTest(key=key, value=value), self.assertRaises(McpServiceError):
                    validate_tunnel({**valid, key: value})
        self.assertNotIn('token', public_tunnel(valid))
        self.assertEqual(validate_tunnel({**valid, 'token': ''}, valid), valid)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'tunnel.json'
            for raw in (b'{', b'{"mode":"quick","mode":"named"}', b'[]', b'\xff'):
                path.write_bytes(raw)
                with self.assertRaises(McpServiceError):
                    read_tunnel(path)
                self.assertEqual(path.read_bytes(), raw)

    def test_named_start_uses_fixed_port_issuer_and_private_environment_on_both_platforms(self):
        for platform in ('win32', 'darwin'):
            with self.subTest(platform=platform), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                project = root / 'project'
                project.mkdir()
                # 验证显式固定端口的传播，不占用用户常驻网关的默认端口。
                with socket.socket() as available:
                    available.bind(('127.0.0.1', 0))
                    origin_port = available.getsockname()[1]
                service = McpService(root / 'private')
                tunnel, main = Mock(), Mock()
                tunnel.poll.return_value = main.poll.return_value = None
                tunnel.stdout = io.BytesIO(b'Registered tunnel connection connIndex=0\n')
                main.stdout = None
                response = MagicMock()
                response.__enter__.return_value.status = 200
                def private_home():
                    service.home.mkdir(parents=True, exist_ok=True)
                try:
                    # 平台替身只验证隧道启动；接续桥健康读取同样隔离，避免访问真实端口和平台 TLS。
                    with patch.object(service.companion, '_probe', return_value=None), patch('harness.app.mcp_service.sys.platform', platform), patch.object(service, '_private_home', side_effect=private_home), patch.object(service, '_cloudflared', return_value='fixture-cloudflared'), patch.object(service, '_python', return_value='fixture-python'), patch.object(service, '_spawn', side_effect=[tunnel, main]) as spawn, patch('harness.app.mcp_service._connector_ready', return_value=True), patch('harness.app.mcp_service.build_opener', return_value=Mock(open=Mock(return_value=response))), patch('harness.app.mcp_service.QuickTunnelService') as quick:
                        service.start(project, tunnel=self.settings(origin_port))
                        service._thread.join(timeout=5)
                        self.assertFalse(service._thread.is_alive())
                        state = service.snapshot()
                        self.assertEqual(state['status'], 'running')
                        self.assertEqual(state['public_url'], 'https://mcp.example.test/mcp')
                        argv = spawn.call_args_list[0].args[0]
                        self.assertIn('run', argv)
                        self.assertNotIn('--url', argv)
                        self.assertNotIn('private-fixture-token', str(argv))
                        self.assertEqual(spawn.call_args_list[0].kwargs['env']['TUNNEL_TOKEN'], 'private-fixture-token')
                        main_argv = spawn.call_args_list[1].args[0]
                        self.assertEqual(main_argv[main_argv.index('--port') + 1], str(origin_port))
                        self.assertEqual(main_argv[main_argv.index('--public-url') + 1], 'https://mcp.example.test')
                        self.assertNotIn('private-fixture-token', json.dumps(state))
                        quick.assert_not_called()
                        with patch('harness.app.mcp_service._connector_ready', return_value=False):
                            reconnecting = service.snapshot()
                        self.assertTrue(reconnecting['reconnecting'])
                        self.assertEqual(reconnecting['public_url'], state['public_url'])
                        self.assertEqual(spawn.call_count, 2)
                        service.stop()
                    restored = McpService(root / 'private').snapshot()
                    self.assertTrue(restored['tunnel']['has_token'])
                    self.assertEqual(restored['tunnel']['mode'], 'named')
                    self.assertNotIn('private-fixture-token', json.dumps(restored))
                finally:
                    service.stop()

    def test_confirmed_rate_limit_blocks_new_process_without_automatic_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / 'project'
            project.mkdir()
            service = McpService(root / 'private')
            service._quick_retry_at = int(time.time()) + 300
            with patch.object(service, '_spawn') as spawn, patch.object(service, '_cloudflared') as prepare:
                for language in ('en', 'zh-CN', 'en'):
                    with current_language.set(language):
                        state = service.start(project)
                        self.assertEqual(state['status'], 'failed')
                        self.assertEqual(state['error_copy']['key'], 'ui.mcpService.error.rateLimited')
                        self.assertEqual(any('\u4e00' <= char <= '\u9fff' for char in state['error']), language == 'zh-CN')
                spawn.assert_not_called()
                prepare.assert_not_called()
            self.assertFalse(service.home.exists())

    def test_failure_diagnostic_redacts_secret_and_port_collision_is_explicit(self):
        output = _TunnelOutput('https://mcp.example.test', 'secret-fixture')
        output.consume(b'original secret-fixture authentication failed\n')
        self.assertNotIn('secret-fixture', output.failure(1).values['detail'])
        with tempfile.TemporaryDirectory() as directory, socket.socket() as occupied:
            occupied.bind(('127.0.0.1', 0))
            occupied.listen()
            root = Path(directory)
            project = root / 'project'
            project.mkdir()
            service = McpService(root / 'private')
            service._launch_tunnel = self.settings(occupied.getsockname()[1])
            with patch.object(service, '_cloudflared', return_value='fixture-cloudflared') as prepare, patch.object(service, '_python', return_value='fixture-python'), patch.object(service, '_spawn') as spawn:
                service._launch(project, 'project', None)
                for language in ('en', 'zh-CN', 'en'):
                    with current_language.set(language):
                        snapshot = service.snapshot()
                        self.assertEqual(snapshot['error_copy'], {'key': 'ui.mcpService.error.originPortBusy', 'params': {'port': occupied.getsockname()[1]}})
                        self.assertEqual(any('\u4e00' <= char <= '\u9fff' for char in snapshot['error']), language == 'zh-CN')
                prepare.assert_not_called()
                spawn.assert_not_called()
            service.stop()

    def test_local_start_api_accepts_named_settings_and_rejects_invalid_input_without_secret_echo(self):
        from types import SimpleNamespace
        from harness.web.api.mcp_service import build_router
        from harness.web.errors import BadRequest
        from harness.web.router import Request
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = McpService(root)
            ctx = SimpleNamespace(mcp_service=service, harness=SimpleNamespace(config=SimpleNamespace(home=root)), workspaces=Mock(return_value=Mock()))
            handler, params = build_router(ctx).resolve('POST', '/api/v1/mcp-service/start')
            def request(settings):
                return Request('POST', '/api/v1/mcp-service/start', {}, {'Host': 'localhost:8765', 'Origin': 'http://localhost:8765'}, json.dumps({'scope': 'all', 'tunnel': settings}).encode(), client_ip='127.0.0.1')
            with self.assertRaises(BadRequest) as caught:
                handler(request({**self.settings(), 'origin_port': True}), **params)
            self.assertEqual(caught.exception.code, 'mcp/tunnel-config')
            self.assertEqual(caught.exception.details['copy']['key'], 'ui.mcpService.error.tunnelConfig')
            self.assertFalse(service.home.exists())
            try:
                with patch('harness.app.mcp_service.threading.Thread'), patch.object(service, '_private_home', side_effect=lambda: service.home.mkdir(parents=True, exist_ok=True)):
                    response = handler(request(self.settings()), **params)
                data = json.loads(response.body)
                self.assertEqual(data['status'], 'starting')
                self.assertTrue(data['tunnel']['has_token'])
                self.assertNotIn('private-fixture-token', response.body.decode())
            finally:
                service._thread = None
                service.stop()

    def test_windows_system_proxy_fills_only_missing_values(self):
        with patch('harness.app.mcp_service.sys.platform', 'win32'), patch.dict('os.environ', {'HTTPS_PROXY': ''}, clear=True), patch('harness.app.mcp_service.urllib_request.getproxies_registry', create=True, return_value={'https': 'http://system:7890', 'http': 'http://system:7890'}):
            env = _tunnel_environment()
            self.assertEqual(env['HTTPS_PROXY'], '')
            self.assertEqual(env['HTTP_PROXY'], 'http://system:7890')
