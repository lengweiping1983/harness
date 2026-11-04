"""macOS 隧道代理、失败原文和输出生命周期回归。"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from harness.app.mcp_service import McpService, _TunnelOutput, _tunnel_environment
from harness.core.language import current_language
from harness.i18n import tr


class TunnelStartupTests(unittest.TestCase):
    def test_macos_system_proxies_only_fill_missing_environment(self):
        with patch('harness.app.mcp_service.sys.platform', 'darwin'), patch.dict(os.environ, {}, clear=True), \
             patch('harness.app.mcp_service.urllib_request.getproxies_macosx_sysconf', create=True, return_value={
                 'https': 'http://127.0.0.1:7890', 'http': 'http://127.0.0.1:7890', 'no': 'localhost,127.0.0.1'}):
            env = _tunnel_environment()
            self.assertEqual(env['HTTPS_PROXY'], 'http://127.0.0.1:7890')
            self.assertEqual(env['NO_PROXY'], 'localhost,127.0.0.1')
            self.assertNotIn('HTTPS_PROXY', os.environ)
            with patch.dict(os.environ, {'http_proxy': 'http://explicit:9000'}):
                self.assertEqual(_tunnel_environment()['HTTPS_PROXY'], 'http://127.0.0.1:7890')
                self.assertEqual({key.lower(): value for key, value in _tunnel_environment().items()}['http_proxy'], 'http://explicit:9000')
            for name in ('https_proxy', 'HTTPS_PROXY'):
                with patch.dict(os.environ, {name: ''}):
                    self.assertEqual({key.lower(): value for key, value in _tunnel_environment().items()}[name.lower()], '')
                    self.assertEqual(len([k for k in _tunnel_environment() if k.lower() == 'https_proxy']), 1)

    def test_windows_environment_is_preserved_without_system_lookup(self):
        with patch('harness.app.mcp_service.sys.platform', 'win32'), patch.dict(os.environ, {
            'PATH': '/fixture', 'OPENAI_API_KEY': 'host-secret', 'NODE_OPTIONS': '--require=/host/inject.js',
            'PYTHONPATH': '/host/inject', 'GIT_CONFIG_GLOBAL': '/host/config'}, clear=True), \
             patch('harness.app.mcp_service.urllib_request.getproxies_registry', create=True, return_value={}), patch('harness.app.mcp_service.urllib_request.getproxies_macosx_sysconf', create=True) as proxies:
            env = _tunnel_environment()
            self.assertEqual(env.get('PATH'), '/fixture')
            for name in ('OPENAI_API_KEY', 'NODE_OPTIONS', 'PYTHONPATH', 'GIT_CONFIG_GLOBAL'):
                self.assertNotIn(name, env)
            proxies.assert_not_called()

    def test_split_url_and_unbounded_lines_keep_bounded_tail(self):
        output = _TunnelOutput()
        output.consume(b'x' * 20000)
        self.assertEqual(len(output.tail), 8192)
        output.consume(b'\nhttps://fixture.trycloud')
        self.assertFalse(output.ready.is_set())
        output.consume(b'flare.com.invalid\n')
        self.assertFalse(output.ready.is_set())
        output.consume(b'\nhttps://fixture.trycloudflare.com')
        self.assertFalse(output.ready.is_set())
        output.consume(b'\n')
        self.assertTrue(output.ready.is_set())
        self.assertEqual(output.url, 'https://fixture.trycloudflare.com')
        self.assertLessEqual(len(output.tail), 8192)

    def test_real_child_failure_reaches_snapshot_and_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root/'project'
            project.mkdir()
            service = McpService(root/'private')
            children = []
            diagnostic = 'quick tunnel provisioning failed with status 429: error code: 1015'

            def spawn(argv, **options):
                self.assertIn('env', options)
                child = subprocess.Popen([sys.executable, '-c', f'print({diagnostic!r});raise SystemExit(1)'], **options)
                children.append(child)
                return child

            try:
                with patch.object(service, '_cloudflared', return_value='fixture-cloudflared'), patch.object(service, '_spawn', side_effect=spawn):
                    service.start(project)
                    service._thread.join(timeout=5)
                    self.assertFalse(service._thread.is_alive())
                for language in ('en', 'zh-CN', 'en'):
                    with current_language.set(language):
                        snapshot = service.snapshot()
                        self.assertEqual(snapshot['status'], 'failed')
                        copy = snapshot['error_copy']
                        self.assertEqual(copy['key'], 'ui.mcpService.error.rateLimited')
                        self.assertEqual(copy['params'], {})
                        self.assertEqual(snapshot['tunnel_diagnostic'], diagnostic)
                        self.assertGreater(snapshot['quick_retry_at'], 0)
                        self.assertEqual(snapshot['error'], tr(copy['key'], **copy['params']))
                        self.assertEqual(snapshot['public_url'], '')
                self.assertEqual(len(children), 1)
                self.assertIsNotNone(children[0].poll())
                self.assertTrue(children[0].stdout.closed)
                self.assertIsNone(service._tunnel)
                self.assertIsNone(service._drain_thread)
            finally:
                service.stop()

    def test_timeout_diagnostic_keeps_external_text(self):
        output = _TunnelOutput()
        output.consume(b'connection timeout\n')
        self.assertEqual(output.failure(None).values, {'code': 'timeout', 'detail': 'connection timeout'})

    def test_invalid_utf8_diagnostic_preserves_original_bytes(self):
        output = _TunnelOutput()
        output.consume(b'\xff\xfe')
        self.assertEqual(output.failure(1).values['detail'], repr(b'\xff\xfe'))
