"""会话统计区分工具执行失败与任务引导取消。"""
import unittest
from types import SimpleNamespace

from harness.web.views import _event_stats
from harness.web.execution_trace import project


def event(seq, kind, data):
    """构造保留原始结果语义的最小事件。"""
    return SimpleNamespace(seq=seq, time=seq, type=kind, data=data, source_event_seqs=())


class ToolStatisticsTests(unittest.TestCase):
    def test_steering_skips_do_not_inflate_failures(self):
        """引导跳过仍保留调用次数，但不作为执行失败。"""
        events = []
        for index, display in enumerate(({'steering_deferred': True}, {}, {'steering_deferred': False})):
            call = str(index)
            events.extend([
                event(index * 2, 'tool/call', {'call_id': call, 'name': 'read', 'status': 'running'}),
                event(index * 2 + 1, 'tool/result', {'call_id': call, 'name': 'read', 'display': display,
                    'results': [{'tool_use_id': call, 'is_error': True, 'content': 'result'}]}),
            ])
        stats = _event_stats(events)
        self.assertEqual(stats['tool_calls'], 3)
        self.assertEqual(stats['tool_errors'], 2)
        self.assertEqual(stats['tools'], [{'name': 'read', 'count': 3, 'errors': 2}])

    def test_trace_distinguishes_steering_skip_from_error(self):
        """跟踪跳过状态与正文一致，保留原始错误结果供核查。"""
        events = [
            event(1, 'tool/call', {'call_id': 'skip', 'name': 'read', 'status': 'running'}),
            event(2, 'tool/result', {'call_id': 'skip', 'display': {'steering_deferred': True},
                'results': [{'tool_use_id': 'skip', 'is_error': True, 'content': 'steering recovery'}]}),
            event(3, 'tool/call', {'call_id': 'failed', 'name': 'read', 'status': 'running'}),
            event(4, 'tool/result', {'call_id': 'failed',
                'results': [{'tool_use_id': 'failed', 'is_error': True, 'content': 'actual failure'}]}),
        ]
        rows = project('session', events, [], [])
        self.assertEqual([row['status'] for row in rows], ['skipped', 'error'])
        self.assertEqual(rows[0]['result'], 'steering recovery')

    def test_trace_run_identity_comes_from_unique_start_not_completion(self):
        events = [event(1, 'tool/call', {'call_id': 'call', 'name': 'read', 'status': 'running', 'run_id': 'member'}),
                  event(2, 'tool/call', {'call_id': 'call', 'name': 'read', 'status': 'done', 'run_id': 'other'})]
        row = project('session', events, [], [])[0]
        self.assertEqual(row['run_id'], 'member')
        self.assertFalse(row['run_attribution_ambiguous'])

    def test_trace_reused_call_ids_cannot_prove_run_attribution(self):
        for second in ('old', 'current'):
            events = [event(1, 'tool/call', {'call_id': 'call', 'name': 'read', 'status': 'running', 'run_id': 'old'}),
                      event(2, 'tool/call', {'call_id': 'call', 'name': 'read', 'status': 'running', 'run_id': second})]
            row = project('session', events, [], [])[0]
            self.assertIsNone(row['run_id'])
            self.assertTrue(row['run_attribution_ambiguous'])
        row = project('session', [event(1, 'tool/call', {'call_id': 'call', 'name': 'read', 'status': 'done'})], [], [])[0]
        self.assertIsNone(row['run_id'])
