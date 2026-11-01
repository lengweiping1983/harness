"""终端传输异常在实际调用时解析语言，保留原异常类型。"""
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from harness.core.language import current_language
from harness.i18n import tr
from harness.tools.pty_process import PtyProcess
from harness.tools.conpty_process import ConPTYProcess
from harness.tools.windows_conpty import WindowsConPTYProcess, conpty_api


class TerminalTransportLanguageTests(unittest.TestCase):
    def test_closed_and_missing_dependency_errors_follow_language(self):
        pty = object.__new__(PtyProcess)
        pty.fd = -1
        conpty = object.__new__(ConPTYProcess)
        conpty._finalizer = SimpleNamespace(alive=False)
        native = object.__new__(WindowsConPTYProcess)
        native._console_lock = threading.Lock()
        native.console = None
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                for operation, key in ((lambda: pty.write(b'fixture'), 'terminal.transport.pty_closed'),
                                       (lambda: conpty.write(b'fixture'), 'terminal.transport.conpty_closed'),
                                       (lambda: native.resize(80, 24), 'terminal.transport.conpty_closed')):
                    with self.assertRaises(BrokenPipeError) as caught:
                        operation()
                    self.assertEqual(str(caught.exception), tr(key))
                conpty_api.cache_clear()
                with patch('harness.tools.windows_conpty.importlib.util.find_spec', return_value=None):
                    with self.assertRaises(ImportError) as caught:
                        conpty_api()
                self.assertEqual(str(caught.exception), tr('terminal.transport.install_required'))
