"""Windows 终端目录只暴露可解析的 Shell，并保留启动参数。"""
import unittest
from unittest.mock import patch

from harness.tools.builtin.terminal_tools import TerminalError, TerminalSessionService


class TerminalBackendTests(unittest.TestCase):
    def test_missing_pwsh_is_not_advertised_or_launched(self):
        executables = {'cmd.exe': r'C:\Windows\System32\cmd.exe',
                       'powershell.exe': r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'}
        with patch('sys.platform', 'win32'), patch(
            'harness.tools.builtin.terminal_tools.shutil.which', side_effect=executables.get
        ), patch('harness.tools.builtin.terminal_tools.os.path.isfile', return_value=False):
            service = TerminalSessionService()
            self.assertEqual(set(service.backends), {'cmd', 'powershell'})
            self.assertEqual(service.resolve('powershell'),
                             [executables['powershell.exe'], '-NoLogo', '-NoProfile'])
            with self.assertRaises(TerminalError):
                service.resolve('pwsh')

    def test_installed_pwsh_uses_resolved_path_with_spaces(self):
        executable = r'C:\Program Files\PowerShell\7\pwsh.exe'
        with patch('sys.platform', 'win32'), patch(
            'harness.tools.builtin.terminal_tools.shutil.which',
            side_effect=lambda name: executable if name == 'pwsh.exe' else None,
        ):
            self.assertEqual(TerminalSessionService().resolve('pwsh'),
                             [executable, '-NoLogo', '-NoProfile'])
