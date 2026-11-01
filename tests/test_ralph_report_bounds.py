"""报告解析先约束原始大小，避免巨大前缀或嵌套进入多次扫描。"""
import unittest
import subprocess
import sys
from unittest.mock import patch

from harness.tools.builtin.ralph_tools import MAX_REPORT_CHARS, _extract_json_object


class RalphReportBoundsTests(unittest.TestCase):
    def test_oversized_raw_report_is_rejected_before_json_parsing(self):
        for text in ('{' * (MAX_REPORT_CHARS + 1), 'x' * MAX_REPORT_CHARS + '{"status":"complete"}'):
            with self.subTest(prefix=text[:8]), patch('harness.tools.builtin.ralph_tools.json.loads', side_effect=AssertionError('Unbounded parse')):
                self.assertIsNone(_extract_json_object(text))

    def test_current_plain_fenced_and_embedded_objects_keep_original_values(self):
        for text in ('{"summary":"Original 原文"}', '```json\n{"summary":"Original 原文"}\n```', 'Original prefix {"summary":"Original 原文"} suffix'):
            self.assertEqual(_extract_json_object(text), {'summary': 'Original 原文'})

    def test_incomplete_nested_report_finishes_within_external_deadline(self):
        script = "from harness.tools.builtin.ralph_tools import _extract_json_object; assert _extract_json_object('{' * 60000) is None"
        result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, timeout=3)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_excessive_json_nesting_is_a_rejected_report_not_a_recursion_error(self):
        self.assertIsNone(_extract_json_object('[' * 2000 + '0' + ']' * 2000))
