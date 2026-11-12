"""验证失败重试、混合计量和重复回执不会误算任务预算。"""
import unittest

from harness.core.response_cost import ResponseCost
from harness.llm.types import StreamEvent, TokenUsage


class ResponseCostTests(unittest.TestCase):
    def test_no_response_progress_does_not_charge_estimated_input(self):
        cost = ResponseCost(100)
        cost.begin_physical()
        cost.finish_physical({'trace_id': 'connection-failed'})
        self.assertEqual(cost.total, 0)

    def test_failed_attempt_and_successful_retry_are_both_charged_once(self):
        cost = ResponseCost(100)
        cost.begin_physical()
        cost.observe(StreamEvent(kind='text', text='abcd'))
        cost.finish_physical({'trace_id': 'failed'})
        cost.begin_physical(110)
        cost.observe(StreamEvent(kind='text', text='efgh'))
        receipt = {'trace_id': 'success', 'usage': {'input_tokens': 110, 'output_tokens': 20}}
        cost.finish_physical(receipt)
        cost.finish_physical(receipt)
        self.assertEqual(cost.total, 231)
        self.assertEqual(cost.settlement(9999), 231)
        self.assertEqual(cost.settlement_source(), 'estimated')

    def test_reported_usage_survives_a_later_empty_attempt(self):
        cost = ResponseCost(100)
        cost.begin_physical()
        cost.finish_physical({'trace_id': 'reported', 'usage': {'input_tokens': 80, 'output_tokens': 8}})
        cost.begin_physical()
        cost.finish_physical({'trace_id': 'empty'})
        self.assertEqual(cost.total, 88)
        self.assertEqual(cost.settlement_source(), 'reported')

    def test_invalid_metering_uses_only_current_attempt_progress(self):
        cost = ResponseCost(100)
        for identity in ('first', 'second'):
            cost.begin_physical()
            cost.observe(StreamEvent(kind='text', text='你好'))
            cost.finish_physical({'trace_id': identity, 'usage': {'input_tokens': True, 'output_tokens': 10000}})
        self.assertEqual(cost.total, 204)
        self.assertEqual(cost.physical_usage, {})

    def test_complete_response_metering_is_used_without_physical_receipts(self):
        cost = ResponseCost(100)
        cost.observe(StreamEvent(kind='usage', usage=TokenUsage(input_tokens=75, output_tokens=5)))
        self.assertEqual(cost.total, 80)
        self.assertEqual(cost.settlement_source(), 'reported')
