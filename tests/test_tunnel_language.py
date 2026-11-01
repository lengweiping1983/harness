"""隧道准备的本机失败分支双语化，不进行下载或缓存写入。"""
import tempfile
import hashlib
import io
import tarfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from harness.app.cloudflared import _check_cancel, _download_official, _install_darwin, _install_windows, resolve_cloudflared
from harness.app.mcp_errors import McpServiceError
from harness.core.language import current_language


class TunnelLanguageTests(unittest.TestCase):
    def test_cancel_and_unsupported_architecture_do_not_download(self):
        with tempfile.TemporaryDirectory() as directory, patch('harness.app.cloudflared._download') as download:
            for language in ('en', 'zh-CN'):
                with current_language.set(language):
                    cancelled = threading.Event()
                    cancelled.set()
                    for operation in (lambda: _check_cancel(cancelled),
                                      lambda: _install_darwin(directory, threading.Event(), 'unsupported-fixture')):
                        with self.assertRaises(RuntimeError) as caught:
                            operation()
                        self.assertEqual(any('\u4e00' <= char <= '\u9fff' for char in str(caught.exception)), language == 'zh-CN')
            download.assert_not_called()
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_windows_automatically_prepares_private_binary(self):
        for language in ('en', 'zh-CN', 'en'):
            cancel = threading.Event()
            with current_language.set(language), patch('harness.app.cloudflared.sys.platform', 'win32'), patch('harness.app.cloudflared.shutil.which', return_value=None), patch('harness.app.cloudflared.platform.machine', return_value='AMD64'), patch('harness.app.cloudflared._install_windows', return_value='private-binary') as install:
                self.assertEqual(resolve_cloudflared('isolated-fixture', cancel), 'private-binary')
                install.assert_called_once_with('isolated-fixture', cancel, 'AMD64')

    def test_windows_keeps_explicit_path_discovery(self):
        executable = r'C:\Tools\cloudflared.exe'
        with patch('harness.app.cloudflared.sys.platform', 'win32'), patch('harness.app.cloudflared.shutil.which', return_value=executable) as which, patch('harness.app.cloudflared.Path') as path:
            self.assertEqual(resolve_cloudflared('isolated-fixture', threading.Event()), executable)
            which.assert_called_once_with('cloudflared')
            path.assert_not_called()


class TunnelDownloadRecoveryTests(unittest.TestCase):
    def test_transient_failure_retries_without_system_transfer(self):
        with patch('harness.app.cloudflared._download', side_effect=[OSError('offline'), b'archive']) as download, patch('harness.app.cloudflared.subprocess.Popen') as spawn:
            self.assertEqual(_download_official('https://example.test/release', threading.Event()), b'archive')
            self.assertEqual(download.call_count, 2)
            spawn.assert_not_called()

    def test_system_transfer_is_verified_and_installed_in_private_cache(self):
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode='w:gz') as package:
            entry = tarfile.TarInfo('cloudflared')
            entry.size = 7
            package.addfile(entry, io.BytesIO(b'fixture'))
        payload = archive.getvalue()

        def spawn(argv, **options):
            options['stdout'].write(payload)
            from unittest.mock import Mock
            return Mock(poll=lambda: 0, returncode=0)

        with tempfile.TemporaryDirectory() as directory, patch('harness.app.cloudflared._download', side_effect=OSError('TLS failed')), patch('harness.app.cloudflared.shutil.which', return_value='/usr/bin/curl'), patch('harness.app.cloudflared.subprocess.Popen', side_effect=spawn), patch.dict('harness.app.cloudflared.DARWIN_SHA256', {'arm64': hashlib.sha256(payload).hexdigest()}):
            binary = Path(_install_darwin(directory, threading.Event(), 'arm64'))
            self.assertEqual(binary.read_bytes(), b'fixture')
            self.assertEqual(binary.parent.parent, Path(directory) / 'tools')
            with patch('harness.app.cloudflared._download') as download:
                self.assertEqual(_install_darwin(directory, threading.Event(), 'arm64'), str(binary))
                download.assert_not_called()

    def test_cancellation_does_not_retry_or_start_system_transfer(self):
        cancel = threading.Event()

        def download(*args):
            cancel.set()
            raise OSError('interrupted')

        with patch('harness.app.cloudflared._download', side_effect=download) as fetch, patch('harness.app.cloudflared.subprocess.Popen') as spawn:
            with self.assertRaises(McpServiceError) as caught:
                _download_official('https://example.test/release', cancel)
            self.assertEqual(caught.exception.key, 'ui.mcpService.error.cancelled')
            self.assertEqual(fetch.call_count, 1)
            spawn.assert_not_called()


class WindowsTunnelInstallTests(unittest.TestCase):
    def test_verified_install_reuse_and_corrupt_cache_recovery(self):
        payload = b'official-fixture'
        for machine, arch in (('AMD64', 'amd64'), ('x86', '386'), ('ARM64', 'amd64')):
            with self.subTest(machine=machine), tempfile.TemporaryDirectory() as directory, patch.dict('harness.app.cloudflared.WINDOWS_SHA256', {arch: hashlib.sha256(payload).hexdigest()}), patch('harness.app.cloudflared._download_official', return_value=payload) as download:
                binary = Path(_install_windows(directory, threading.Event(), machine))
                self.assertEqual(binary.read_bytes(), payload)
                self.assertIn(f'windows-{arch}', str(binary))
                self.assertEqual(_install_windows(directory, threading.Event(), machine), str(binary))
                self.assertEqual(download.call_count, 1)
                binary.write_bytes(b'corrupt')
                _install_windows(directory, threading.Event(), machine)
                self.assertEqual(binary.read_bytes(), payload)
                self.assertEqual(download.call_count, 2)
                self.assertEqual(list(binary.parent.glob('*.partial')), [])

    def test_invalid_download_and_cancel_preserve_existing_file(self):
        for mode in ('invalid', 'cancel'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                from harness.app.cloudflared import VERSION
                binary = Path(directory) / 'tools' / f'cloudflared-{VERSION}-windows-amd64' / 'cloudflared.exe'
                binary.parent.mkdir(parents=True)
                binary.write_bytes(b'original')
                cancel = threading.Event()

                def download(*args):
                    if mode == 'cancel':
                        cancel.set()
                    return b'fixture'

                with patch('harness.app.cloudflared._download_official', side_effect=download), patch.dict('harness.app.cloudflared.WINDOWS_SHA256', {'amd64': hashlib.sha256(b'fixture').hexdigest() if mode == 'cancel' else 'invalid'}):
                    with self.assertRaises(McpServiceError) as caught:
                        _install_windows(directory, cancel, 'AMD64')
                self.assertEqual(caught.exception.key, 'ui.mcpService.error.cancelled' if mode == 'cancel' else 'ui.mcpService.error.verificationFailed')
                self.assertEqual(binary.read_bytes(), b'original')
                self.assertEqual(list(binary.parent.glob('*.partial')), [])

    def test_unsupported_architecture_does_not_download(self):
        with patch('harness.app.cloudflared._download_official') as download:
            with self.assertRaises(McpServiceError):
                _install_windows('unused-fixture', threading.Event(), 'unsupported')
            download.assert_not_called()


class TunnelSourceFallbackTests(unittest.TestCase):
    def test_mirror_preferred_and_official_api_fallback_verified(self):
        from harness.app.cloudflared import VERSION, RELEASE_ASSETS
        name = 'cloudflared-windows-amd64.exe'
        upstream = f'https://github.com/cloudflare/cloudflared/releases/download/{VERSION}/{name}'
        mirror = f'https://download.nexusdock.co/components/cloudflared/{VERSION}/{name}'
        api = f'https://api.github.com/repos/cloudflare/cloudflared/releases/assets/{RELEASE_ASSETS[name]}'
        payload = b'verified-fixture'
        for replies, expected in (([payload], [mirror]), ([OSError('mirror offline'), payload], [mirror, upstream]), ([b'corrupted', OSError('upstream offline'), payload], [mirror, upstream, api])):
            with self.subTest(expected=expected), patch.dict('harness.app.cloudflared.WINDOWS_SHA256', {'amd64': hashlib.sha256(payload).hexdigest()}), patch('harness.app.cloudflared._download_transport', side_effect=replies) as fetch:
                self.assertEqual(_download_official(upstream, threading.Event()), payload)
                self.assertEqual([call.args[0] for call in fetch.call_args_list], expected)

    def test_all_failed_sources_keep_bilingual_diagnostics(self):
        from harness.app.cloudflared import VERSION
        upstream = f'https://github.com/cloudflare/cloudflared/releases/download/{VERSION}/cloudflared-windows-amd64.exe'
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language), patch('harness.app.cloudflared._download_transport', side_effect=OSError('fixture TLS failure')) as fetch:
                with self.assertRaises(McpServiceError) as caught:
                    _download_official(upstream, threading.Event())
                self.assertEqual(fetch.call_count, 3)
                self.assertEqual(caught.exception.key, 'ui.mcpService.error.downloadFailedDetails')
                self.assertIn('fixture TLS failure', str(caught.exception))
                self.assertEqual(any('\u4e00' <= char <= '\u9fff' for char in str(caught.exception)), language == 'zh-CN')

    def test_cancelled_source_does_not_start_fallback(self):
        from harness.app.cloudflared import VERSION
        cancel = threading.Event()
        def fetch(*args):
            cancel.set()
            return b'partial'
        with patch('harness.app.cloudflared._download_transport', side_effect=fetch) as download:
            with self.assertRaises(McpServiceError) as caught:
                _download_official(f'https://github.com/cloudflare/cloudflared/releases/download/{VERSION}/cloudflared-windows-amd64.exe', cancel)
            self.assertEqual(caught.exception.key, 'ui.mcpService.error.cancelled')
            self.assertEqual(download.call_count, 1)

    def test_windows_native_fallback_after_other_transports_fail(self):
        from harness.app.cloudflared import _download_transport
        with patch('harness.app.cloudflared.sys.platform', 'win32'), patch('harness.app.cloudflared._download', side_effect=OSError('TLS failed')), patch('harness.app.cloudflared.shutil.which', return_value=None), patch('harness.app.cloudflared._download_windows', return_value=b'fixture') as native:
            self.assertEqual(_download_transport('https://example.test/release', threading.Event()), b'fixture')
            native.assert_called_once()
