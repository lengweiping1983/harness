"""模型目录和创建/图片错误随调用语言切换，不修改用户原文。"""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlsplit

from harness.core.errors import ToolExecutionError
from harness.core.language import current_language
from harness.i18n import tr
from harness.plugins.authoring import create_plugin, main, plugin_source
from harness.plugins.packages import PackageError
from harness.tools.builtin.image_tools import MIME_BY_SUFFIX, read_image_file, read_image_url
from harness.web.models import model_catalog


class CatalogAuthoringImageLanguageTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_imported_catalog_and_errors_follow_runtime_language(self):
        config = SimpleNamespace(model='custom/Original-model', model_routes=[])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / '用户原文.bad'
            path.write_bytes(b'original')
            for language in ('en', 'zh-CN', 'en'):
                with current_language.set(language):
                    payload = model_catalog(config)
                    self.assertEqual(payload['active'], '')
                    self.assertEqual(payload['models'], [])
                    self.assertEqual(payload['providers'], [])
                    expected = tr('python.image_tools.e02a74d6198a', p0='.bad', p1=tr('ui.common.list_separator').join(sorted(MIME_BY_SUFFIX)))
                    with self.assertRaises(ToolExecutionError) as failure:
                        read_image_file(path)
                    self.assertEqual(failure.exception.message, expected)
                    with patch('harness.tools.builtin.image_tools._check_url', return_value=urlsplit('https://example.test/用户.bad')):
                        with self.assertRaises(ToolExecutionError) as failure:
                            await read_image_url('https://example.test/用户.bad')
                    self.assertEqual(failure.exception.message, expected)
                    with self.assertRaises(PackageError) as failure:
                        plugin_source(folder, '!!!')
                    self.assertEqual(failure.exception.message, tr('plugin.authoring.name'))
                    with self.assertRaises(PackageError) as failure:
                        create_plugin(folder, 'fixture', ' ')
                    self.assertEqual(failure.exception.message, tr('plugin.authoring.description_required'))
                    output = io.StringIO()
                    with patch('sys.argv', ['authoring', '--help']), contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as exit:
                        main()
                    self.assertEqual(exit.exception.code, 0)
                    self.assertIn(tr('plugin.authoring.cli_description'), output.getvalue())
            self.assertEqual(path.read_bytes(), b'original')
            self.assertEqual([entry.name for entry in Path(folder).iterdir()], [path.name])
