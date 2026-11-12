"""账号提示按当前语言解析，真实回调线程只操作隔离账号和模拟传输。"""
import base64
import io
import json
import tempfile
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from html import escape
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from harness.core.language import current_language
from harness.i18n import tr
from harness.llm.chatgpt_auth import ChatGPTAccount, ChatGPTAccountError, request_json


class ChatGPTAccountLanguageTests(unittest.TestCase):
    def test_account_errors_and_corrupt_original_follow_language(self):
        with tempfile.TemporaryDirectory() as directory:
            account = ChatGPTAccount(Path(directory))
            for language in ('en', 'zh-CN', 'en'):
                with current_language.set(language):
                    for operation, key in ((account.headers, 'chatgpt.login_required'),
                                           (account.resolve, 'chatgpt.model_unavailable'),
                                           (lambda: account.select('missing'), 'chatgpt.model_unknown')):
                        with self.assertRaises(ValueError) as caught:
                            operation()
                        self.assertEqual(str(caught.exception), tr(key))
                    path = Path(directory) / 'chatgpt-account.json'
                    path.write_bytes(b'{broken')
                    with self.assertRaises(ValueError) as caught:
                        ChatGPTAccount(Path(directory))
                    self.assertEqual(str(caught.exception), tr('chatgpt.file_invalid'))
                    self.assertEqual(path.read_bytes(), b'{broken')
            account.error = ChatGPTAccountError('chatgpt.http_failed', status=429)
            for language in ('en', 'zh-CN', 'en'):
                with current_language.set(language):
                    self.assertEqual(account.status()['error'], tr('chatgpt.http_failed', status=429))

    def test_oversized_expiry_is_rejected_or_defaulted_without_leaking_overflow(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'chatgpt-account.json'
            original = json.dumps({
                'version': 1,
                'credentials': {
                    'access': 'fixture', 'refresh': 'fixture', 'account_id': 'fixture',
                    'email': 'fixture@example.invalid', 'expires': 10 ** 4000,
                },
                'models': [], 'selected': '',
            })
            path.write_text(original, encoding='utf-8')
            with self.assertRaises(ChatGPTAccountError) as caught:
                ChatGPTAccount(Path(directory))
            self.assertEqual(str(caught.exception), tr('chatgpt.file_invalid'))
            self.assertEqual(path.read_text(encoding='utf-8'), original)

        with tempfile.TemporaryDirectory() as directory:
            token = 'fixture.' + base64.urlsafe_b64encode(
                json.dumps({'chatgpt_account_id': 'fixture'}).encode()
            ).decode().rstrip('=') + '.fixture'
            account = ChatGPTAccount(Path(directory), request=Mock(return_value={
                'access_token': token, 'refresh_token': 'fixture', 'expires_in': 10 ** 4000,
            }))
            with patch('harness.llm.chatgpt_auth.time.time', return_value=1000):
                credentials = account._token({'grant_type': 'authorization_code'})
            self.assertEqual(credentials['expires'], 4600)

    def test_owned_response_errors_are_localized_without_exposing_response_data(self):
        for language in ('en', 'zh-CN'):
            with current_language.set(language):
                for raw, key in ((b'[]', 'chatgpt.response_object'), (b'{private', 'chatgpt.network_failed'),
                                 (b'\xff', 'chatgpt.network_failed'), (b'x' * (2 * 1024 * 1024 + 1), 'chatgpt.response_large')):
                    with patch('urllib.request.build_opener') as opener:
                        opener.return_value.open.return_value = io.BytesIO(raw)
                        with self.assertRaises(ChatGPTAccountError) as caught:
                            request_json('https://fixture.invalid/')
                        self.assertEqual(str(caught.exception), tr(key))
                        self.assertNotIn('private', str(caught.exception))

    def test_api_storage_failure_is_localized_and_redacts_operating_system_details(self):
        import asyncio
        from harness.web.api.chatgpt import build_router
        from harness.web.errors import BadRequest
        from harness.web.router import Request
        account = SimpleNamespace(logout=Mock(side_effect=OSError('private account path')))
        ctx = SimpleNamespace(harness=SimpleNamespace(chatgpt=account), call=lambda coro, **kwargs: asyncio.run(coro))
        handler, _ = build_router(ctx).resolve('POST', '/api/v1/chatgpt/logout')
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language), self.assertRaises(BadRequest) as caught:
                handler(Request('POST', '/api/v1/chatgpt/logout', {}, {}, b'{}'))
            self.assertEqual(caught.exception.code, 'chatgpt/failed')
            with current_language.set(language):
                self.assertEqual(caught.exception.message, tr('chatgpt.state_save_failed'))
            self.assertNotIn('private', caught.exception.message)

    def test_callback_captures_login_language_and_status_uses_reading_language(self):
        token = 'fixture.' + base64.urlsafe_b64encode(json.dumps({'chatgpt_account_id': 'fixture', 'email': 'fixture@example.invalid'}).encode()).decode().rstrip('=') + '.fixture'
        for language in ('en', 'zh-CN'):
            for approve in (False, True):
                with self.subTest(language=language, approve=approve), tempfile.TemporaryDirectory() as directory:
                    request = Mock(side_effect=[{'access_token': token, 'refresh_token': 'fixture'},
                                                {'models': [{'slug': 'fixture', 'display_name': 'Model 原文', 'description': 'Original 描述'}]}])
                    account = ChatGPTAccount(Path(directory), request=request)
                    servers = []
                    def create_server(address, handler):
                        server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
                        servers.append(server)
                        return server
                    try:
                        with current_language.set(language), \
                             patch('harness.llm.chatgpt_auth.ThreadingHTTPServer', side_effect=create_server), \
                             patch('harness.llm.chatgpt_auth.threading.Timer', return_value=SimpleNamespace(start=lambda: None, cancel=lambda: None)):
                            started = account.login()
                        state = urllib.parse.parse_qs(urllib.parse.urlsplit(started['authorization_url']).query)['state'][0]
                        base = 'http://127.0.0.1:' + str(servers[0].server_address[1]) + '/auth/callback?'
                        with self.assertRaises(urllib.error.HTTPError) as caught:
                            urllib.request.urlopen(base + urllib.parse.urlencode({'state': '错误'}), timeout=3)
                        with caught.exception as response, current_language.set(language):
                            self.assertEqual(response.code, 400)
                            self.assertIn(escape(tr('chatgpt.callback_invalid')), response.read().decode())
                            self.assertEqual(response.headers.get_content_type(), 'text/html')
                        self.assertIsNotNone(account.pending)
                        query = {'state': state, 'code': 'fixture'} if approve else {'state': state, 'error': 'access_denied'}
                        with urllib.request.urlopen(base + urllib.parse.urlencode(query), timeout=3) as response, current_language.set(language):
                            page = response.read().decode()
                            self.assertIn(escape(tr('chatgpt.complete' if approve else 'chatgpt.declined')), page)
                            self.assertIn('data-state="success"' if approve else 'data-state="attention"', page)
                            self.assertIn("history.replaceState(null, '', '/auth/callback')", page)
                            self.assertEqual(response.headers['Cache-Control'], 'no-store')
                            self.assertIn("frame-ancestors 'none'", response.headers['Content-Security-Policy'])
                            self.assertNotIn(state, page)
                        for reading_language in ('en', 'zh-CN'):
                            with current_language.set(reading_language):
                                status = account.status()
                                self.assertEqual(status['error'], '' if approve else tr('chatgpt.declined'))
                                self.assertEqual(status['logged_in'], approve)
                                if approve:
                                    self.assertEqual(status['models'][0]['label'], 'Model 原文')
                                    self.assertEqual(status['models'][0]['description'], 'Original 描述')
                        self.assertEqual(request.call_count, 2 if approve else 0)
                    finally:
                        account.cancel_login()
                        for server in servers:
                            server.shutdown()
                            server.server_close()