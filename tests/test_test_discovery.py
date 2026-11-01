"""环境异常、发现为空和业务失败都必须让聚合入口失败。"""
import contextlib
import io
import subprocess
import unittest
from unittest.mock import Mock, patch

from scripts.run_python_tests import check_environment, environment_identity, main


class DiscoveryTests(unittest.TestCase):
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
