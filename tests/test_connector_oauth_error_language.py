"""后台授权的自有固定错误在读取时解析，外部诊断不反向翻译。"""
import threading
import unittest
from unittest.mock import AsyncMock, Mock, patch

from harness.connectors.manager import ConnectorManager
from harness.connectors.oauth import AuthorizationServer, OAuthError, _run_device_flow
from harness.connectors.store import ConnectorRecord
from harness.core.language import current_language
from harness.i18n import tr


class ConnectorOAuthErrorLanguageTests(unittest.IsolatedAsyncioTestCase):
    async def test_client_id_names_and_separator_follow_read_language_without_environment_changes(self):
        from harness.connectors.oauth import resolve_client_id
        connector = 'Original-name'
        names = ['ORIGINAL_CLIENT', 'HARNESS_OAUTH_CLIENT_ID_ORIGINAL_NAME']
        with patch.dict('os.environ', {}, clear=True), current_language.set('en'):
            with self.assertRaises(OAuthError) as caught:
                resolve_client_id(connector, {'client_id_env': names[0]})
            failure = caught.exception
            snapshot = failure.message
            for language in ('en', 'zh-CN', 'en'):
                with current_language.set(language):
                    self.assertEqual(failure.localized_text(), '[connector/oauth-no-client] ' +
                        tr('python.oauth.e728da5d3340', p0=repr(connector), p1=tr('common.or').join(names)))
            self.assertEqual(failure.message, snapshot)
        with patch.dict('os.environ', {names[0]: ' original client '}, clear=True):
            self.assertEqual(resolve_client_id(connector, {'client_id_env': names[0]}), 'original client')

    async def test_nested_discovery_errors_resolve_both_layers_and_keep_network_reason(self):
        from harness.connectors import oauth
        original = OSError('Original diagnostic 原文 {url}')
        url = 'https://fixture.invalid'
        with current_language.set('en'), patch('urllib.request.urlopen', side_effect=original) as request:
            with self.assertRaises(OAuthError) as caught:
                oauth.discover_authorization_server(url)
        failure = caught.exception
        snapshot = failure.message
        self.assertEqual(request.call_count, 1)
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                inner = '[connector/oauth] ' + tr('python.oauth.ca748ae3eb55', p0=str(original),
                    p1=url+'/.well-known/oauth-authorization-server')
                expected = '[connector/oauth-discovery] ' + tr('python.oauth.2abc37f65921', p0=url, p1=inner)
                self.assertEqual(failure.localized_text(), expected)
        self.assertEqual(failure.message, snapshot)
        self.assertIs(failure.copy[1]['p1'].__cause__, original)

    async def test_real_device_refusal_retains_identity_without_restarting_authorization(self):
        server = AuthorizationServer('https://fixture.invalid', 'https://fixture.invalid/auth', 'https://fixture.invalid/token')
        with current_language.set('en'):
            with self.assertRaises(OAuthError) as caught:
                await _run_device_flow(server, 'client', [], on_pending=Mock(), open_browser=False, timeout=1)
        failure = caught.exception
        original_snapshot = failure.message
        manager = object.__new__(ConnectorManager)
        record = ConnectorRecord('fixture', transport='streamable-http', metadata={'oauth': {'scopes': []}})
        manager.store = Mock()
        manager.store.get.return_value = record
        manager.credentials = Mock()
        manager.credentials.get.return_value = None
        manager.credentials.backend_name = 'memory'
        manager._oauth_epochs = {'fixture': 0}
        manager._oauth_lock = threading.RLock()
        manager._oauth_pending = {'fixture': {'state': 'pending'}}
        manager._oauth_error = {}
        manager._emit_changed = AsyncMock()
        with patch('harness.connectors.manager.run_authorization', new_callable=AsyncMock, side_effect=failure) as authorize:
            await manager._oauth_login_task('fixture', False, 0)
            self.assertIs(manager._oauth_error['fixture'], failure)
            for language in ('en', 'zh-CN', 'en'):
                with current_language.set(language):
                    status = manager.oauth_status('fixture')
                    self.assertEqual(status['state'], 'error')
                    self.assertEqual(status['message'], '[connector/oauth-unsupported] ' + tr('python.oauth.77387febd78a'))
            authorize.assert_awaited_once()
        self.assertEqual(failure.message, original_snapshot)
        manager.credentials.set.assert_not_called()
        manager._emit_changed.assert_awaited_once_with('connector/oauth-error', 'fixture')
        raw = OAuthError(original_snapshot)
        for language in ('en', 'zh-CN'):
            with current_language.set(language):
                self.assertEqual(raw.localized_text(), str(raw))
