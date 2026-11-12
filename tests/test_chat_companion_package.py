"""交付 ZIP 的根清单、离线说明、资源字节及摘要须与实际扩展一致。"""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from scripts.package_chat_companion import ASSETS, GUIDE, package


class ChatCompanionPackageTests(unittest.TestCase):
    def test_package_root_can_be_loaded_and_contains_no_runtime_files(self):
        source = Path(__file__).resolve().parent.parent / 'extensions' / 'chatgpt-companion'
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'companion.zip'
            result = package(source, target)
            first = target.read_bytes()
            package(source, target)
            self.assertEqual(target.read_bytes(), first)
            self.assertEqual(result['sha256'], hashlib.sha256(first).hexdigest())
            with zipfile.ZipFile(target) as bundle:
                self.assertEqual(set(bundle.namelist()), set(ASSETS) | {'INSTALL.html', 'README.txt', 'SHA256.json'})
                manifest = json.loads(bundle.read('manifest.json'))
                self.assertEqual(manifest['manifest_version'], 3)
                self.assertEqual(manifest['action']['default_popup'], 'popup.html')
                hashes = json.loads(bundle.read('SHA256.json'))
                for name, digest in hashes.items():
                    self.assertEqual(hashlib.sha256(bundle.read(name)).hexdigest(), digest)
                for name in ASSETS:
                    self.assertEqual(bundle.read(name), (source / name).read_bytes())
                self.assertIn(b"from './secret-input.mjs'", bundle.read('popup.mjs'))
                self.assertIn('secret-input.mjs', hashes)
                for language in ('zh_CN', 'en'):
                    messages = json.loads(bundle.read(f'_locales/{language}/messages.json'))
                    for key in GUIDE:
                        self.assertIn(messages[key]['message'], bundle.read('README.txt').decode('utf-8'))
                self.assertIn('manifest.json', bundle.read('INSTALL.html').decode('utf-8'))
                self.assertNotIn('<script', bundle.read('INSTALL.html').decode('utf-8'))
