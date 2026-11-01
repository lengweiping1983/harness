"""执行器存储与图片拒绝路径双语化，原记录和网络边界保持。"""
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from harness.core.errors import HarnessError
from harness.core.language import current_language
from harness.execution.store import ExecutionStore
from harness.execution.images import inspect_image, download_image


class ExecutorStorageImageLanguageTests(unittest.TestCase):
    def test_storage_rejections_preserve_original_session_and_call_state(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ExecutionStore(Path(directory))
            try:
                session = store.open_session('owner', 'project', 'Original title 原文', '/OriginalRoot')
                sid = session['session_id']
                store.begin('owner', sid, 'write', 'accepted', 'original')
                for language in ('en', 'zh-CN', 'en'):
                    cases = [(lambda: store.session(sid, 'other'), 'execution/session-not-found'),
                        (lambda: store.checkpoint(sid, 'owner', 9, 'changed', []), 'execution/revision-conflict'),
                        (lambda: store.begin('owner', sid, 'write', '', 'original'), 'execution/request-id-required'),
                        (lambda: store.begin('owner', sid, 'write', 'accepted', 'different'), 'execution/request-conflict'),
                        (lambda: store.begin('owner', sid, 'write', 'accepted', 'original'), 'execution/outcome-unknown')]
                    with current_language.set(language):
                        for operation, code in cases:
                            with self.subTest(language=language, code=code), self.assertRaises(HarnessError) as caught:
                                operation()
                            self.assertEqual(caught.exception.code, code)
                            self.assertEqual(any('\u4e00' <= char <= '\u9fff' for char in caught.exception.message), language == 'zh-CN')
                        self.assertEqual(store.session(sid, 'owner'), session)
                store.finish('owner', sid, 'write', 'accepted', {'original': '原文'})
                self.assertEqual(store.begin('owner', sid, 'write', 'accepted', 'original'), {'original': '原文'})
            finally:
                store.close()

    def test_image_rejections_and_valid_metadata_keep_network_and_content_boundaries(self):
        content = io.BytesIO()
        Image.new('RGB', (2, 3)).save(content, format='PNG')
        data = content.getvalue()
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language), patch('harness.execution.images._PinnedHTTPS') as connection:
                for operation, code in [(lambda: download_image('http://example.invalid/private', 100), 'execution/image-url-denied'),
                                         (lambda: inspect_image(b'not an image', '.png'), 'execution/image-format'),
                                         (lambda: inspect_image(data, '.jpg'), 'execution/image-format'),
                                         (lambda: inspect_image(data, '.png', 'image/jpeg'), 'execution/image-format')]:
                    with self.subTest(language=language, code=code), self.assertRaises(HarnessError) as caught:
                        operation()
                    self.assertEqual(caught.exception.code, code)
                    self.assertEqual(any('\u4e00' <= char <= '\u9fff' for char in caught.exception.message), language == 'zh-CN')
                connection.assert_not_called()
                self.assertEqual(inspect_image(data, '.png', 'image/png'), {'mime_type': 'image/png', 'width': 2, 'height': 3})
