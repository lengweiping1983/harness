"""自有离开页面提示按读取语言解析，网页原文与回应身份保持。"""
import unittest
import io
import json
import subprocess
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from harness.app.browser_dialogs import BrowserDialogs
from harness.app.desktop_browser import DesktopBrowserService, DesktopDialog
from harness.core.errors import HarnessError
from harness.core.language import current_language
from harness.i18n import tr


class BrowserDialogLanguageTests(unittest.IsolatedAsyncioTestCase):
    async def test_http_rejection_keeps_code_copy_language_and_original_cause(self):
        service = DesktopBrowserService.__new__(DesktopBrowserService)
        script = "import {browserFailures} from './desktop/tests/browser-failures.mjs';process.stdout.write(JSON.stringify(browserFailures()))"
        failures = json.loads(subprocess.check_output(['node', '--input-type=module', '-e', script], cwd=Path(__file__).resolve().parents[1], text=True))
        for failure in failures:
            copy, code = failure['copy'], failure['code']
            for language in ('en', 'zh-CN', 'en'):
                body = io.BytesIO(json.dumps({'error': code, 'code': code, 'copy': copy}).encode())
                original = urllib.error.HTTPError('http://127.0.0.1/request', 400, 'Rejected', {}, body)
                with current_language.set(language), patch.dict('os.environ', {'HARNESS_DESKTOP_BRIDGE': 'http://127.0.0.1/request', 'HARNESS_DESKTOP_TOKEN': 'fixture'}), patch('urllib.request.urlopen', side_effect=original) as send:
                    with self.assertRaises(HarnessError) as caught:
                        await service.request('dialog_response', 'owner', id='Original', accept=True)
                    self.assertEqual(caught.exception.code, code)
                    self.assertEqual(caught.exception.message, tr(copy['key'], **copy['params']))
                    self.assertEqual(caught.exception.details['copy'], copy)
                    self.assertIs(caught.exception.__cause__, original)
                    send.assert_called_once()
                    self.assertTrue(body.closed)

    async def test_malformed_resource_failure_is_rejected_without_replaying_request(self):
        service = DesktopBrowserService.__new__(DesktopBrowserService)
        for copy in (None, [], {}, {'key': 1, 'params': {}}, {'key': '', 'params': {}}, {'key': 'browser.desktop.tabClosed', 'params': []}):
            for language in ('en', 'zh-CN'):
                body = io.BytesIO(json.dumps({'code': 'browser/tab-closed', 'copy': copy}).encode())
                original = urllib.error.HTTPError('http://127.0.0.1/request', 400, 'Rejected', {}, body)
                with current_language.set(language), patch.dict('os.environ', {'HARNESS_DESKTOP_BRIDGE': 'http://127.0.0.1/request', 'HARNESS_DESKTOP_TOKEN': 'fixture'}), patch('urllib.request.urlopen', side_effect=original) as send:
                    with self.assertRaises(HarnessError) as caught:
                        await service.request('dialog_response', 'owner', id='Original', accept=True)
                    self.assertEqual(caught.exception.code, 'browser/desktop-response')
                    self.assertEqual(caught.exception.message, tr('browser.desktop.invalidFailure'))
                    self.assertIs(caught.exception.__cause__, original)
                    send.assert_called_once()

    async def test_owned_copy_follows_reader_language_without_replaying_response(self):
        for key in ('browser.desktop.confirmClose', 'browser.desktop.confirmLeave'):
            calls = []

            async def request(*args, **kwargs):
                calls.append((args, kwargs))

            service = SimpleNamespace(request=request)
            snapshot = {'id': 'Original-native-id', 'type': 'beforeunload', 'message': '',
                        'message_copy': {'key': key, 'params': {}}}
            native = DesktopDialog(service, 'Original-owner', snapshot)
            dialogs = BrowserDialogs()
            page = type('Page', (), {'url': 'https://fixture.invalid'})()
            entry = dialogs.opened('Original-owner', page, 'target', native)
            for language in ('en', 'zh-CN', 'en'):
                with current_language.set(language):
                    view = entry.view()
                    self.assertEqual(view['message'], tr(key))
                    self.assertEqual(view['message_copy'], snapshot['message_copy'])
                    self.assertFalse(view['page_content_trusted'])
                    self.assertEqual(calls, [])
            await dialogs.respond(entry, {'accept': False})
            self.assertEqual(calls, [(('dialog_response', 'Original-owner'),
                                     {'id': 'Original-native-id', 'accept': False})])

    def test_website_text_is_not_translated_even_when_it_matches_owned_copy(self):
        for message in ('网页可能有尚未保存的内容。是否关闭此标签？', 'Original {name} 原文'):
            native = DesktopDialog(None, 'owner', {'id': 'native', 'type': 'beforeunload', 'message': message})
            dialogs = BrowserDialogs()
            page = type('Page', (), {'url': 'https://fixture.invalid'})()
            entry = dialogs.opened('owner', page, 'target', native)
            for language in ('en', 'zh-CN', 'en'):
                with current_language.set(language):
                    self.assertEqual(entry.view()['message'], message)
                    self.assertNotIn('message_copy', entry.view())
