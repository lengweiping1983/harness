"""ConPTY使用真实构造器初始化等待状态，不能靠测试注入缺失条件变量。"""
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from harness.tools.windows_conpty import WindowsConPTYProcess
from harness.tools.processes import ProcessCleanupError


class ConPTYWaitInitializationTests(unittest.TestCase):
    def test_constructor_initializes_inherited_wait_and_stop_state(self):
        process = object.__new__(WindowsConPTYProcess)
        kernel = SimpleNamespace(CreateJobObjectW=Mock(side_effect=RuntimeError('fixture boundary')),
                                 TerminateJobObject=Mock(return_value=1))
        # 在原生分配前截断，仍执行真实构造器的状态初始化；不申请系统资源。
        with patch.dict(sys.modules, {'msvcrt': SimpleNamespace()}), \
             patch('harness.tools.windows_conpty.kernel_api', return_value=kernel), \
             patch('harness.tools.windows_conpty.conpty_api', return_value=object()), \
             patch.object(process, 'close'):
            with self.assertRaisesRegex(RuntimeError, 'fixture boundary'):
                process.__init__(['fixture'], cwd='.', env={})
        process.pid = 42
        process.active_count = Mock(return_value=1)
        with self.assertRaises(ProcessCleanupError):
            process.wait(.001)
        self.assertFalse(process._confirmed)
        process.active_count = Mock(side_effect=[1, 0])
        process.root_code = Mock(return_value=0)
        process.stop(.1)
        self.assertTrue(process._confirmed)
        kernel.TerminateJobObject.assert_called_once()
