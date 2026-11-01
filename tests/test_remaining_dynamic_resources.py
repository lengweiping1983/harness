"""只读比较错误与图片摘要使用实际分支解析资源，保留原始数据。"""
import base64
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

from harness.app.comparison import ComparisonService
from harness.core.errors import HarnessError
from harness.core.language import current_language
from harness.i18n import tr
from harness.tools.builtin.image_tools import build_image_tools
from harness.tools.registry import ToolRunContext


class RemainingDynamicResourceTests(unittest.IsolatedAsyncioTestCase):
    def test_comparison_failure_producers_resolve_all_six_resources(self):
        service = ComparisonService()
        with tempfile.TemporaryDirectory() as folder:
            parent = Path(folder).resolve()
            root = parent / 'repository'
            root.mkdir()
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            original = 'Original 用户 {value}\n'.encode('utf-8')
            path = root / '原文.txt'
            path.write_bytes(original)
            cases = [
                ('invalid', 'review/invalid', lambda: service.resolve(root, '-invalid')),
                ('unavailable', 'review/unavailable', lambda: service.git(root, 'not-a-git-command')),
                ('no_git', 'review/unavailable', lambda: service.root(parent)),
                ('ref', 'review/invalid-ref', lambda: service.resolve(root, 'missing-ref')),
                ('outside', 'review/outside-workspace', lambda: service._content(root, parent / 'outside.txt')),
                ('changed', 'review/changed', lambda: service.compare(root, revision='stale')),
            ]
            for language in ('en', 'zh-CN', 'en'):
                with current_language.set(language):
                    for suffix, code, invoke in cases:
                        with self.subTest(language=language, suffix=suffix):
                            with self.assertRaises(HarnessError) as failure:
                                invoke()
                            self.assertEqual(failure.exception.code, code)
                            self.assertEqual(failure.exception.message, tr('comparison.' + suffix))
            self.assertEqual(path.read_bytes(), original)

    async def test_same_image_tool_formats_valid_dimensions_and_fallback_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            tool = next(tool for tool in build_image_tools(str(root)) if tool.schema.name == 'read_image')
            ctx = ToolRunContext('image-language', 'fixture', str(root), 1, 1)
            # 头部夹具用于校验尺寸探测与原始字节，不代表完整图片解码。
            raw = b'\x89PNG\r\n\x1a\n' + b'\x00\x00\x00\rIHDR' + struct.pack('>II', 2, 3)
            path = root / '原文{width}.png'
            path.write_bytes(raw)
            args = {'path': str(path)}
            result = await tool.execute(args, ctx)
            self.assertEqual((result['width'], result['height']), (2, 3))
            # 正式读取拒绝缺失尺寸；格式器仍保留无尺寸元数据的兜底分支。
            fallback = {**result, 'width': None, 'height': None}
            for value, suffix in ((result, 'dimensions'), (fallback, 'basic')):
                for language in ('en', 'zh-CN', 'en'):
                    with current_language.set(language):
                        blocks = tool.to_content(args, value)
                        self.assertEqual(blocks[0].text, tr('image.summary.' + suffix,
                            path=value['path'], mime=value['mime_type'], size=len(raw),
                            width=value.get('width'), height=value.get('height')))
                        self.assertEqual(blocks[1].text, value['data_url'])
                        self.assertEqual(base64.b64decode(blocks[1].text.split(',', 1)[1]), raw)
            self.assertEqual(path.read_bytes(), raw)
