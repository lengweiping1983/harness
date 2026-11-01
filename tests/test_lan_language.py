"""局域网地址校验和启动提示双语回归，不监听实际网络端口。"""
import io
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import Mock, patch

from harness.core.language import current_language
from harness.i18n import tr
from harness.web.lan import serve_lan, validate_lan_origin


class LanLanguageTests(unittest.TestCase):
    def test_invalid_origins_share_localized_validation_and_valid_ports_are_exact(self):
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                for value in (None, '', 'http://[', 'http://192.168.1.2:0', 'http://192.168.1.2:65536',
                              'http://8.8.8.8:8765', 'http://0.0.0.0:8765', 'http://localhost:8765',
                              'http://192.168.1.2/path', 'http://user@192.168.1.2:8765', 'https://192.168.1.2:8765'):
                    with self.subTest(language=language, value=value), self.assertRaises(ValueError) as failure:
                        validate_lan_origin(value)
                    self.assertEqual(str(failure.exception), tr('web.lan.invalid_origin'))
                self.assertEqual(validate_lan_origin('http://192.168.1.2:8765'), ('192.168.1.2', 8765))
                self.assertEqual(validate_lan_origin('http://192.168.1.2'), ('192.168.1.2', 80))

    def test_start_messages_preserve_urls_and_close_both_owned_listeners(self):
        for language in ('en', 'zh-CN'):
            with current_language.set(language):
                local = SimpleNamespace(server_address=('127.0.0.1', 12345), _harness_loop=object())
                shared = object()
                app = SimpleNamespace(session_credentials=SimpleNamespace(authenticate=object()), close=Mock(return_value=object()))
                receipt = Mock()
                output = io.StringIO()
                with patch('harness.web.server.serve', side_effect=[local, shared]) as serve, \
                     patch('harness.web.server.stop') as stop, \
                     patch('harness.web.lan.wait_for_shutdown'), \
                     patch('harness.web.lan.asyncio.run_coroutine_threadsafe', return_value=receipt), redirect_stdout(output):
                    serve_lan(app, origin='http://192.168.1.2:8765')
                self.assertEqual(output.getvalue().splitlines(), [tr('web.lan.local', url='http://127.0.0.1:12345'),
                    tr('web.lan.shared', url='http://192.168.1.2:8765/shared')])
                self.assertEqual([call.args[0] for call in stop.call_args_list], [shared, local])
                self.assertIsNone(app.shared_origin)
                self.assertEqual(serve.call_args_list[1].kwargs['host'], '192.168.1.2')
                self.assertEqual(serve.call_args_list[1].kwargs['port'], 8765)
                receipt.result.assert_called_once_with(15)

    def test_nonloopback_management_fails_before_any_listener(self):
        for language in ('en', 'zh-CN'):
            with current_language.set(language), patch('harness.web.server.serve') as serve:
                with self.assertRaises(ValueError) as failure:
                    serve_lan(object(), origin='http://192.168.1.2:8765', host='0.0.0.0')
                self.assertEqual(str(failure.exception), tr('web.lan.loopback_required'))
                serve.assert_not_called()
