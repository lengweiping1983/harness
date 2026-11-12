"""临时域名申请的双平台代理传输与所属资源回归。"""
from http.client import HTTPConnection
from io import BytesIO
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from harness.app.mcp_service import McpService
from harness.app.mcp_errors import McpServiceError
from harness.app.quick_tunnel import MAX_RESPONSE, PROVISIONING_URL, QuickTunnelService, _NoRedirect
from harness.core.language import current_language
from harness.i18n import tr


class Response(BytesIO):
    """可核对原始字节与关闭状态的官方接口替身。"""
    status = 200


class QuickTunnelProxyTests(unittest.TestCase):
    def request(self, service, *, path=None, body=b'', headers=None):
        connection = HTTPConnection('127.0.0.1', service._server.server_port, timeout=3)
        try:
            connection.request('POST', path or service._path + '/tunnel', body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def test_single_official_request_uses_selected_proxy_and_preserves_result(self):
        payload = b'{"success":true,"result":{"secret":"private-fixture","hostname":"fixture.trycloudflare.com"}}'
        upstream = Response(payload)
        opener = Mock()
        opener.open.return_value = upstream
        with patch('harness.app.quick_tunnel.build_opener', return_value=opener) as build:
            service = QuickTunnelService('http://127.0.0.1:7890')
            try:
                service.start()
                self.assertEqual(self.request(service, headers={'User-Agent': 'cloudflared/fixture', 'Authorization': 'not-forwarded'}), (200, payload))
                self.assertEqual(self.request(service)[0], 409)
                opener.open.assert_called_once()
                request = opener.open.call_args.args[0]
                self.assertEqual(request.full_url, PROVISIONING_URL)
                self.assertEqual(request.get_method(), 'POST')
                self.assertEqual(request.data, b'')
                self.assertEqual(request.get_header('User-agent'), 'cloudflared/fixture')
                self.assertIsNone(request.get_header('Authorization'))
                self.assertEqual(build.call_args.args[0].proxies, {'https': 'http://127.0.0.1:7890'})
                self.assertTrue(upstream.closed)
            finally:
                service.stop()
            self.assertFalse(service._thread.is_alive())
            self.assertEqual(service._server.socket.fileno(), -1)

    def test_rate_limit_is_preserved_without_automatic_retry(self):
        payload = b'error code: 1015'
        opener = Mock()
        opener.open.side_effect = HTTPError(PROVISIONING_URL, 429, 'Too Many Requests', {}, BytesIO(payload))
        with patch('harness.app.quick_tunnel.build_opener', return_value=opener):
            service = QuickTunnelService('http://fixture.invalid')
            try:
                service.start()
                self.assertEqual(self.request(service), (429, payload))
                self.assertEqual(self.request(service)[0], 409)
                self.assertEqual(opener.open.call_count, 1)
            finally:
                service.stop()

    def test_invalid_requests_cannot_consume_or_forward_provisioning(self):
        opener = Mock()
        opener.open.return_value = Response(b'{}')
        with patch('harness.app.quick_tunnel.build_opener', return_value=opener):
            service = QuickTunnelService('http://fixture.invalid')
            try:
                service.start()
                for options in ({'path': '/tunnel'}, {'body': b'x'}, {'headers': {'Origin': 'https://external.invalid'}}, {'headers': {'Transfer-Encoding': 'chunked'}}):
                    self.assertEqual(self.request(service, **options)[0], 400)
                    opener.open.assert_not_called()
                self.assertEqual(self.request(service)[0], 200)
                opener.open.assert_called_once()
            finally:
                service.stop()

    def test_transport_failure_and_size_limit_keep_unconfirmed_result_bilingual(self):
        for language in ('en', 'zh-CN', 'en'):
            for failure in ('timeout', 'oversize'):
                opener = Mock()
                if failure == 'timeout':
                    opener.open.side_effect = OSError('original transport failure')
                else:
                    opener.open.return_value = Response(b'x' * (MAX_RESPONSE + 1))
                with self.subTest(language=language, failure=failure), current_language.set(language), patch('harness.app.quick_tunnel.build_opener', return_value=opener):
                    service = QuickTunnelService('http://fixture.invalid')
                    try:
                        service.start()
                        status, body = self.request(service)
                        self.assertEqual(status, 502)
                        detail = 'original transport failure' if failure == 'timeout' else 'provisioning response limit'
                        self.assertIn(tr('ui.mcpService.error.provisioningTransport', detail=detail), body.decode('utf-8'))
                        self.assertEqual(self.request(service)[0], 409)
                        opener.open.assert_called_once()
                    finally:
                        service.stop()

    def test_stop_retains_pending_accepted_request_until_it_finishes(self):
        entered, release = threading.Event(), threading.Event()
        opener = Mock()
        def upstream(*args, **kwargs):
            entered.set()
            release.wait(5)
            return Response(b'{}')
        opener.open.side_effect = upstream
        with patch('harness.app.quick_tunnel.build_opener', return_value=opener):
            service = QuickTunnelService('http://fixture.invalid')
            service.start()
            client = threading.Thread(target=lambda: self.request(service))
            try:
                client.start()
                self.assertTrue(entered.wait(1))
                with patch.object(service._thread, 'join'):
                    with self.assertRaises(McpServiceError):
                        service.stop()
                self.assertTrue(service._thread.is_alive())
                release.set()
                client.join(3)
                service.stop()
                self.assertFalse(service._thread.is_alive())
                opener.open.assert_called_once()
            finally:
                release.set()
                client.join(3)
                service.stop()

    def test_redirect_is_not_replayed(self):
        self.assertIsNone(_NoRedirect().redirect_request(None, None, 307, '', {}, 'https://elsewhere.invalid'))

    def test_windows_with_proxy_uses_owned_provisioning_service(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / 'project'
            project.mkdir()
            manager = McpService(root / 'private')
            with patch('harness.app.mcp_service.sys.platform', 'win32'), patch.object(manager, '_cloudflared', return_value='fixture-cloudflared'), \
                 patch('harness.app.mcp_service._tunnel_environment', return_value={'HTTPS_PROXY': 'http://fixture.invalid'}), \
                 patch('harness.app.mcp_service.QuickTunnelService') as proxy, patch.object(manager, '_spawn', side_effect=RuntimeError('stop before child')) as spawn:
                manager.start(project)
                manager._thread.join(3)
                self.assertFalse(manager._thread.is_alive())
                argv = spawn.call_args.args[0]
                self.assertEqual(argv[:2], ['fixture-cloudflared', '--config'])
                self.assertEqual(argv[3], 'tunnel')
                self.assertIn('--url', argv)
                self.assertEqual(argv[argv.index('--protocol') + 1], 'http2')
                self.assertIn('--no-autoupdate', argv)
                self.assertIn('--metrics', argv)
                self.assertIn('--quick-service', argv)
                proxy.assert_called_once_with('http://fixture.invalid')
                proxy.return_value.start.assert_called_once()
                proxy.return_value.stop.assert_called_once()
                self.assertIsNone(manager._quick_service)
            manager.stop()

    def test_macos_with_proxy_creates_one_owned_service_and_cleans_after_spawn_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / 'project'
            project.mkdir()
            manager = McpService(root / 'private')
            with patch('harness.app.mcp_service.sys.platform', 'darwin'), patch.object(manager, '_cloudflared', return_value='fixture-cloudflared'), \
                 patch('harness.app.mcp_service._tunnel_environment', return_value={'https_proxy': 'http://fixture.invalid'}), \
                 patch('harness.app.mcp_service.QuickTunnelService') as proxy, patch.object(manager, '_spawn', side_effect=RuntimeError('stop before child')) as spawn:
                proxy.return_value.start.return_value = 'http://127.0.0.1:1234/nonce'
                manager.start(project)
                manager._thread.join(3)
                argv = spawn.call_args.args[0]
                self.assertEqual(argv[-2:], ['--quick-service', 'http://127.0.0.1:1234/nonce'])
                proxy.assert_called_once_with('http://fixture.invalid')
                proxy.return_value.start.assert_called_once()
                proxy.return_value.stop.assert_called_once()
                self.assertIsNone(manager._quick_service)
            manager.stop()

    def test_failed_proxy_cleanup_keeps_ownership_and_blocks_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = McpService(directory)
            proxy = Mock()
            original = OSError('original proxy cleanup failure')
            proxy.stop.side_effect = original
            manager._quick_service = proxy
            with self.assertRaises(OSError) as caught:
                manager.stop()
            self.assertIs(caught.exception, original)
            self.assertIs(manager._quick_service, proxy)
            self.assertEqual(manager.snapshot()['status'], 'stopping')
            proxy.stop.side_effect = None
            self.assertEqual(manager.stop()['status'], 'stopped')
            self.assertIsNone(manager._quick_service)
