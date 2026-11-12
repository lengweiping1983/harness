"""本机OAuth拒绝文案遵循上下文，标准错误与存储边界保持。"""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from harness.core.language import current_language
from harness.mcp_server.auth import LocalOAuthProvider
from mcp.server.auth.provider import AuthorizeError, RegistrationError


class ExecutorOAuthLanguageTests(unittest.IsolatedAsyncioTestCase):
    async def test_registration_scope_and_resource_rejections_do_not_create_pending_authorizations(self):
        with tempfile.TemporaryDirectory() as directory:
            provider = LocalOAuthProvider(Path(directory), 'https://fixture.invalid')
            for language in ('en', 'zh-CN', 'en'):
                with current_language.set(language):
                    operations = [(provider.register_client(SimpleNamespace(redirect_uris=['https://other.invalid/callback'])), RegistrationError, 'invalid_redirect_uri'),
                        (provider.authorize(SimpleNamespace(scope='project.read'), SimpleNamespace(resource='https://other.invalid/mcp')), AuthorizeError, 'invalid_target'),
                        (provider.authorize(SimpleNamespace(scope='invalid'), SimpleNamespace(resource=None, scopes=['invalid'])), AuthorizeError, 'invalid_scope')]
                    for operation, kind, code in operations:
                        with self.assertRaises(kind) as caught:
                            await operation
                        self.assertEqual(caught.exception.error, code)
                        self.assertEqual(any('\u4e00' <= c <= '\u9fff' for c in caught.exception.error_description), language == 'zh-CN')
                    for operation in (lambda: provider.consent_redirect('missing'), lambda: provider.approve('missing')):
                        with self.assertRaises(ValueError) as caught:
                            operation()
                        self.assertEqual(any('\u4e00' <= c <= '\u9fff' for c in str(caught.exception)), language == 'zh-CN')
                self.assertEqual(provider.pending_requests(), [])
