"""差异展示与文件读取使用相同行边界，不拆开源码中的Unicode分隔符。"""
import unittest
from pathlib import Path
from harness.tools.builtin.file_diff import file_diff


class FileDiffLineTests(unittest.TestCase):
    def test_unicode_separators_remain_inside_source_line(self):
        for separator in ('\u2028', '\u2029', '\v', '\f'):
            with self.subTest(separator=repr(separator)):
                before = 'value = "a' + separator + 'b"\r\nold\r\n'
                after = 'value = "a' + separator + 'b"\r\nnew\r\n'
                result = file_diff(before, after, Path('fixture.py'), changed=True)
                self.assertIn('@@ -1,2 +1,2 @@', result['diff'])
                self.assertIn(' value = "a' + separator + 'b"\n-old\n+new', result['diff'])
                self.assertEqual(result['_file_change']['before'], before)
                self.assertEqual(result['_file_change']['after'], after)
