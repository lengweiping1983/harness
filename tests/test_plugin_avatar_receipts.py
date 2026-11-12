"""头像有界读取及安装已落盘回执保持真实归属。"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from harness.core.language import current_language
from harness.i18n import tr
from harness.web.api.plugins import _read_avatar, build_router, MAX_AVATAR_BYTES
from harness.web.errors import ServiceUnavailable
from harness.web.router import Request


class PluginAvatarReceiptTests(unittest.TestCase):
    def test_avatar_replacement_with_same_size_and_time_changes_etag_and_body(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            path = root / 'avatar.svg'
            path.write_bytes(b'<svg>original</svg>')
            agent = SimpleNamespace(path=root/'AGENTS.md', metadata={'avatar': 'avatar.svg'}, name='fixture')
            harness = SimpleNamespace(config=SimpleNamespace(cwd=root), agents=SimpleNamespace(list_all=lambda: [agent]))
            handler, params = build_router(SimpleNamespace(harness=harness)).resolve('GET', '/api/v1/plugins/agents/fixture/avatar')
            request = Request('GET', '/api/v1/plugins/agents/fixture/avatar', {}, {}, b'')
            first = handler(request, **params)
            conditional = Request('GET', request.path, {}, {'If-None-Match': first.headers['ETag']}, b'')
            self.assertEqual(handler(conditional, **params).status, 304)
            stamp = path.stat()
            path.write_bytes(b'<svg>replaced</svg>')
            os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
            second = handler(conditional, **params)
            self.assertEqual(second.status, 200)
            self.assertNotEqual(first.headers['ETag'], second.headers['ETag'])
            self.assertEqual(second.body, b'<svg>replaced</svg>')

    def test_avatar_changed_during_bounded_read_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            target = root/'avatar.svg'
            target.write_bytes(b'<svg>original</svg>')
            fdopen = os.fdopen
            class ChangedRead:
                def __init__(self, descriptor, mode):
                    self.stream = fdopen(descriptor, mode)
                def __enter__(self):
                    return self
                def __exit__(self, *args):
                    self.stream.close()
                def fileno(self):
                    return self.stream.fileno()
                def read(self, limit):
                    if limit != MAX_AVATAR_BYTES+1:
                        raise AssertionError('avatar read must remain bounded')
                    raw = self.stream.read(limit)
                    target.write_bytes(b'<svg>changed</svg>')
                    return raw
            with patch('harness.web.api.plugins.os.fdopen', ChangedRead):
                with self.assertRaisesRegex(OSError, 'avatar file changed during read'):
                    _read_avatar(target, root)
            self.assertEqual(target.read_bytes(), b'<svg>changed</svg>')

    def test_oversized_avatar_is_rejected_before_reading_and_original_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            target = root/'huge.png'
            with target.open('wb') as stream:
                stream.truncate(MAX_AVATAR_BYTES+1)
            with self.assertRaisesRegex(OSError, 'avatar file exceeds limit or is not regular'):
                _read_avatar(target, root)
            self.assertEqual(target.stat().st_size, MAX_AVATAR_BYTES+1)
            target.write_bytes(b'Original bytes')
            self.assertEqual(_read_avatar(target, root), b'Original bytes')

    @unittest.skipUnless(os.open in os.supports_dir_fd, 'requires directory handles')
    def test_parent_replaced_by_external_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)/'root';root.mkdir()
            parent = root/'avatars';parent.mkdir()
            target = parent/'avatar.svg';target.write_bytes(b'<svg>original</svg>')
            outside = Path(directory)/'outside';outside.mkdir()
            (outside/'avatar.svg').write_bytes(b'<svg>private</svg>')
            parent.rename(root/'original')
            parent.symlink_to(outside, target_is_directory=True)
            with self.assertRaises(OSError):
                _read_avatar(target, root)
            self.assertEqual((root/'original'/'avatar.svg').read_bytes(), b'<svg>original</svg>')

    def test_installed_path_and_original_refresh_error_are_preserved_for_both_routes(self):
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                for kind in ('skills', 'agents'):
                    harness = Mock()
                    harness.config.cwd = '/original';harness.config.home = '/fixture-home'
                    error = OSError('Original refresh error')
                    getattr(harness, 'refresh_'+kind).side_effect = error
                    path = '/api/v1/plugins/'+kind+'/install'
                    handler, params = build_router(SimpleNamespace(harness=harness)).resolve('POST', path)
                    request = Request('POST', path, {}, {}, json.dumps({'path':'/source'}).encode())
                    with patch('harness.web.api.plugins.install_from_directory', return_value=Path('/installed/fixture')) as install:
                        with self.assertRaises(ServiceUnavailable) as caught:
                            handler(request, **params)
                    self.assertEqual(caught.exception.message, tr('plugin.install.refreshFailed'))
                    self.assertEqual(caught.exception.details, {'installed': True, 'path': str(Path('/installed/fixture'))})
                    self.assertIs(caught.exception.__cause__, error)
                    install.assert_called_once()
