"""CLI 运行错误使用配置语言，退出恢复外层上下文。"""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from harness.core.language import current_language
from harness.mcp_server.cli import main
from harness.i18n import resources


class ExecutorCliLanguageTests(unittest.TestCase):
    def test_companion_uses_dedicated_loopback_port_and_configured_language(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'config.json'
            path.write_text(json.dumps({'home': 'state', 'projects': {'fixture': {'root': '.'}}, 'language': 'en'}), encoding='utf-8')
            with current_language.set('zh-CN'), patch('harness.mcp_server.companion.serve') as serve:
                main(['companion', '--config', str(path), '--companion-port', '8899'])
                self.assertEqual(serve.call_args.args[1:], (8899, 'local'))
                self.assertEqual(current_language.get(), 'zh-CN')
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                main(['companion', '--config', str(path), '--public-url', 'https://fixture.invalid'])
            self.assertFalse((root / 'state').exists())

    def test_missing_authorization_arguments_follow_config_without_changing_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'project').mkdir()
            path = root / 'config.json'
            for language in ('en', 'zh-CN', 'en'):
                path.write_text(json.dumps({'home': 'state', 'projects': {'fixture': {'root': 'project'}}, 'language': language}))
                original = path.read_bytes()
                output = io.StringIO()
                with current_language.set('zh-CN'), contextlib.redirect_stderr(output), self.assertRaises(SystemExit) as caught:
                    main(['approvals', '--config', str(path)])
                self.assertEqual(caught.exception.code, 2)
                self.assertIn(resources(language)['execution.cli.same_url'], output.getvalue())
                self.assertEqual(current_language.get(), 'zh-CN')
                self.assertEqual(path.read_bytes(), original)
                self.assertFalse((root / 'state').exists())
