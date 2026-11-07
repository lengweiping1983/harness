"""环境异常、发现为空和业务失败都必须让聚合入口失败。"""
import contextlib
import io
import os
import subprocess
import sys
import textwrap
import unittest
from unittest.mock import Mock, patch

from scripts.run_python_tests import (DiagnosticTestResult, academy_sandbox_preflight, check_environment,
                                      environment_identity, main, positive_seconds)


class DiscoveryTests(unittest.TestCase):
    def test_real_long_wait_emits_traceback_and_finishes_without_skipping(self):
        """独立解释器实测原生诊断计时器，不让测试自身污染外层计时器。"""
        script = textwrap.dedent('''
            import time, unittest
            from functools import partial
            from scripts.run_python_tests import DiagnosticTestResult
            class Fixture(unittest.TestCase):
                def test_wait(self):
                    time.sleep(.2)
            result = unittest.TextTestRunner(resultclass=partial(
                DiagnosticTestResult, traceback_timeout=.05)).run(
                unittest.defaultTestLoader.loadTestsFromTestCase(Fixture))
            raise SystemExit(0 if result.wasSuccessful() and result.testsRun == 1 else 1)
        ''')
        result = subprocess.run([sys.executable, '-X', 'utf8', '-c', script],
                                capture_output=True, timeout=10)
        diagnostic = result.stderr.decode('utf-8')
        self.assertEqual(result.returncode, 0, diagnostic)
        self.assertIn('Timeout (', diagnostic)
        self.assertIn('in test_wait', diagnostic)
        self.assertIn('Ran 1 test', diagnostic)
        self.assertIn('OK', diagnostic)

    def test_diagnostic_timer_tracks_each_case_and_preserves_failures(self):
        """多个用例各自计时，失败及收尾不留下跨用例的诊断计时器。"""
        class Fixture(unittest.TestCase):
            def test_failure(self):
                self.fail('original failure')
            def test_success(self):
                pass
        stream = io.StringIO()
        with patch('scripts.run_python_tests.faulthandler') as diagnostic:
            result = unittest.TextTestRunner(stream=stream,
                resultclass=lambda *args, **kwargs: DiagnosticTestResult(*args, traceback_timeout=.5, **kwargs)
            ).run(unittest.defaultTestLoader.loadTestsFromTestCase(Fixture))
        self.assertEqual(result.testsRun, 2)
        self.assertFalse(result.wasSuccessful())
        self.assertEqual(len(result.failures), 1)
        self.assertIn('original failure', result.failures[0][1])
        self.assertEqual(diagnostic.dump_traceback_later.call_count, 2)
        self.assertEqual(diagnostic.cancel_dump_traceback_later.call_count, 1)
        self.assertEqual(diagnostic.dump_traceback_later.call_args.args, (.5,))
        self.assertTrue(diagnostic.dump_traceback_later.call_args.kwargs['repeat'])

    def test_diagnostic_timer_covers_async_runner_executor_shutdown(self):
        """stopTest 后的异步运行器关闭仍受诊断监督，不能留下观察盲区。"""
        script = textwrap.dedent('''
            import asyncio, threading, time, unittest
            from functools import partial
            from scripts.run_python_tests import DiagnosticTestResult
            class Fixture(unittest.IsolatedAsyncioTestCase):
                async def test_executor(self):
                    entered = threading.Event()
                    def owned_thread():
                        entered.set()
                        time.sleep(.3)
                    asyncio.get_running_loop().run_in_executor(None, owned_thread)
                    assert await asyncio.to_thread(entered.wait, 1)
            result = unittest.TextTestRunner(resultclass=partial(
                DiagnosticTestResult, traceback_timeout=.05)).run(
                unittest.defaultTestLoader.loadTestsFromTestCase(Fixture))
            raise SystemExit(0 if result.wasSuccessful() else 1)
        ''')
        result = subprocess.run([sys.executable, '-X', 'utf8', '-c', script],
                                capture_output=True, timeout=10)
        diagnostic = result.stderr.decode('utf-8')
        self.assertEqual(result.returncode, 0, diagnostic)
        self.assertIn('Timeout (', diagnostic)
        self.assertIn('in owned_thread', diagnostic)
        self.assertIn('Ran 1 test', diagnostic)

    def test_diagnostic_interval_rejects_nonpositive_and_nonfinite_values(self):
        import argparse
        for value in ('0', '-1', 'nan', 'inf', '-inf'):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                positive_seconds(value)
        self.assertEqual(positive_seconds('0.25'), .25)

    def test_empty_suite_fails(self):
        with (patch('sys.argv', ['run_python_tests.py']), patch('scripts.run_python_tests.check_environment'),
              patch('scripts.run_python_tests.academy_sandbox_preflight', return_value=None),
              patch('unittest.defaultTestLoader.discover', return_value=unittest.TestSuite())):
            self.assertEqual(main(), 1)

    def test_environment_probe_preserves_real_directory_semantics(self):
        """真实临时目录重复创建成功，非 macOS 不调用原生内存接口。"""
        with patch('scripts.run_python_tests.sys.platform', 'linux'), patch('harness.core.macos_process.resident_memory_kib') as measure:
            check_environment()
        measure.assert_not_called()

    def test_broken_exist_ok_stops_before_discovery_with_original_diagnostic(self):
        """模拟宿主代理破坏 exist_ok，不能计成业务用例失败。"""
        from pathlib import Path
        mkdir = Path.mkdir

        def broken(path, *args, **kwargs):
            if kwargs.get('exist_ok') and path.is_dir():
                raise FileExistsError('fixture broker EEXIST')
            return mkdir(path, *args, **kwargs)

        error = io.StringIO()
        with (patch('sys.argv', ['run_python_tests.py']), patch.object(Path, 'mkdir', broken),
              patch('scripts.run_python_tests.academy_sandbox_preflight', return_value=None) as sandbox,
              patch('unittest.defaultTestLoader.discover') as discover, contextlib.redirect_stderr(error)):
            self.assertEqual(main(), 1)
        discover.assert_not_called()
        sandbox.assert_not_called()
        self.assertIn('测试尚未发现或执行', error.getvalue())
        self.assertIn('FileExistsError: fixture broker EEXIST', error.getvalue())

    def test_mac_memory_probe_rejects_denial_and_invalid_values(self):
        """原生观察被拒仍阻止发现，不伪造内存监督能力。"""
        for failure in (PermissionError('fixture native denied'), OSError('fixture native failure'),
                        0, -1, True, '123'):
            error = io.StringIO()
            with (self.subTest(failure=failure), patch('sys.argv', ['run_python_tests.py']),
                  patch('scripts.run_python_tests.sys.platform', 'darwin'),
                  patch('harness.core.macos_process.resident_memory_kib') as measure,
                  patch('scripts.run_python_tests.academy_sandbox_preflight', return_value=None),
                  patch('unittest.defaultTestLoader.discover') as discover, contextlib.redirect_stderr(error)):
                if isinstance(failure, Exception):
                    measure.side_effect = failure
                else:
                    measure.return_value = failure
                self.assertEqual(main(), 1)
                discover.assert_not_called()
                measure.assert_called_once_with(os.getpid())
                self.assertIn('测试尚未发现或执行', error.getvalue())
                if isinstance(failure, Exception):
                    self.assertIn(str(failure), error.getvalue())

    def test_mac_memory_probe_uses_native_observation_without_spawning_ps(self):
        """受限命令执行不影响可用的原生观察。"""
        with (patch('scripts.run_python_tests.sys.platform', 'darwin'),
              patch('harness.core.macos_process.resident_memory_kib', return_value=1024) as measure,
              patch('scripts.run_python_tests.subprocess.run', side_effect=PermissionError('ps denied'))):
            check_environment()
        measure.assert_called_once_with(os.getpid())

    def test_business_failure_is_not_reclassified_as_environment_failure(self):
        runner = Mock()
        runner.run.return_value.wasSuccessful.return_value = False
        suite = unittest.TestSuite([unittest.FunctionTestCase(lambda: None)])
        with (patch('sys.argv', ['run_python_tests.py']), patch('scripts.run_python_tests.check_environment'),
              patch('scripts.run_python_tests.academy_sandbox_preflight', return_value=None),
              patch('unittest.defaultTestLoader.discover', return_value=suite),
              patch('unittest.TextTestRunner', return_value=runner)):
            self.assertEqual(main(), 1)
        runner.run.assert_called_once_with(suite)

    def test_identity_reports_loaded_customization_without_vendor_inference(self):
        with patch.dict('sys.modules', {'sitecustomize': Mock(__file__='/fixture/sitecustomize.py')}):
            self.assertEqual(environment_identity()['sitecustomize'], '/fixture/sitecustomize.py')

    def test_sandbox_preflight_failure_stops_before_discovery_with_raw_receipt(self):
        """课程沙箱启动被拒时整体失败并保留结构化回执，未执行项不算通过。"""
        receipt = {'passed': False, 'reason': 'sandbox-probe-failed',
                   'execution': {'exit_code': 71, 'stderr': 'sandbox_apply: Operation not permitted'}}
        error = io.StringIO()
        with (patch('sys.argv', ['run_python_tests.py']), patch('scripts.run_python_tests.check_environment'),
              patch('scripts.run_python_tests.academy_sandbox_preflight', return_value=receipt),
              patch('unittest.defaultTestLoader.discover') as discover, contextlib.redirect_stderr(error)):
            self.assertEqual(main(), 1)
        discover.assert_not_called()
        self.assertIn('测试尚未发现或执行', error.getvalue())
        self.assertIn('sandbox-probe-failed', error.getvalue())
        self.assertIn('Operation not permitted', error.getvalue())

    def test_sandbox_preflight_passing_keeps_normal_discovery(self):
        """沙箱可用时预检不改变聚合入口行为，仍完整发现并执行用例。"""
        runner = Mock()
        runner.run.return_value.wasSuccessful.return_value = True
        suite = unittest.TestSuite([unittest.FunctionTestCase(lambda: None)])
        with (patch('sys.argv', ['run_python_tests.py']), patch('scripts.run_python_tests.check_environment'),
              patch('scripts.run_python_tests.academy_sandbox_preflight', return_value=None),
              patch('unittest.defaultTestLoader.discover', return_value=suite),
              patch('unittest.TextTestRunner', return_value=runner)):
            self.assertEqual(main(), 0)
        runner.run.assert_called_once_with(suite)

    def test_sandbox_preflight_ready_probe_allows_discovery(self):
        """探针实测通过时放行，不跳过也不改写业务用例。"""
        ready = {'passed': True, 'reason': 'ready', 'execution': {}}
        runner = Mock()
        runner.run.return_value.wasSuccessful.return_value = True
        suite = unittest.TestSuite([unittest.FunctionTestCase(lambda: None)])
        with (patch('sys.argv', ['run_python_tests.py']), patch('scripts.run_python_tests.check_environment'),
              patch('harness.academy.runtime.AcademyRuntime.backend', return_value='seatbelt'),
              patch('scripts.check_academy_sandbox.check_sandbox', return_value=ready) as probe,
              patch('unittest.defaultTestLoader.discover', return_value=suite) as discover,
              patch('unittest.TextTestRunner', return_value=runner)):
            self.assertEqual(main(), 0)
        probe.assert_called_once_with()
        discover.assert_called_once()
        runner.run.assert_called_once_with(suite)

    def test_sandbox_preflight_skips_platforms_without_an_isolator(self):
        """隔离器未安装的平台沿用各用例既有 skipIf，不阻断常规回归。"""
        with (patch('harness.academy.runtime.AcademyRuntime.backend', return_value='unavailable'),
              patch('scripts.check_academy_sandbox.check_sandbox') as probe):
            self.assertIsNone(academy_sandbox_preflight())
        probe.assert_not_called()

    def test_sandbox_preflight_preserves_probe_exceptions(self):
        """探针自身异常保留原错误，不静默放行也不伪造通过。"""
        with (patch('harness.academy.runtime.AcademyRuntime.backend', return_value='seatbelt'),
              patch('scripts.check_academy_sandbox.check_sandbox', side_effect=PermissionError('原始启动拒绝'))):
            report = academy_sandbox_preflight()
        self.assertFalse(report['passed'])
        self.assertEqual(report['reason'], 'sandbox-probe-error')
        self.assertIn('原始启动拒绝', report['error']['message'])
