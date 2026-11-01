"""登录监听与到期计时器按实例回收，验证使用隔离监听及可控线程。"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from harness.llm.chatgpt_auth import ChatGPTAccount


class LoginLifecycleTests(unittest.TestCase):
    def test_cancel_and_replacement_cancel_only_the_owned_timer(self):
        with tempfile.TemporaryDirectory() as directory:
            account = ChatGPTAccount(Path(directory))
            servers = [Mock(), Mock()]
            timers = [Mock(), Mock()]
            with patch('harness.llm.chatgpt_auth.ThreadingHTTPServer', side_effect=servers), \
                 patch('harness.llm.chatgpt_auth.threading.Thread'), \
                 patch('harness.llm.chatgpt_auth.threading.Timer', side_effect=timers):
                account.login()
                account.login()
                timers[0].cancel.assert_called_once()
                servers[0].shutdown.assert_called_once()
                servers[0].server_close.assert_called_once()
                account.cancel_login(servers[0])
                self.assertIs(account.server, servers[1])
                timers[1].cancel.assert_not_called()
                account.cancel_login()
                account.cancel_login()
                timers[1].cancel.assert_called_once()
                servers[1].shutdown.assert_called_once()
                servers[1].server_close.assert_called_once()
                self.assertIsNone(account.server)
                self.assertIsNone(account.pending)
                self.assertIsNone(account.login_timer)

    def test_thread_start_failure_closes_real_bound_socket_without_shutdown_deadlock(self):
        import socket
        from http.server import ThreadingHTTPServer
        with tempfile.TemporaryDirectory() as directory:
            account = ChatGPTAccount(Path(directory))
            server = ThreadingHTTPServer(('127.0.0.1', 0), lambda *args: None)
            address = server.server_address
            timer = Mock()
            try:
                with patch('harness.llm.chatgpt_auth.ThreadingHTTPServer', return_value=server), \
                     patch('harness.llm.chatgpt_auth.threading.Thread') as thread, \
                     patch('harness.llm.chatgpt_auth.threading.Timer', return_value=timer):
                    failure = RuntimeError('injected thread start failure')
                    thread.return_value.start.side_effect = failure
                    with self.assertRaises(RuntimeError) as caught:
                        account.login()
                    self.assertIs(caught.exception, failure)
                    timer.cancel.assert_called_once()
                    self.assertIsNone(account.server)
                    self.assertIsNone(account.pending)
                    self.assertIsNone(account.login_timer)
                with socket.socket() as probe:
                    probe.bind(address)
            finally:
                server.server_close()

    def test_timer_start_failure_closes_started_listener_and_cancels_timer(self):
        with tempfile.TemporaryDirectory() as directory:
            account = ChatGPTAccount(Path(directory))
            server, timer = Mock(), Mock()
            failure = RuntimeError('injected timer start failure')
            timer.start.side_effect = failure
            with patch('harness.llm.chatgpt_auth.ThreadingHTTPServer', return_value=server), \
                 patch('harness.llm.chatgpt_auth.threading.Thread'), \
                 patch('harness.llm.chatgpt_auth.threading.Timer', return_value=timer):
                with self.assertRaises(RuntimeError) as caught:
                    account.login()
            self.assertIs(caught.exception, failure)
            timer.cancel.assert_called_once()
            server.shutdown.assert_called_once()
            server.server_close.assert_called_once()
            self.assertIsNone(account.server)
            self.assertIsNone(account.login_timer)

    def test_shutdown_failure_still_releases_listener_and_timer(self):
        with tempfile.TemporaryDirectory() as directory:
            account = ChatGPTAccount(Path(directory))
            server, timer = Mock(), Mock()
            failure = OSError('injected shutdown failure')
            server.shutdown.side_effect = failure
            account.server, account.login_timer, account.pending = server, timer, 'fixture'
            with self.assertRaises(OSError) as caught:
                account.cancel_login()
            self.assertIs(caught.exception, failure)
            timer.cancel.assert_called_once()
            server.server_close.assert_called_once()
            self.assertIsNone(account.server)
            self.assertIsNone(account.login_timer)

    def test_status_snapshot_cannot_mutate_model_catalog_or_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            account = ChatGPTAccount(Path(directory))
            account.models = [{'id': 'chatgpt/fixture', 'label': 'Original 原文',
                               'reasoning_efforts': ['low', 'high']}]
            account.selected = 'chatgpt/fixture'
            first = account.status()
            first['models'][0]['id'] = 'chatgpt/other'
            first['models'][0]['reasoning_efforts'].append('invalid')
            first['models'].clear()
            second = account.status()
            self.assertEqual(second['models'], [{'id': 'chatgpt/fixture', 'label': 'Original 原文',
                                                'reasoning_efforts': ['low', 'high']}])
            self.assertEqual(second['selected'], 'chatgpt/fixture')
            self.assertEqual(account.models[0]['id'], account.selected)
            self.assertFalse(account.path.exists())

    def test_resolved_model_cannot_mutate_nested_catalog_options(self):
        with tempfile.TemporaryDirectory() as directory:
            account = ChatGPTAccount(Path(directory))
            account.models = [{'id': 'chatgpt/fixture', 'reasoning_efforts': ['low', 'high']}]
            account.selected = 'chatgpt/fixture'
            for requested in (None, 'chatgpt/fixture'):
                with self.subTest(requested=requested):
                    resolved = account.resolve(requested)
                    resolved['id'] = 'changed'
                    resolved['reasoning_efforts'].clear()
                    self.assertEqual(account.resolve(requested), {
                        'id': 'chatgpt/fixture', 'reasoning_efforts': ['low', 'high']})
                    self.assertEqual(account.status()['models'], account.models)
                    self.assertEqual(account.selected, 'chatgpt/fixture')
            self.assertFalse(account.path.exists())
