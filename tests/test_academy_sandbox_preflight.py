"""沙箱启动预检依据固定探针的真实终态，失败不得变成业务通过。"""
import contextlib
import io
import json
import unittest
from unittest.mock import patch

from scripts import check_academy_sandbox as probe


class AcademySandboxPreflightTests(unittest.TestCase):
    def result(self, **changes):
        return {'status': 'passed', 'exit_code': 0, 'stdout': probe.MARKER, 'stderr': '',
                'truncated': False, 'memory_limited': False, 'sandbox': 'seatbelt', **changes}

    def test_probe_owns_an_isolated_project_and_requires_exact_success(self):
        roots = []
        result = self.result()
        def run(runtime, command):
            roots.append(runtime.root)
            self.assertTrue((runtime.root / 'probe.py').is_file())
            self.assertEqual(command, ['python', 'probe.py'])
            self.assertEqual(runtime.timeout, 3)
            self.assertEqual(runtime.output_limit, 4096)
            return result
        with (patch.object(probe.AcademyRuntime, 'backend', return_value='seatbelt'),
              patch.object(probe.AcademyRuntime, 'run', autospec=True, side_effect=run)):
            report = probe.check_sandbox()
        self.assertTrue(report['passed'])
        self.assertIs(report['execution'], result)
        self.assertFalse(roots[0].exists())

    def test_unavailable_backend_never_attempts_an_unisolated_command(self):
        with (patch.object(probe.AcademyRuntime, 'backend', return_value='unavailable'),
              patch.object(probe.AcademyRuntime, 'run') as run):
            report = probe.check_sandbox()
        self.assertFalse(report['passed'])
        self.assertEqual(report['reason'], 'sandbox-unavailable')
        run.assert_not_called()

    def test_failed_and_inconsistent_probe_receipts_preserve_raw_evidence(self):
        for result in (self.result(status='failed', exit_code=71, stdout='', stderr='sandbox_apply: Operation not permitted'),
                       self.result(status='timeout'), self.result(exit_code=False), self.result(stdout=''),
                       self.result(truncated=True), self.result(memory_limited=True), self.result(sandbox='bubblewrap')):
            with (self.subTest(result=result), patch.object(probe.AcademyRuntime, 'backend', return_value='seatbelt'),
                  patch.object(probe.AcademyRuntime, 'run', return_value=result)):
                report = probe.check_sandbox()
            self.assertFalse(report['passed'])
            self.assertIs(report['execution'], result)
            self.assertEqual(report['reason'], 'sandbox-probe-failed')

    def test_cli_failure_is_nonzero_and_does_not_hide_probe_exceptions(self):
        for failure in ({'passed': False, 'reason': 'sandbox-unavailable'}, PermissionError('原始启动拒绝')):
            output, error = io.StringIO(), io.StringIO()
            with (self.subTest(failure=failure), contextlib.redirect_stdout(output), contextlib.redirect_stderr(error),
                  patch.object(probe, 'check_sandbox') as check):
                if isinstance(failure, Exception):
                    check.side_effect = failure
                else:
                    check.return_value = failure
                self.assertEqual(probe.main(), 1)
            self.assertFalse(json.loads(output.getvalue())['passed'])
            if isinstance(failure, Exception):
                self.assertIn('原始启动拒绝', error.getvalue())
