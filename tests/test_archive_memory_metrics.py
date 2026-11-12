"""验证评估探针不伪造内存数据，并核对Windows实际分配响应。"""
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.archive_memory_metrics import process_memory


class ArchiveMemoryMetricTests(unittest.TestCase):
    def test_unsupported_platform_is_explicit_and_native_errors_are_visible(self):
        with patch('scripts.archive_memory_metrics.sys.platform', 'unsupported'):
            self.assertEqual(process_memory()['status'], 'unavailable')
        with patch('scripts.archive_memory_metrics.sys.platform', 'win32'):
            with patch('scripts.archive_memory_metrics._windows_reader', side_effect=OSError('native probe failed')):
                with self.assertRaisesRegex(OSError, 'native probe failed'):
                    process_memory()

    @unittest.skipUnless(sys.platform == 'win32', 'Windows原生内存API验证')
    def test_fresh_process_reports_private_commit_for_touched_allocation(self):
        script = '''
import json
from scripts.archive_memory_metrics import process_memory
before = process_memory()
block = bytearray(16 * 1024 * 1024)
for index in range(0, len(block), 4096):
    block[index] = 1
after = process_memory()
print(json.dumps({'before': before, 'after': after}))
'''
        process = subprocess.run([sys.executable, '-c', script],
            cwd=Path(__file__).resolve().parent.parent, capture_output=True, text=True, timeout=20)
        self.assertEqual(process.returncode, 0, process.stderr)
        data = json.loads(process.stdout)
        before, after = data['before'], data['after']
        self.assertEqual(after['status'], 'available')
        self.assertGreaterEqual(after['private_committed_bytes'] - before['private_committed_bytes'], 12 * 1024 * 1024)
        self.assertGreater(after['working_set_bytes'], 0)
        self.assertGreaterEqual(after['lifetime_peak_working_set_bytes'], after['working_set_bytes'])
