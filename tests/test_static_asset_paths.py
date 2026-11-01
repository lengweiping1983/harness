"""静态资源每次请求重新核对路径，不沿用符号链接替换前的边界。"""
import os
from itertools import product
import tempfile
import unittest
from pathlib import Path
from harness.web.static_assets import StaticAssets
from tests.filesystem_links import directory_link


def resource_link(link: Path, target: Path) -> None:
    """POSIX 覆盖文件符号链接；Windows 以目录联接和首页解析验证同一 URL 边界。"""
    if os.name == 'nt':
        if target.name != 'index.html':
            raise AssertionError('目录联接资源夹具必须使用首页文件')
        directory_link(link, target.parent)
    else:
        link.symlink_to(target)


class StaticAssetPathTests(unittest.TestCase):
    def test_replaced_file_cannot_expose_outside_content(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / 'dist'
            root.mkdir()
            (root / 'index.html').write_text('fixture index')
            (base / 'outside').mkdir()
            outside = base / 'outside' / 'index.html'
            outside.write_text('outside fixture')
            target = root / 'file.txt'
            target.write_text('inside fixture')
            assets = StaticAssets(root)
            self.assertEqual(assets.serve('/file.txt').body, b'inside fixture')
            target.unlink()
            resource_link(target, outside)
            self.assertIsNone(assets.serve('/file.txt'))

    def test_directory_and_spa_indexes_cannot_resolve_outside_root(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / 'dist'
            root.mkdir()
            (base / 'outside').mkdir()
            outside = base / 'outside' / 'index.html'
            outside.write_text('outside fixture')
            nested = root / 'nested'
            nested.mkdir()
            resource_link(nested / 'index.html', outside)
            resource_link(root / 'index.html', outside)
            assets = StaticAssets(root)
            for path in ('/', '/index.html', '/nested/', '/missing-route'):
                with self.subTest(path=path):
                    self.assertIsNone(assets.serve(path))

    def test_current_in_root_links_and_spa_fallback_remain_available(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'index.html').write_text('fixture index')
            (root / 'source').mkdir()
            source = root / 'source' / 'index.html'
            source.write_text('fixture file', encoding='utf-8')
            resource_link(root / 'link.txt', source)
            assets = StaticAssets(root)
            self.assertEqual(assets.serve('/link.txt').body, b'fixture file')
            self.assertEqual(assets.serve('/conversation/fixture').body, b'fixture index')
            self.assertIsNone(assets.serve('/missing.js'))

    def test_replacement_between_stat_and_open_never_returns_outside_bytes(self):
        """打开时替换文件或父目录，句柄身份不符则拒绝且不缓存。"""
        from unittest.mock import patch
        from harness.web.errors import ServiceUnavailable
        for parent in (False, True):
            with self.subTest(parent=parent), tempfile.TemporaryDirectory() as directory:
                base = Path(directory).resolve()
                root = base/'dist'
                (root/'nested').mkdir(parents=True)
                outside = base/'outside'
                outside.mkdir()
                (outside/'index.html').write_bytes(b'Outside original')
                source = root/'nested'/'index.html'
                source.write_bytes(b'Inside original')
                assets = StaticAssets(root)
                original_open = os.open
                metadata_complete = False
                original_stat = assets._resource_stat
                def observed_stat(path):
                    nonlocal metadata_complete
                    info = original_stat(path)
                    metadata_complete = True
                    return info
                def swapped(filename, flags, *args, **kwargs):
                    if metadata_complete and Path(filename) == source:
                        if parent:
                            source.parent.rename(root/'original')
                            directory_link(root/'nested', outside)
                        else:
                            source.unlink()
                            resource_link(source, outside/'index.html')
                    return original_open(filename, flags, *args, **kwargs)
                with patch.object(assets, '_resource_stat', side_effect=observed_stat), patch('harness.web.static_assets.os.open', side_effect=swapped):
                    with self.assertRaises(ServiceUnavailable) as caught:
                        assets.serve('/nested/index.html')
                self.assertEqual(caught.exception.code, 'static/unreadable')
                self.assertEqual(len(assets._encoded), 0)

    def test_replacement_after_resolution_is_rejected_before_metadata_and_conditional_response(self):
        """解析后更换父目录不能让外部文件参与正文或304身份。"""
        from unittest.mock import patch
        for conditional, cached in product((False, True), repeat=2):
            with self.subTest(conditional=conditional, cached=cached), tempfile.TemporaryDirectory() as directory:
                base = Path(directory).resolve()
                root = base/'dist'
                (root/'nested').mkdir(parents=True)
                outside = base/'outside'
                outside.mkdir()
                (outside/'index.html').write_bytes(b'Outside fixture')
                (root/'nested'/'index.html').write_bytes(b'Inside fixture')
                assets = StaticAssets(root)
                etag = assets.serve('/nested/index.html').headers['ETag']
                if not cached:
                    assets._encoded.clear()
                resolve = assets.resolve
                def replaced(url):
                    result = resolve(url)
                    (root/'nested').rename(root/'original')
                    directory_link(root/'nested', outside)
                    return result
                with patch.object(assets, 'resolve', side_effect=replaced):
                    self.assertIsNone(assets.serve('/nested/index.html', if_none_match=etag if conditional else ''))
                self.assertEqual(len(assets._encoded), int(cached))
