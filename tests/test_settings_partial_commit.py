"""混合设置失败时明确报告已提交模型，不谎报整体保留或重放写入。"""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from harness.core.language import current_language
from harness.i18n import tr
from harness.llm.chatgpt_auth import ChatGPTAccount
from harness.web.api.settings import SettingsStore, build_router
from harness.web.errors import BadRequest, ServiceUnavailable
from harness.web.router import Request


class SettingsPartialCommitTests(unittest.TestCase):
    def test_all_invalid_fields_are_rejected_before_any_write(self):
        for language in ('en', 'zh-CN'):
            for body in ({'language': 'fr-FR'}, {'theme': 'neon'},
                         {'language': None}, {'language': ['en']},
                         {'language': 'fr-FR', 'theme': 'neon'}):
                with self.subTest(language=language, body=body), tempfile.TemporaryDirectory() as directory, current_language.set(language):
                    root = Path(directory)
                    account, settings, config, invoke = self.fixture(root)
                    original = (root / 'ui-settings.json').read_bytes()
                    with patch.object(settings, '_persist') as persist, patch.object(account, 'select') as select:
                        with self.assertRaises(BadRequest) as caught:
                            invoke('PATCH', body)
                        persist.assert_not_called()
                        select.assert_not_called()
                    self.assertEqual(caught.exception.status, 400)
                    self.assertEqual(caught.exception.code, 'settings/invalid-fields')
                    self.assertEqual(caught.exception.message, tr('python.settings.invalid_field'))
                    self.assertEqual(caught.exception.details, {'rejected': list(body)})
                    self.assertEqual(config.language, 'en')
                    self.assertEqual((root / 'ui-settings.json').read_bytes(), original)

    def test_mixed_invalid_language_keeps_explicit_partial_update_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            _, _, config, invoke = self.fixture(Path(directory))
            result = invoke('PATCH', {'language': 'fr-FR', 'theme': 'neon', 'reasoning_effort': 'high'})
            self.assertEqual(result['rejected'], ['language', 'theme'])
            self.assertEqual(result['settings']['reasoning_effort'], 'high')
            self.assertEqual(config.language, 'en')

    def fixture(self, root):
        account = ChatGPTAccount(root)
        account.credentials = {'access': 'fixture', 'refresh': '', 'account_id': 'fixture', 'email': '', 'expires': 9999999999}
        account.models = [{'id': f'chatgpt/{name}', 'label': name, 'provider': 'chatgpt', 'description': '',
                           'supports_reasoning': True, 'reasoning_efforts': []} for name in ('old', 'new')]
        account.selected = 'chatgpt/old'
        account.save()
        settings = SettingsStore(root)
        settings.update({'reasoning_effort': 'low'})
        config = SimpleNamespace(home=root, chatgpt_account=account, language='en', model='fixture', model_routes=[])
        ctx = SimpleNamespace(harness=SimpleNamespace(config=config), _ui_settings=settings)
        router = build_router(ctx)

        def invoke(method, body=None):
            handler, params = router.resolve(method, '/api/v1/ui-settings')
            return json.loads(handler(Request(method, '/api/v1/ui-settings', {}, {},
                json.dumps(body).encode() if body is not None else b''), **params).body)
        return account, settings, config, invoke

    def test_mixed_failure_reports_committed_selection_and_readback_matches_disk(self):
        for language in ('en', 'zh-CN'):
            with self.subTest(language=language), tempfile.TemporaryDirectory() as directory, current_language.set(language):
                root = Path(directory)
                account, settings, config, invoke = self.fixture(root)
                old_settings = (root / 'ui-settings.json').read_bytes()
                with patch.object(settings, '_persist', side_effect=OSError('private path')), patch.object(account, 'save', wraps=account.save) as save:
                    with self.assertRaises(ServiceUnavailable) as caught:
                        invoke('PATCH', {'default_model': 'chatgpt/new', 'reasoning_effort': 'high'})
                    self.assertEqual(save.call_count, 1)
                error = caught.exception
                self.assertEqual(error.code, 'settings/partially-applied')
                self.assertEqual(error.message, tr('python.settings.partially_applied'))
                self.assertNotIn('private', error.message)
                self.assertEqual(error.details, {'applied': {'default_model': 'chatgpt/new'}})
                self.assertEqual(ChatGPTAccount(root).selected, 'chatgpt/new')
                self.assertEqual((root / 'ui-settings.json').read_bytes(), old_settings)
                self.assertEqual(config.reasoning_effort, 'low')
                read = invoke('GET')['settings']
                self.assertEqual(read['default_model'], 'chatgpt/new')
                self.assertEqual(read['reasoning_effort'], 'low')

    def test_account_save_failure_preserves_both_files_and_settings_are_not_attempted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            account, settings, _, invoke = self.fixture(root)
            original = account.path.read_bytes()
            with patch.object(account, 'save', side_effect=OSError('fixture')), patch.object(settings, '_persist') as persist:
                with self.assertRaises(ServiceUnavailable) as caught:
                    invoke('PATCH', {'default_model': 'chatgpt/new', 'reasoning_effort': 'high'})
                persist.assert_not_called()
            self.assertEqual(caught.exception.code, 'settings/persist-failed')
            self.assertEqual(account.selected, 'chatgpt/old')
            self.assertEqual(account.path.read_bytes(), original)

    def test_repeated_selection_does_not_write_and_failed_other_settings_are_not_partial(self):
        with tempfile.TemporaryDirectory() as directory:
            account, settings, _, invoke = self.fixture(Path(directory))
            with patch.object(account, 'save') as save:
                self.assertEqual(invoke('PATCH', {'default_model': 'chatgpt/old'})['settings']['default_model'], 'chatgpt/old')
                with patch.object(settings, '_persist', side_effect=OSError('fixture')):
                    with self.assertRaises(ServiceUnavailable) as caught:
                        invoke('PATCH', {'default_model': 'chatgpt/old', 'reasoning_effort': 'high'})
                save.assert_not_called()
            self.assertEqual(caught.exception.code, 'settings/persist-failed')

    def test_successful_mixed_update_keeps_existing_rejected_field_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            account, settings, config, invoke = self.fixture(root)
            result = invoke('PATCH', {'default_model': 'chatgpt/new', 'reasoning_effort': 'high', 'unknown': 'value'})
            self.assertEqual(result['rejected'], ['unknown'])
            self.assertEqual(result['settings']['default_model'], 'chatgpt/new')
            self.assertEqual(config.reasoning_effort, 'high')
            self.assertEqual(settings.snapshot()['reasoning_effort'], 'high')
            self.assertEqual(ChatGPTAccount(root).selected, account.selected)
