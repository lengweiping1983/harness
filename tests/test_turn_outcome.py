"""正常取消不应被投影为额外的执行失败。"""
import unittest
from types import SimpleNamespace

from harness.core.runs import turn_outcome


class TurnOutcomeTests(unittest.TestCase):
    def test_normal_abort_is_interrupted_without_synthetic_error(self):
        """保留取消终态，不从普通说明文字合成运行错误。"""
        self.assertEqual(turn_outcome(SimpleNamespace(reason='aborted', error='被取消', error_info=None)),
                         ('interrupted', None))

    def test_abort_preserves_explicit_structured_failure(self):
        """取消期间已有明确错误时不能丢弃可核验证据。"""
        error = {'code': 'storage/failed', 'message': '写入失败'}
        self.assertEqual(turn_outcome(SimpleNamespace(reason='aborted', error='被取消', error_info=error)),
                         ('interrupted', error))

    def test_regular_errors_remain_failed(self):
        """普通执行失败仍有通用错误码。"""
        self.assertEqual(turn_outcome(SimpleNamespace(reason='error', error='failure', error_info=None)),
                         ('failed', {'code': 'run/error', 'message': 'failure'}))
