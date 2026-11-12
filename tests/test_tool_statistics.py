"""会话统计区分工具执行失败与任务引导取消。"""
import unittest
from types import SimpleNamespace

from harness.web.views import _event_stats
from harness.session.tool_activity import confirmed_tool_result, tool_invocations
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

    def test_result_requires_current_identity_without_falling_back_to_call_id(self):
        """旧别名或外层身份不能将缺少结果身份的记录配给正在执行的调用。"""
        for payload in ({'call_id': 'call'}, {}):
            with self.subTest(payload=payload):
                events = [
                    event(1, 'tool/call', {'call_id': 'call', 'name': 'read', 'status': 'running'}),
                    event(2, 'tool/result', {'call_id': 'call', 'name': 'read',
                        'results': [{**payload, 'is_error': False, 'content': 'unconfirmed'}]}),
                ]
                rows = tool_invocations(events)
                self.assertEqual(rows[0]['call_id'], 'call')
                self.assertEqual(rows[0]['status'], 'running')
                self.assertIsNone(rows[0]['_result_event'])
                self.assertFalse(any(confirmed_tool_result(row) for row in rows))

    def test_result_uses_current_identity_and_checks_conflicting_envelope(self):
        """结果自身身份用于配对，外层身份冲突仍不能提供成功证据。"""
        for envelope in ({}, {'call_id': 'call'}, {'call_id': 'other'}):
            with self.subTest(envelope=envelope):
                events = [
                    event(1, 'tool/call', {'call_id': 'call', 'name': 'read', 'status': 'running'}),
                    event(2, 'tool/result', {**envelope, 'name': 'read',
                        'results': [{'tool_use_id': 'call', 'is_error': False, 'content': 'result'}]}),
                ]
                rows = tool_invocations(events)
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]['status'], 'done')
                self.assertEqual(confirmed_tool_result(rows[0]), envelope.get('call_id', 'call') == 'call')
