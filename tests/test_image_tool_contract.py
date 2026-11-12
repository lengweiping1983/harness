"""图片工具当前参数契约和完整双语摘要。"""
import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from harness.core.errors import ToolExecutionError
from harness.core.language import current_language
from harness.i18n import tr
from harness.tools.builtin.image_tools import build_image_tools
from harness.tools.registry import ToolRunContext


class ImageToolContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_current_path_contract_and_shared_batch_summary(self):
        png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jfa8AAAAASUVORK5CYII=')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / 'Original-{path}-原文.png'
            path.write_bytes(png)
            ctx = ToolRunContext(call_id='image', session_id='fixture', cwd=directory, turn=1, step=1)
            single, batch = build_image_tools(directory)
            remote = {'path': 'https://example.test/Original-{path}.png', 'mime_type': 'image/png',
                      'bytes': len(png), 'width': 1, 'height': 1, 'data_url': 'data:image/png;base64,Original'}
            fetch = AsyncMock(return_value=remote)
            with patch('harness.tools.builtin.image_tools.read_image_url', fetch):
                for language in ('en', 'zh-CN', 'en'):
                    with current_language.set(language):
                        for args in (None, [], {}, {'url': remote['path']}, {'path': None},
                                     {'path': 1}, {'path': True}, {'path': []}, {'path': '  '}):
                            with self.subTest(language=language, args=args):
                                with self.assertRaises(ToolExecutionError) as error:
                                    await single.execute(args, ctx)
                                self.assertEqual(error.exception.message, tr('python.image_tools.7af9a07106bf'))
                        value = await single.execute({'path': str(path)}, ctx)
                        self.assertEqual(base64.b64decode(value['data_url'].split(',', 1)[1]), png)
                        blocks = single.to_content({}, value)
                        expected = f'Image: {path} (image/png, {len(png)} bytes, dimensions 1x1)' if language == 'en' else f'图片：{path}（image/png，{len(png)} 字节，尺寸 1x1）'
                        self.assertEqual(blocks[0].text, expected)
                        self.assertEqual(blocks[1].text, value['data_url'])
                        fetched = await single.execute({'path': remote['path']}, ctx)
                        self.assertEqual(fetched, remote)
                        grouped = await batch.execute({'paths': [str(path), remote['path'], str(path)]}, ctx)
                        self.assertFalse(grouped['error'])
                        self.assertEqual(len(grouped['images']), 2)
                        self.assertEqual(batch.to_content({}, grouped)[1].text, expected)
                        basic = {**value, 'width': None, 'height': None}
                        self.assertEqual(single.to_content({}, basic)[0].text,
                                         tr('image.summary.basic', path=str(path), mime='image/png', size=len(png)))
            self.assertEqual(fetch.await_count, 6)
            self.assertEqual(path.read_bytes(), png)
            self.assertEqual(list(Path(directory).resolve().iterdir()), [path])
