"""真实课程沙箱中的原生内存监督、拒绝传播与所属资源收尾。"""
import errno
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from harness.academy.runtime import AcademyRuntime


@unittest.skipUnless(sys.platform == 'darwin', '仅在 macOS 验证原生内存监督')
class AcademyRuntimeMemoryTests(unittest.TestCase):
    def runtime(self, root, source):
        (root / 'main.py').write_text(source, encoding='utf-8')
        runtime = AcademyRuntime(root)
        runtime.ensure_environment()
        return runtime

    def test_actual_sandbox_executes_without_ps_and_records_memory(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = self.runtime(Path(directory), "import time; data=bytearray(8*1024*1024); time.sleep(.15); print('done')")
            with patch('harness.academy.runtime.subprocess.run', side_effect=PermissionError('ps denied')):
                result = runtime.run(['python', 'main.py'])
            self.assertEqual(result['status'], 'passed', result)
            self.assertEqual(result['stdout'], 'done\n')
            self.assertGreater(result['peak_memory_kib'], 0)
            self.assertFalse(result['memory_limited'])

    def test_memory_threshold_still_stops_execution_and_closes_readers(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = self.runtime(Path(directory), 'import time; time.sleep(30)')
            with patch('harness.academy.runtime.resident_memory_kib', return_value=512 * 1024 + 1):
                result = runtime.run(['python', 'main.py'])
            self.assertEqual(result['status'], 'failed', result)
            self.assertTrue(result['memory_limited'])
            self.assertEqual(result['peak_memory_kib'], 512 * 1024 + 1)
            self.assertNotEqual(result['exit_code'], 0)

    def test_observation_failure_propagates_after_real_process_and_pipes_close(self):
        popen = subprocess.Popen
        for original in (PermissionError(errno.EPERM, '原始监督拒绝'),
                         ProcessLookupError(errno.ESRCH, '存活进程不可观察')):
            with self.subTest(original=original), tempfile.TemporaryDirectory() as directory:
                runtime = self.runtime(Path(directory), 'import time; time.sleep(30)')
                owned = []
                def spawn(*args, **kwargs):
                    process = popen(*args, **kwargs)
                    owned.append(process)
                    return process
                with (patch('harness.academy.runtime.subprocess.Popen', side_effect=spawn),
                      patch('harness.academy.runtime.resident_memory_kib', side_effect=original)):
                    with self.assertRaises(OSError) as caught:
                        runtime.run(['python', 'main.py'])
                self.assertIs(caught.exception, original)
                self.assertEqual(len(owned), 1)
                self.assertIsNotNone(owned[0].poll())
                self.assertTrue(owned[0].stdout.closed)
                self.assertTrue(owned[0].stderr.closed)
