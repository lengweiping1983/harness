"""本机 CLI 仅操作统一账本；状态、身份拒绝及参数冲突不启动模型。"""
import io
import json
import tempfile
import unittest
from pathlib import Path
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch

from harness.cli import main
from harness.app.superagent_local import submit_local, read_status
from harness.core.language import current_language
from tests.superagent_product_fixture import product_config
from tests.filesystem_links import directory_link


class SuperagentLocalTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path, self.spec = product_config(self.root)

    def test_cli_submit_and_status_need_no_credentials_and_construct_no_hosts(self):
        output = io.StringIO()
        with patch('harness.app.superagent_bootstrap.Harness', side_effect=AssertionError('不得启动模型')), redirect_stdout(output):
            self.assertEqual(main(['--superagent', str(self.path), '--superagent-submit', 'Fix the typo.', '--superagent-request-id', 'stable']), 0)
        original = json.loads(output.getvalue())
        output = io.StringIO()
        with patch('harness.app.superagent_bootstrap.Harness', side_effect=AssertionError('不得启动模型')), redirect_stdout(output):
            self.assertEqual(main(['--superagent', str(self.path), '--superagent-status']), 0)
        self.assertEqual(json.loads(output.getvalue())['instance_id'], 'product')
        self.assertEqual(submit_local(self.path, 'Fix the typo.', request_id='stable')['trigger_id'], original['trigger_id'])
        self.assertFalse((self.root / 'state' / 'runtime').exists())

    def test_unauthorized_or_ambiguous_identity_is_rejected_before_creating_state(self):
        for selectors in ({'actor_id':'outsider'}, {'source_id':'unknown'}, {'thread_id':'another'}):
            with self.subTest(selectors=selectors), self.assertRaises(Exception):
                submit_local(self.path, 'Do work.', **selectors)
        self.spec['sources']['local']['actors'].append('human:2')
        self.path.write_text(json.dumps(self.spec))
        with self.assertRaises(Exception):
            submit_local(self.path, 'Do work.')
        self.assertFalse((self.root / 'state').exists())

    def test_directory_link_cannot_redirect_local_submission(self):
        external = self.root / 'external'
        external.mkdir()
        directory_link(self.root / 'state', external)
        with self.assertRaises(Exception):
            submit_local(self.path, 'Do work.')
        self.assertEqual(list(external.iterdir()), [])

    def test_auxiliary_modes_reject_conflicts_before_touching_state(self):
        for args in (['--superagent-status'], ['--superagent', str(self.path), '--superagent-status', '--superagent-once'],
                     ['--superagent', str(self.path), '--superagent-mission', 'm'],
                     ['--superagent', str(self.path), '--superagent-cancel']):
            with self.subTest(args=args), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                main(args)
        self.assertFalse((self.root / 'state').exists())

    def test_help_consumes_english_chinese_english_without_constructing_host(self):
        help_text = []
        for language in ('en', 'zh-CN', 'en'):
            output = io.StringIO()
            with current_language.set(language), redirect_stdout(output), self.assertRaises(SystemExit):
                main(['--language', language, '--help'])
            help_text.append(output.getvalue())
        self.assertEqual(help_text[0], help_text[2])
        self.assertNotEqual(help_text[0], help_text[1])

    def test_corrupt_or_foreign_runtime_projection_is_refused_without_rewriting_database(self):
        submit_local(self.path, 'Do work.', request_id='initial')
        path = self.root / 'state' / 'runtime-status.json'
        original = b'{"instance_id":"another","runtime_state":"running"}'
        path.write_bytes(original)
        with self.assertRaises(Exception):
            read_status(self.path)
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse((self.root / 'state' / 'runtime').exists())

    def test_real_cli_subprocess_drives_unified_models_and_publishes_once(self):
        import asyncio
        import os
        import subprocess
        import sys
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from tests.superagent_product_fixture import ProductTransport
        transport = ProductTransport()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                body = json.dumps({'data':[{'id':'fixture', 'context_length':65536, 'max_output_tokens':4096}]}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def do_POST(self):
                raw = self.rfile.read(int(self.headers['Content-Length']))
                async def collect():
                    return b''.join([chunk async for chunk in transport.post_stream(self.path, headers={}, body=raw, timeout=30)])
                body = asyncio.run(collect())
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        self.spec['base_url'] = 'http://127.0.0.1:' + str(server.server_port) + '/v1'
        self.path.write_text(json.dumps(self.spec))
        env = {**os.environ, 'HARNESS_API_KEY':'fixture-only'}
        command = [sys.executable, '-m', 'harness.cli', '--language', 'en', '--superagent', str(self.path),
                   '--superagent-submit', 'Fix the existing typo.', '--superagent-request-id', 'cli-original', '--superagent-once']
        try:
            for _ in range(2):
                result = subprocess.run(command, cwd=Path(__file__).resolve().parents[1], env=env,
                                        capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertTrue(json.loads(result.stdout)['queued'])
            self.assertEqual(len(transport.requests), 3)
            status = read_status(self.path)
            self.assertEqual(status['missions']['recent'][0]['state'], 'completed')
            self.assertEqual(status['runtime_state'], 'stopped')
            self.assertEqual(status['outbox'], {'sent':1})
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
