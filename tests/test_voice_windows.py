"""Windows 语音接入使用隔离安装树和只读进程查询替身，不代替实机验收。"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from harness.tools.builtin import hex_tools
from harness.core.language import current_language
from tests.test_hex_tools import FakeHexServer


class WindowsVoiceDiscoveryTests(unittest.TestCase):
    def test_bundled_engine_is_independent_of_a_damaged_external_install(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(hex_tools.sys, 'platform', 'win32'), \
             patch.object(hex_tools.platform, 'machine', return_value='AMD64'), \
             patch.dict(os.environ, {'APPDATA': str(Path(tmp) / 'user-data')}), \
             patch.object(hex_tools, '__file__', str(Path(tmp) / 'package/harness/tools/builtin/hex_tools.py')):
            binary = Path(tmp) / 'package/harness/resources/hex/windows-x86_64/hex.exe'
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b'bundled fixture')
            pointer = Path(tmp) / 'user-data/hex/current-version'
            pointer.parent.mkdir(parents=True)
            pointer.write_bytes(b'../../damaged-install')
            self.assertEqual(hex_tools.hex_binary_path(), binary.resolve())
            self.assertEqual(pointer.read_bytes(), b'../../damaged-install')
            binary.unlink()
            with self.assertRaises(hex_tools.HexToolError):
                hex_tools.hex_binary_path()
            self.assertEqual(pointer.read_bytes(), b'../../damaged-install')

    def test_installed_pointer_supports_x64_arm64_and_explicit_updates(self):
        for machine in ('AMD64', 'ARM64'):
            with self.subTest(machine=machine), tempfile.TemporaryDirectory() as tmp, \
                 patch.object(hex_tools.sys, 'platform', 'win32'), \
                 patch.object(hex_tools.platform, 'machine', return_value=machine), \
                 patch.dict(os.environ, {'APPDATA': tmp, 'HEX_APPLICATION_SUPPORT_DIR': str(Path(tmp)/'isolated-data')}):
                root = Path(tmp)/'hex'
                pointer = root/'current-version'
                for version in ('3.2.0', '3.2.1-dev.1'):
                    binary = root/'versions'/version/'hex.exe'
                    binary.parent.mkdir(parents=True)
                    binary.touch()
                    pointer.write_text(version+'\n', encoding='utf-8')
                    self.assertEqual(hex_tools.hex_binary_path(), binary)
                self.assertEqual(hex_tools.hex_discovery_path(), Path(tmp)/'isolated-data'/'local-api.json')
                self.assertEqual(pointer.read_text(encoding='utf-8'), '3.2.1-dev.1\n')

    def test_bad_pointer_is_preserved_and_does_not_disable_harness_construction(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(hex_tools.sys, 'platform', 'win32'), \
             patch.object(hex_tools, 'hex_cpu_supported', return_value=True), \
             patch.dict(os.environ, {'APPDATA': tmp}), patch.object(hex_tools.subprocess, 'Popen') as spawn:
            root = Path(tmp)/'hex'
            root.mkdir()
            pointer = root/'current-version'
            for original in (b'../../other', b'C:\\elsewhere', b'', b'\xff', b'v'*257, b'missing-version'):
                with self.subTest(original=original):
                    pointer.write_bytes(original)
                    manager = hex_tools.HexServiceManager(discovery=Path(tmp)/'no-api.json')
                    with self.assertRaises(hex_tools.HexToolError) as caught:
                        manager.ensure_running()
                    self.assertEqual(caught.exception.copy['key'], 'ui.voice.installationInvalid')
                    self.assertEqual(pointer.read_bytes(), original)
            spawn.assert_not_called()

    def test_absent_install_returns_platform_binary_without_creating_user_data(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(hex_tools.sys, 'platform', 'win32'), \
             patch.object(hex_tools.platform, 'machine', return_value='AMD64'), \
             patch.dict(os.environ, {'APPDATA': tmp}):
            path = hex_tools.hex_binary_path()
            self.assertEqual(path.name, 'hex.exe')
            self.assertIn('windows-x86_64', path.parts)
            self.assertFalse((Path(tmp)/'hex').exists())


class WindowsVoiceProcessTests(unittest.TestCase):
    def api(self, exit_code=259):
        """模拟 Win32 只读查询，断言借用句柄必定归还。"""
        kernel = Mock()
        kernel.OpenProcess.return_value = 42
        kernel.CloseHandle.return_value = True
        def query(handle, pointer):
            pointer._obj.value = exit_code
            return True
        kernel.GetExitCodeProcess.side_effect = query
        return kernel

    def test_probe_never_sends_signals_and_distinguishes_running_from_exited(self):
        for code in (259, 0, 1):
            kernel = self.api(code)
            with patch.object(hex_tools.sys, 'platform', 'win32'), \
                 patch.object(hex_tools, '_windows_process_api', return_value=kernel), \
                 patch.object(hex_tools.os, 'kill') as kill:
                self.assertEqual(hex_tools._pid_alive(456), code == 259)
                kill.assert_not_called()
            kernel.OpenProcess.assert_called_once_with(0x1000, False, 456)
            kernel.CloseHandle.assert_called_once_with(42)

    def test_missing_and_access_denied_do_not_kill_or_close_an_unowned_handle(self):
        for code in (5, 87):
            kernel = self.api()
            kernel.OpenProcess.return_value = None
            kernel.GetLastError.return_value = code
            with patch.object(hex_tools.sys, 'platform', 'win32'), \
                 patch.object(hex_tools, '_windows_process_api', return_value=kernel), \
                 patch.object(hex_tools.os, 'kill') as kill:
                self.assertEqual(hex_tools._pid_alive(456), code == 5)
                kill.assert_not_called()
            kernel.CloseHandle.assert_not_called()

    def test_query_failure_still_closes_handle_and_retains_bilingual_error(self):
        kernel = self.api()
        kernel.GetExitCodeProcess.side_effect = None
        kernel.GetExitCodeProcess.return_value = False
        kernel.GetLastError.return_value = 6
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language), patch.object(hex_tools, '_windows_process_api', return_value=kernel):
                with self.assertRaises(hex_tools.HexToolError) as caught:
                    hex_tools._windows_pid_alive(456)
                self.assertEqual(caught.exception.copy['key'], 'ui.voice.pidProbeFailed')
                self.assertEqual(caught.exception.copy['params']['code'], 6)
        self.assertEqual(kernel.CloseHandle.call_count, 3)

    def test_query_exception_and_close_failure_keep_both_originals(self):
        kernel = self.api()
        original = OSError('fixture query failed')
        kernel.GetExitCodeProcess.side_effect = original
        kernel.CloseHandle.return_value = False
        kernel.GetLastError.return_value = 6
        with patch.object(hex_tools, '_windows_process_api', return_value=kernel):
            with self.assertRaises(BaseExceptionGroup) as caught:
                hex_tools._windows_pid_alive(456)
        self.assertIs(caught.exception.exceptions[0], original)
        kernel.CloseHandle.assert_called_once_with(42)

    def test_authenticated_running_service_is_reused_without_signal_or_spawn(self):
        fake = FakeHexServer()
        try:
            kernel = self.api()
            manager = hex_tools.HexServiceManager(discovery=fake.discovery)
            with patch.object(hex_tools.sys, 'platform', 'win32'), \
                 patch.object(hex_tools, 'hex_cpu_supported', return_value=True), \
                 patch.object(hex_tools, '_windows_process_api', return_value=kernel), \
                 patch.object(hex_tools.os, 'kill') as kill, \
                 patch.object(hex_tools.subprocess, 'Popen') as spawn:
                self.assertEqual(manager.ensure_running(), (f'http://127.0.0.1:{fake.port}', 'test-token'))
                manager.stop()
                kill.assert_not_called()
                spawn.assert_not_called()
        finally:
            fake.close()
