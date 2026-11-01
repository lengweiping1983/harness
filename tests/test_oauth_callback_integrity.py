"""隔离回调端口验证语言、拒绝后恢复和首次回执，不访问外部账号。"""
import io
import json
import unittest
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from threading import Event
from unittest.mock import patch

from harness.connectors import oauth
from harness.core.language import current_language
from harness.i18n import tr
from harness.tools.builtin.workflow_host import validate_against_schema
from harness.core.errors import ToolExecutionError


class OAuthCallbackIntegrityTests(unittest.TestCase):
    def test_invalid_callback_keeps_flow_alive_and_valid_callback_uses_login_language(self):
        server = oauth.AuthorizationServer('https://fixture.invalid', 'https://fixture.invalid/auth', 'https://fixture.invalid/token')
        for language in ('en', 'zh-CN'):
            for denied in (False, True):
                pending = Queue()
                def run():
                    with current_language.set(language):
                        return oauth._run_code_flow(server, 'fixture', [], resource='', on_pending=pending.put,
                                                    open_browser=False, timeout=5)
                with patch.object(oauth, '_http_json', return_value={'access_token': 'fixture'}) as exchange, \
                     ThreadPoolExecutor(max_workers=1) as pool:
                    task = pool.submit(run)
                    query = urllib.parse.parse_qs(urllib.parse.urlsplit(pending.get(timeout=2).authorize_url).query)
                    base, state = query['redirect_uri'][0], query['state'][0]
                    invalid = [({'state': 'wrong', 'error': 'access_denied'}, '/callback'),
                               ({'state': state, 'code': ''}, '/callback'),
                               ({'state': state, 'code': 'one', 'error': 'access_denied'}, '/callback'),
                               ({'state': [state, state], 'code': 'one'}, '/callback'),
                               ({'state': state, 'code': 'one'}, '/other')]
                    for values, path in invalid:
                        url = base.replace('/callback', path) + '?' + urllib.parse.urlencode(values, doseq=True)
                        with self.assertRaises(urllib.error.HTTPError) as caught:
                            urllib.request.urlopen(url, timeout=2)
                        with caught.exception as response, current_language.set(language):
                            self.assertEqual(response.code, 404 if path == '/other' else 400)
                            body = response.read().decode()
                            if response.code == 400:
                                self.assertEqual(body, tr('oauth.callback_invalid'))
                        self.assertFalse(task.done())
                        exchange.assert_not_called()
                    values = {'state': state, **({'error': 'access_denied'} if denied else {'code': 'fixture-code'})}
                    with urllib.request.urlopen(base + '?' + urllib.parse.urlencode(values), timeout=2) as response, current_language.set(language):
                        self.assertEqual(response.read().decode(), tr('python.oauth.7908660018e2'))
                    if denied:
                        with self.assertRaises(oauth.OAuthError) as caught:
                            task.result(timeout=3)
                        self.assertEqual(caught.exception.code, 'connector/oauth-denied')
                        exchange.assert_not_called()
                    else:
                        self.assertEqual(task.result(timeout=3).access_token, 'fixture')
                        exchange.assert_called_once()
                        self.assertEqual(exchange.call_args.kwargs['form']['code'], 'fixture-code')

    def test_duplicate_callback_cannot_replace_first_code(self):
        pending, entered, release = Queue(), Event(), Event()
        server = oauth.AuthorizationServer('https://fixture.invalid', 'https://fixture.invalid/auth', 'https://fixture.invalid/token')
        def exchange(*args, **kwargs):
            entered.set()
            if not release.wait(3):
                raise AssertionError('令牌交换屏障未释放')
            return {'access_token': 'fixture'}
        def run():
            with current_language.set('en'):
                return oauth._run_code_flow(server, 'fixture', [], resource='', on_pending=pending.put,
                                            open_browser=False, timeout=5)
        with patch.object(oauth, '_http_json', side_effect=exchange) as request, ThreadPoolExecutor(max_workers=1) as pool:
            task = pool.submit(run)
            try:
                query = urllib.parse.parse_qs(urllib.parse.urlsplit(pending.get(timeout=2).authorize_url).query)
                base, state = query['redirect_uri'][0], query['state'][0]
                with urllib.request.urlopen(base + '?' + urllib.parse.urlencode({'state': state, 'code': 'first'}), timeout=2) as response:
                    self.assertEqual(response.status, 200)
                self.assertTrue(entered.wait(2))
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(base + '?' + urllib.parse.urlencode({'state': state, 'code': 'second'}), timeout=2)
                with caught.exception as response, current_language.set('en'):
                    self.assertEqual(response.code, 409)
                    self.assertEqual(response.read().decode(), tr('oauth.callback_consumed'))
            finally:
                release.set()
            self.assertEqual(task.result(timeout=3).access_token, 'fixture')
            request.assert_called_once()
            self.assertEqual(request.call_args.kwargs['form']['code'], 'first')

    def test_device_error_json_is_parsed_before_display_truncation_and_response_is_closed(self):
        raw = json.dumps({'error': 'authorization_pending', 'error_description': 'x' * 1000}).encode()
        body = io.BytesIO(raw)
        error = urllib.error.HTTPError('https://fixture.invalid', 400, 'fixture', {}, body)
        with patch.object(oauth.urllib.request, 'urlopen', side_effect=error):
            self.assertEqual(oauth._http_json('https://fixture.invalid', tolerate_error_body=True)['error'], 'authorization_pending')
        self.assertTrue(body.closed)
        body = io.BytesIO(b'')
        error = urllib.error.HTTPError('https://fixture.invalid', 401, 'fixture',
            {'WWW-Authenticate': 'Bearer resource_metadata="https://fixture.invalid/meta"'}, body)
        with patch.object(oauth.urllib.request, 'urlopen', side_effect=error):
            self.assertEqual(oauth._resource_metadata_from_401('https://fixture.invalid'), 'https://fixture.invalid/meta')
        self.assertTrue(body.closed)

    def test_workflow_integer_comparison_preserves_precision_and_numeric_equivalence(self):
        for keyword in ('const', 'enum'):
            schema = {'type': 'object', 'properties': {'n': {'type': 'integer', keyword: [2**53] if keyword == 'enum' else 2**53}}}
            validate_against_schema({'n': 2**53}, schema)
            validate_against_schema({'n': float(2**53)}, schema)
            with self.assertRaises(ToolExecutionError):
                validate_against_schema({'n': 2**53 + 1}, schema)
        huge = 10**400
        validate_against_schema({'n': huge}, {'type': 'object', 'properties': {'n': {'type': 'integer', 'const': huge}}})
