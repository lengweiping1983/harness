"""测试发现为空时必须让聚合入口失败。"""
import unittest
from unittest.mock import patch

from scripts.run_python_tests import main


class DiscoveryTests(unittest.TestCase):
    def test_empty_suite_fails(self):
        with patch('sys.argv', ['run_python_tests.py']), patch('unittest.defaultTestLoader.discover', return_value=unittest.TestSuite()):
            self.assertEqual(main(), 1)
