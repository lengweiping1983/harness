"""登录回调参数保持单值身份，拒绝歧义且不交换授权码。"""
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlencode
from unittest.mock import Mock, patch

from harness.core.language import current_language
from harness.i18n import tr
from harness.llm.chatgpt_auth import ChatGPTAccount


class ChatGPTCallbackQueryTests(unittest.TestCase):
    def test_ambiguous_query_never_consumes_pending_login(self):
        for language in ('en', 'zh-CN', 'en'):
            with tempfile.TemporaryDirectory() as directory, current_language.set(language):
                account = ChatGPTAccount(Path(directory))
                with patch('harness.llm.chatgpt_auth.ThreadingHTTPServer') as server, \
                     patch('harness.llm.chatgpt_auth.threading.Thread'), \
                     patch('harness.llm.chatgpt_auth.threading.Timer'), \
                     patch.object(account, '_token') as token:
                    account.login()
                    callback_type = server.call_args.args[1]
                    state = account.pending
                    cases = [ [('state', state), ('state', state), ('code', 'original')],
                              [('state', state), ('state', ''), ('code', 'original')],
                              [('state', state), ('code', 'original'), ('code', 'other')],
                              [('state', state), ('code', 'original'), ('code', '')],
                              [('state', state), ('error', 'denied'), ('error', 'other')],
                              [('state', ''), ('state', state), ('code', 'original')] ]
                    for query in cases:
                        with self.subTest(language=language, query=query):
                            callback = object.__new__(callback_type)
                            callback.path = '/auth/callback?' + urlencode(query)
                            callback._respond = Mock()
                            callback.do_GET()
                            callback._respond.assert_called_once_with(400, tr('chatgpt.callback_invalid'))
                            self.assertEqual(account.pending, state)
                            token.assert_not_called()
                            self.assertFalse(account.path.exists())
                    account.cancel_login()

    def test_single_opaque_code_is_exchanged_once(self):
        with tempfile.TemporaryDirectory() as directory:
            account = ChatGPTAccount(Path(directory))
            with patch('harness.llm.chatgpt_auth.ThreadingHTTPServer') as server, \
                 patch('harness.llm.chatgpt_auth.threading.Thread'), \
                 patch('harness.llm.chatgpt_auth.threading.Timer'), \
                 patch.object(account, '_token', return_value={'access_token': 'fixture'}) as token, \
                 patch.object(account, 'save'), patch.object(account, 'refresh_models'):
                account.login()
                callback = object.__new__(server.call_args.args[1])
                code = 'Original+/% 原文'
                callback.path = '/auth/callback?' + urlencode({'state': account.pending, 'code': code})
                callback._respond = Mock()
                callback.do_GET()
                token.assert_called_once()
                self.assertEqual(token.call_args.args[0]['code'], code)
                self.assertIsNone(account.pending)
                self.assertEqual(callback._respond.call_args.args[0], 200)
                self.assertTrue(callback._respond.call_args.kwargs['success'])
                account.cancel_login()
