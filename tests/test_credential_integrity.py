"""凭据损坏时保留原件，并保证同一存储的并发写入不丢记录。"""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from harness.connectors.credentials import CredentialStore, OAUTH_FILENAME
from harness.core.errors import ConnectorError
from harness.core.language import current_language


class CredentialIntegrityTests(unittest.TestCase):
    def test_corrupt_store_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / OAUTH_FILENAME
            for raw in ('{bad', '[]', '{"fixture": 42}'):
                for language in ('zh-CN', 'en'):
                    path.write_text(raw, encoding='utf-8')
                    store = CredentialStore(directory, backend='file')
                    with current_language.set(language):
                        for operation in (lambda: store.get('fixture'), lambda: store.set('new', {}), lambda: store.delete('fixture')):
                            with self.assertRaises(ConnectorError):
                                operation()
                            self.assertEqual(path.read_text(encoding='utf-8'), raw)

    def test_invalid_credential_payload_is_not_missing(self):
        store = CredentialStore(backend='memory')
        store._memory['fixture'] = '[]'
        with self.assertRaises(ConnectorError):
            store.get('fixture')

    def test_concurrent_writes_preserve_every_record(self):
        with tempfile.TemporaryDirectory() as directory:
            store = CredentialStore(directory, backend='file')
            barrier = threading.Barrier(8)
            def write(index):
                barrier.wait(timeout=5)
                store.set(str(index), {'fixture': index})
            with ThreadPoolExecutor(max_workers=8) as executor:
                list(executor.map(write, range(8)))
            self.assertEqual(len(json.loads((Path(directory) / OAUTH_FILENAME).read_text())), 8)
            for index in range(8):
                self.assertEqual(store.get(str(index)), {'fixture': index})

    def test_unknown_backend_does_not_silently_use_memory(self):
        with tempfile.TemporaryDirectory() as directory:
            for language in ('en', 'zh-CN'):
                for backend in ('fil', '', 'legacy', 1, False):
                    with self.subTest(language=language, backend=backend), current_language.set(language):
                        with self.assertRaises(ConnectorError) as error:
                            CredentialStore(directory, backend=backend)
                        self.assertEqual(any('\u4e00' <= c <= '\u9fff' for c in error.exception.message), language == 'zh-CN')
                        self.assertFalse((Path(directory) / OAUTH_FILENAME).exists())

    def test_rejected_payload_preserves_existing_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            store = CredentialStore(directory, backend='file')
            store.set('fixture', {'access_token': 'fixture-secret'})
            path = Path(directory) / OAUTH_FILENAME
            original = path.read_bytes()
            for payload in ([], None, 'invalid', 1):
                with self.subTest(payload=payload), self.assertRaises(ConnectorError):
                    store.set('fixture', payload)
                self.assertEqual(path.read_bytes(), original)
                self.assertEqual(store.get('fixture'), {'access_token': 'fixture-secret'})

    def test_preset_storage_guidance_matches_unencrypted_file_backend(self):
        from harness.connectors.presets import get_preset

        with tempfile.TemporaryDirectory() as directory:
            store = CredentialStore(directory, backend='file')
            store.set('fixture', {'access_token': 'fixture-secret'})
            path = Path(directory) / OAUTH_FILENAME
            saved = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(json.loads(saved['fixture'])['access_token'], 'fixture-secret')
            self.assertEqual(store.backend_name, 'file')
            for language in ('en', 'zh-CN', 'en'):
                with current_language.set(language):
                    preset = get_preset('github')
                    for text in (preset.summary()['auth_hint'], preset.skill_markdown):
                        self.assertIn(OAUTH_FILENAME, text)
                        self.assertIn('0600', text)
                        self.assertIn('未加密' if language == 'zh-CN' else 'unencrypted', text)
