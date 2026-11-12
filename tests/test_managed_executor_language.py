"""托管执行器配置与启动语言传播，不启动外部进程。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
from harness.app.mcp_service import McpService
from harness.core.language import current_language
from harness.execution.config import ExecutionConfig
from harness.mcp_server import managed
from harness.i18n import resources


class ManagedExecutorLanguageTests(unittest.TestCase):
    def test_worker_writes_effective_language_to_child_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / 'project'
            project.mkdir()
            service = McpService(root / 'private')
            try:
                for language in ('en', 'zh-CN', 'en'):
                    with current_language.set(language), patch.object(service, '_python', return_value='fixture-python'), patch.object(service, '_cloudflared', return_value='fixture-tunnel'), patch.object(service, '_spawn', side_effect=RuntimeError('fixture stop before external process')):
                        service.start(project)
                        service._thread.join(timeout=3)
                        self.assertFalse(service._thread.is_alive())
                    config = ExecutionConfig.load(service.home / 'config.json')
                    self.assertEqual(config.language, language)
                    self.assertEqual(config.projects['project'].root, project.resolve())
                    self.assertEqual(service.snapshot()['status'], 'failed')
            finally:
                service.stop()

    def test_managed_entry_binds_config_and_restores_context(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'config.json'
            for language in ('en', 'zh-CN', 'en'):
                path.write_text(json.dumps({'home': 'state', 'projects': {'fixture': {'root': str(root)}}, 'language': language}))
                seen = []
                with current_language.set('zh-CN'), patch('sys.argv', ['managed', '--config', str(path), '--public-url', 'https://fixture.invalid', '--port', '10001', '--local-port', '10002']), patch.object(managed, '_run', side_effect=lambda *_: seen.append(current_language.get())):
                    managed.main()
                    self.assertEqual(current_language.get(), 'zh-CN')
                self.assertEqual(seen, [language])

    def test_missing_password_does_not_regenerate_existing_login(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Mock(home=Path(directory) / 'state')
            for language in ('en', 'zh-CN'):
                provider = Mock(password_enabled=True)
                with current_language.set(language), patch('harness.mcp_server.auth.LocalOAuthProvider', return_value=provider), self.assertRaises(RuntimeError) as caught:
                    managed._run(config, Mock(public_url='https://fixture.invalid'))
                self.assertEqual(str(caught.exception), resources(language)['execution.managed.password_missing'])
                provider.initialize_login.assert_not_called()
                self.assertFalse((Path(directory) / 'password.json').exists())
