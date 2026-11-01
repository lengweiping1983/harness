"""握手须先验证协议，不能将普通HTTP请求直接升级。"""
import unittest
from email.message import Message
from http.server import BaseHTTPRequestHandler
from types import SimpleNamespace
from unittest.mock import Mock, patch

from harness.core.language import current_language
from harness.i18n import tr
from harness.web.server import Handler


class WsHandshakeValidationTests(unittest.TestCase):
    def handler(self):
        handler = object.__new__(Handler)
        handler.harness = SimpleNamespace(config=SimpleNamespace(language=current_language.get()))
        handler.command = 'GET'
        handler.request_version = 'HTTP/1.1'
        handler.path = '/ws?session=fixture'
        handler.headers = Message()
        for name, value in {'Host':'127.0.0.1:8765', 'Upgrade':'websocket', 'Connection':'keep-alive, Upgrade',
                            'Sec-WebSocket-Key':'dGhlIHNhbXBsZSBub25jZQ==', 'Sec-WebSocket-Version':'13'}.items():
            handler.headers[name] = value
        handler._web_ctx = SimpleNamespace(shared_access=None)
        handler._origin_allowed = Mock(return_value=True)
        handler._error = Mock()
        handler.do_GET_ws = Mock()
        return handler

    def test_invalid_handshakes_cannot_upgrade(self):
        cases = [('method','POST'), ('version','HTTP/1.0'), ('Host',''), ('Upgrade','other'), ('Connection','keep-alive'),
                 ('Sec-WebSocket-Version','12'), ('Sec-WebSocket-Key',''), ('Sec-WebSocket-Key','not-base64'),
                 ('Sec-WebSocket-Key','YQ=='), ('Sec-WebSocket-Key','中文')]
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                for field, value in cases:
                    with self.subTest(language=language, field=field, value=value):
                        handler = self.handler()
                        if field == 'method':
                            handler.command = value
                        elif field == 'version':
                            handler.request_version = value
                        else:
                            handler.headers.replace_header(field, value)
                        with patch.object(BaseHTTPRequestHandler, 'parse_request', return_value=True):
                            self.assertFalse(handler.parse_request())
                        handler.do_GET_ws.assert_not_called()
                        handler._error.assert_called_once()
                        error = handler._error.call_args.args[0]
                        self.assertEqual(error.message, tr('web.websocket.invalid_handshake', field=field))
                        self.assertEqual(error.status, 426 if field == 'Sec-WebSocket-Version' else 400)
                        self.assertTrue(handler.close_connection)

    def test_duplicate_single_value_headers_are_rejected(self):
        for field in ('Host', 'Sec-WebSocket-Key', 'Sec-WebSocket-Version'):
            handler = self.handler()
            handler.headers[field] = handler.headers[field]
            with patch.object(BaseHTTPRequestHandler, 'parse_request', return_value=True):
                self.assertFalse(handler.parse_request())
            handler._error.assert_called_once()
            handler.do_GET_ws.assert_not_called()

    def test_browser_tokens_are_case_insensitive_and_connection_fields_combine(self):
        handler = self.handler()
        handler.headers.replace_header('Upgrade', 'WebSocket')
        handler.headers.replace_header('Connection', 'keep-alive')
        handler.headers['Connection'] = 'uPgRaDe'
        with patch.object(BaseHTTPRequestHandler, 'parse_request', return_value=True):
            self.assertFalse(handler.parse_request())
        handler._error.assert_not_called()
        handler.do_GET_ws.assert_called_once_with('fixture', '0', snapshot_view='full')

    def test_version_error_advertises_supported_version(self):
        from harness.web.errors import BadRequest
        handler = self.handler()
        handler._send = Mock()
        error = BadRequest('Original error', code='websocket/version', status=426)
        Handler._error(handler, error)
        response = handler._send.call_args.args[0]
        self.assertEqual(response.status, 426)
        self.assertEqual(response.headers['Sec-WebSocket-Version'], '13')
