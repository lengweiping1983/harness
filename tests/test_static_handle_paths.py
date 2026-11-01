"""Windows句柄路径范围判断，不依赖本机安装Windows。"""
import os
import unittest
from unittest.mock import patch
from pathlib import Path
import tempfile

from harness.core.file_handles import windows_path_within, windows_handle_path
from harness.web.static_assets import StaticAssets


class StaticHandlePathTests(unittest.TestCase):
    def test_windows_final_path_boundaries(self):
        for filename, root, expected in [
            (r'\\?\C:\dist\assets\a.js', r'c:\dist', True),
            (r'C:\dist-other\a.js', r'C:\dist', False),
            (r'D:\dist\a.js', r'C:\dist', False),
            (r'\\?\UNC\host\share\dist\a.js', r'\\host\share\dist', True),
            (r'\\host\other\dist\a.js', r'\\host\share\dist', False),
            (r'C:\dist\..\outside\a.js', r'C:\dist', False),
        ]:
            with self.subTest(filename=filename):
                self.assertEqual(windows_path_within(filename, root), expected)

    def test_metadata_rejects_outside_handle_before_conditional_response(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'index.html').write_bytes(b'Original')
            assets = StaticAssets(root)
            with patch('harness.web.static_assets._DIRECTORY_HANDLES', False), patch(
                'harness.web.static_assets.check_handle_root', side_effect=OSError('outside fixture')) as check:
                self.assertIsNone(assets.serve('/', if_none_match='any'))
            check.assert_called_once()
            self.assertEqual(len(assets._encoded), 0)

    @unittest.skipUnless(os.name == 'nt', '需要原生Windows句柄API')
    def test_native_handle_path_matches_opened_file(self):
        """真实系统调用返回已打开文件的根内路径。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / '中文资源.txt'
            source.write_bytes(b'fixture')
            with source.open('rb') as incoming:
                actual = windows_handle_path(incoming.fileno())
                self.assertTrue(windows_path_within(actual, str(root)))
                self.assertTrue(os.path.samefile(actual, source))
                self.assertFalse(windows_path_within(actual, str(root / 'other')))

    def test_path_recheck_after_read_rejects_changed_boundary_without_caching(self):
        """读取后句柄路径检查失败不能发布正文或写入压缩缓存。"""
        from harness.web.errors import ServiceUnavailable
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'index.html').write_bytes(b'fixture' * 1000)
            assets = StaticAssets(root)
            # 元数据、读取前检查成功，读取后路径查询失败。
            checks = [None, None, OSError('outside fixture')]
            with patch('harness.web.static_assets._DIRECTORY_HANDLES', False), patch(
                'harness.web.static_assets.check_handle_root', side_effect=checks) as check:
                with self.assertRaises(ServiceUnavailable) as caught:
                    assets.serve('/', accept_encoding='gzip')
            self.assertEqual(caught.exception.code, 'static/unreadable')
            self.assertEqual(check.call_count, 3)
            self.assertFalse(assets._encoded)
