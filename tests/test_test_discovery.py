"""环境异常、发现为空和业务失败都必须让聚合入口失败。"""
import contextlib
import io
import subprocess
import sys
import textwrap
import unittest
from unittest.mock import Mock, patch

from scripts.run_python_tests import DiagnosticTestResult, check_environment, environment_identity, main, positive_seconds


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
        self.assertEqual(diagnostic.cancel_dump_traceback_later.call_count, 2)
        self.assertEqual(diagnostic.dump_traceback_later.call_args.args, (.5,))
        self.assertTrue(diagnostic.dump_traceback_later.call_args.kwargs['repeat'])

    def test_diagnostic_interval_rejects_nonpositive_and_nonfinite_values(self):
        import argparse
        for value in ('0', '-1', 'nan', 'inf', '-inf'):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                positive_seconds(value)
        self.assertEqual(positive_seconds('0.25'), .25)

    def test_empty_suite_fails(self):
        with (patch('sys.argv', ['run_python_tests.py']), patch('scripts.run_python_tests.check_environment'),
              patch('unittest.defaultTestLoader.discover', return_value=unittest.TestSuite())):
            self.assertEqual(main(), 1)

    def test_environment_probe_preserves_real_directory_semantics(self):
        """真实临时目录重复创建成功，非 macOS 不依赖 ps。"""
        with patch('scripts.run_python_tests.sys.platform', 'linux'), patch('scripts.run_python_tests.subprocess.run') as run:
            check_environment()
        run.assert_not_called()

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
              patch('unittest.defaultTestLoader.discover') as discover, contextlib.redirect_stderr(error)):
            self.assertEqual(main(), 1)
        discover.assert_not_called()
        self.assertIn('FileExistsError: fixture broker EEXIST', error.getvalue())

    def test_mac_memory_probe_rejects_denial_nonzero_invalid_output_and_timeout(self):
        """保留系统拒绝与非零回执，不伪造内存监督能力。"""
        command = ['/bin/ps', '-o', 'rss=', '-p']
        for failure in (PermissionError('fixture ps denied'), subprocess.TimeoutExpired(command, 5),
                        subprocess.CalledProcessError(1, command), Mock(stdout=b'invalid')):
            error = io.StringIO()
            with (self.subTest(failure=failure), patch('sys.argv', ['run_python_tests.py']),
                  patch('scripts.run_python_tests.sys.platform', 'darwin'),
                  patch('scripts.run_python_tests.subprocess.run') as run,
                  patch('unittest.defaultTestLoader.discover') as discover, contextlib.redirect_stderr(error)):
                if isinstance(failure, Exception):
                    run.side_effect = failure
                else:
                    run.return_value = failure
                self.assertEqual(main(), 1)
                discover.assert_not_called()
                self.assertEqual(run.call_args.args[0][:4], command)
                self.assertTrue(run.call_args.kwargs['check'])
                self.assertIn('测试尚未发现或执行', error.getvalue())

    def test_business_failure_is_not_reclassified_as_environment_failure(self):
        runner = Mock()
        runner.run.return_value.wasSuccessful.return_value = False
        suite = unittest.TestSuite([unittest.FunctionTestCase(lambda: None)])
        with (patch('sys.argv', ['run_python_tests.py']), patch('scripts.run_python_tests.check_environment'),
              patch('unittest.defaultTestLoader.discover', return_value=suite),
              patch('unittest.TextTestRunner', return_value=runner)):
            self.assertEqual(main(), 1)
        runner.run.assert_called_once_with(suite)

    def test_identity_reports_loaded_customization_without_vendor_inference(self):
        with patch.dict('sys.modules', {'sitecustomize': Mock(__file__='/fixture/sitecustomize.py')}):
            self.assertEqual(environment_identity()['sitecustomize'], '/fixture/sitecustomize.py')
