"""语言检查必须核对浏览器子集，覆盖条件分支和历史语言识别入口。"""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from tests.filesystem_links import directory_link

ROOT = Path(__file__).resolve().parents[1]


class BrowserCatalogValidationTests(unittest.TestCase):
    def test_backend_only_keys_are_rejected_in_browser_calls(self):
        node = shutil.which('node')
        self.assertIsNotNone(node, '语言验证需要 Node.js')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'scripts').mkdir()
            shutil.copyfile(ROOT / 'scripts/check_i18n.mjs', root / 'scripts/check_i18n.mjs')
            (root / 'web/src').mkdir(parents=True)
            directory_link(root / 'web/node_modules', ROOT / 'web/node_modules')
            for language in ('en', 'zh-CN'):
                for relative, data in [('harness/i18n/locales', {'ui.available': 'Available', 'backend.only': 'Backend'}),
                                       ('web/src/i18n/locales', {'ui.available': 'Available'})]:
                    path = root / relative / (language + '.json')
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps(data), encoding='utf-8')
            fixture = root / 'web/src/fixture.ts'
            for code in ("t('backend.only')", "t(flag ? 'ui.available' : 'backend.only')"):
                fixture.write_text(code, encoding='utf-8')
                result = subprocess.run([node, str(root / 'scripts/check_i18n.mjs')], capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('Unknown browser translation key backend.only', result.stderr)
            fixture.write_text("t(flag ? 'ui.available' : `ui.available`)", encoding='utf-8')
            result = subprocess.run([node, str(root / 'scripts/check_i18n.mjs')], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
