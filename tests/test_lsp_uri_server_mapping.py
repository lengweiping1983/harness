"""LSP URI保留路径身份，服务器发现只选择当前支持的协议入口。"""
import os
import tempfile
import unittest
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import SimpleNamespace
from unittest.mock import patch
from harness.tools.builtin import lsp_tools


class LspUriServerMappingTests(unittest.TestCase):
    def test_local_paths_round_trip_original_unicode_and_reserved_characters(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory).resolve() / 'Original 原文 #?%20@.py'
            uri = lsp_tools.path_to_uri(target)
            self.assertEqual(uri, target.as_uri())
            self.assertEqual(lsp_tools.uri_to_path(uri), target)
        if os.name != 'nt':
            self.assertEqual(lsp_tools.uri_to_path('file:///C:/Original%20file.py'), Path('/C:/Original file.py'))
        self.assertEqual(lsp_tools.uri_to_path('file://localhost/Original%20file.py'), Path('/Original file.py'))

    def test_windows_drive_and_unc_uri_preserve_volume_and_host(self):
        for target, uri in (
            (PureWindowsPath('C:/Original 原文 #.py'), 'file:///C:/Original%20%E5%8E%9F%E6%96%87%20%23.py'),
            (PureWindowsPath('//host/share/Original 原文 #.py'), 'file://host/share/Original%20%E5%8E%9F%E6%96%87%20%23.py'),
        ):
            with self.subTest(target=target):
                with patch.object(lsp_tools, 'Path', PureWindowsPath), patch.object(lsp_tools, 'os', SimpleNamespace(name='nt')):
                    self.assertEqual(lsp_tools.uri_to_path(uri), target)
        with patch.object(lsp_tools, 'Path', PurePosixPath), patch.object(lsp_tools, 'os', SimpleNamespace(name='posix')):
            self.assertEqual(lsp_tools.uri_to_path('file://host/share/Original.py'), PurePosixPath('//host/share/Original.py'))

    def test_tsserver_alone_does_not_masquerade_as_lsp(self):
        for language in ('typescript', 'javascript', 'tsx'):
            with self.subTest(language=language), patch.object(lsp_tools.shutil, 'which', side_effect=lambda candidate: '/fixture/tsserver' if candidate == 'tsserver' else None):
                self.assertEqual(lsp_tools.find_server(language), (None, []))
        with patch.object(lsp_tools.shutil, 'which', return_value='/fixture/typescript-language-server') as which:
            self.assertEqual(lsp_tools.find_server('typescript'), ('/fixture/typescript-language-server', ['--stdio']))
            which.assert_called_once_with('typescript-language-server')

    def test_candidates_keep_order_and_do_not_share_returned_arguments(self):
        with patch.object(lsp_tools.shutil, 'which', side_effect=lambda candidate: '/fixture/pylsp' if candidate == 'pylsp' else None) as which:
            executable, arguments = lsp_tools.find_server('python')
            self.assertEqual(executable, '/fixture/pylsp')
            self.assertEqual(arguments, [])
            self.assertEqual([call.args[0] for call in which.call_args_list], ['pyright-langserver', 'pylsp'])
        with patch.object(lsp_tools.shutil, 'which', return_value='/fixture/pyright'):
            _, arguments = lsp_tools.find_server('python')
            arguments.append('foreign')
            self.assertEqual(lsp_tools.find_server('python')[1], ['--stdio'])
