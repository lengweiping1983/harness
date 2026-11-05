"""语言资源生成只发布发生变化的文件，避免额外触发开发重载。"""
from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import generate_i18n


class I18nGenerationUpdateTests(unittest.TestCase):
    def test_unchanged_language_and_startup_files_keep_their_mtime(self):
        """普通中文文案变化只更新中文产物，不重写英文及启动资源。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources = root / 'harness' / 'i18n' / 'locales'
            sources.mkdir(parents=True)
            for language in ('zh-CN', 'en'):
                (sources / f'{language}.json').write_text(json.dumps({
                    'ui.App.f76a8d866552': language,
                    'ui.startup.failed': language,
                    'ui.deferred.reload': language,
                    'ui.example': language,
                }), encoding='utf-8')
            with patch.object(generate_i18n, 'ROOT', root), patch('sys.argv', ['generate_i18n.py']), redirect_stdout(io.StringIO()):
                self.assertEqual(generate_i18n.main(), 0)
                generated = [root / 'web' / 'src' / 'i18n' / 'locales' / f'{language}.json' for language in ('zh-CN', 'en')]
                generated.append(root / 'web' / 'src' / 'i18n' / 'startup.generated.json')
                for path in generated:
                    os.utime(path, ns=(1, 1))
                self.assertEqual(generate_i18n.main(), 0)
                self.assertTrue(all(path.stat().st_mtime_ns == 1 for path in generated))
                source = sources / 'zh-CN.json'
                values = json.loads(source.read_text(encoding='utf-8'))
                values['ui.example'] = '新的中文文案'
                source.write_text(json.dumps(values, ensure_ascii=False), encoding='utf-8')
                self.assertEqual(generate_i18n.main(), 0)
                self.assertEqual(json.loads(generated[0].read_text(encoding='utf-8'))['ui.example'], '新的中文文案')
                self.assertNotEqual(generated[0].stat().st_mtime_ns, 1)
                self.assertEqual(generated[1].stat().st_mtime_ns, 1)
                self.assertEqual(generated[2].stat().st_mtime_ns, 1)
