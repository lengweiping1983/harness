"""授权页语言隔离、表单协议和内容转义回归。"""
import tempfile
import unittest
from pathlib import Path
import httpx
from harness.core.language import current_language
from harness.execution.config import ExecutionConfig, ProjectConfig
from harness.mcp_server.consent import page, headers
from harness.mcp_server.server import create_http_app


class ConsentLanguageTests(unittest.IsolatedAsyncioTestCase):
    def test_form_language_and_untrusted_values(self):
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                text = page('ticket', 'nonce', {'redirect_uri': 'https://client.example/<script>',
                    'scopes': ['project.read', 'project.write', 'command.run', 'git.commit', '<script>']}, error='<script>')
                self.assertIn(f'<html lang="{language}">', text)
                self.assertEqual(any('\u4e00' <= c <= '\u9fff' for c in text), language == 'zh-CN')
                self.assertNotIn('<script>', text)
                for field in ('name="password"', 'name="csrf"', 'method="post"', 'maxlength="256"'):
                    self.assertIn(field, text)
                self.assertNotIn('<form', page())
                self.assertNotEqual(page(nonce='n', all_projects=True), page(nonce='n', all_projects=False))
        self.assertIn("default-src 'none'", headers()['Content-Security-Policy'])
        self.assertEqual(headers()['Cache-Control'], 'no-store')

    async def test_http_errors_use_service_language_and_restore_caller(self):
        with tempfile.TemporaryDirectory() as directory:
            for language in ('en', 'zh-CN', 'en'):
                config = ExecutionConfig(home=Path(directory) / language, projects={"fixture": ProjectConfig(Path(directory))}, language=language)
                app = create_http_app(config, public_url='https://executor.example', manage_runtime=False)
                with current_language.set('zh-CN' if language == 'en' else 'en'):
                    before = current_language.get()
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://executor.example') as client:
                        result = await client.get('/consent?ticket=missing')
                    self.assertEqual(current_language.get(), before)
                self.assertEqual(result.status_code, 400)
                self.assertIn(f'<html lang="{language}">', result.text)
                self.assertEqual(any('\u4e00' <= c <= '\u9fff' for c in result.text), language == 'zh-CN')
                self.assertEqual(result.headers['cache-control'], 'no-store')
