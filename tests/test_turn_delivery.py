"""本轮结果只使用唯一轮次的真实事件，不把成功工具当成验收。"""
import unittest
from types import SimpleNamespace

from harness.session.events import SessionEvent
from harness.web.api.turn_completion import completion_snapshot
from harness.web.errors import Conflict, NotFound


def event(kind, seq, **data):
    return SessionEvent(kind, seq, seq, data)


def session(events):
    return SimpleNamespace(id='conversation', events=events,
                           events_of_type=lambda kind: [row for row in events if row.type == kind])


class TurnDeliveryTests(unittest.TestCase):
    def test_successful_present_is_an_artifact_not_verification(self):
        rows = [event('turn/start', 0, turn=1, run_id='run'),
                event('tool/call', 1, turn=1, run_id='run', call_id='call', name='present', status='running'),
                event('tool/result', 2, turn=1, run_id='run', call_id='call', name='present',
                      results=[{'tool_use_id': 'call', 'is_error': False}], display={'files': [{'path': '/work/result.txt'}]}),
                event('turn/end', 3, turn=1, run_id='run')]
        result = completion_snapshot(session(rows), 1)['delivery']
        self.assertEqual(result['artifacts'], [{'path': '/work/result.txt', 'source_seq': 2}])
        self.assertEqual(result['verification'], 'unverified')
        self.assertEqual(result['tool_executions'], 1)
        self.assertEqual(result['unknown_tools'], 0)

    def test_duplicate_result_remains_unknown_and_has_no_artifact(self):
        rows = [event('turn/start', 0, turn=1, run_id='run'),
                event('tool/call', 1, call_id='call', name='present', status='running'),
                event('tool/result', 2, call_id='call', name='present', results=[{'tool_use_id': 'call', 'is_error': False}] * 2,
                      display={'files': [{'path': '/work/result.txt'}]}), event('turn/end', 3, turn=1, run_id='run')]
        value = completion_snapshot(session(rows), 1)['delivery']
        self.assertEqual(value['unknown_tools'], 1)
        self.assertEqual(value['artifact_count'], 0)

    def test_foreign_run_result_cannot_complete_this_call(self):
        rows = [event('turn/start', 0, turn=1, run_id='first'),
                event('tool/call', 1, call_id='call', name='edit', status='running'),
                event('tool/result', 2, run_id='other', name='edit', call_id='call', results=[{'tool_use_id': 'call', 'is_error': False}]),
                event('turn/end', 3, turn=1, run_id='first')]
        value = completion_snapshot(session(rows), 1)['delivery']
        self.assertEqual(value['unknown_tools'], 1)

    def test_failed_command_is_not_hidden_by_completed_turn(self):
        rows = [event('turn/start', 0, turn=1, run_id='run'),
                event('tool/call', 1, call_id='call', name='bash', status='running'),
                event('tool/result', 2, name='bash', call_id='call', results=[{'tool_use_id': 'call', 'is_error': True}]),
                event('turn/end', 3, turn=1, run_id='run')]
        self.assertEqual(completion_snapshot(session(rows), 1)['delivery']['failed_tools'], 1)

    def test_duplicate_start_is_rejected(self):
        rows = [event('turn/start', 0, turn=1, run_id='first'),
                event('turn/start', 1, turn=1, run_id='second'), event('turn/end', 2, turn=1, run_id='second')]
        with self.assertRaises(Conflict):
            completion_snapshot(session(rows), 1)

    def test_later_turn_cannot_supply_missing_end(self):
        rows = [event('turn/start', 0, turn=1, run_id='first'),
                event('turn/start', 1, turn=2, run_id='second'), event('turn/end', 2, turn=2, run_id='second')]
        with self.assertRaises(NotFound):
            completion_snapshot(session(rows), 1)

    def test_partial_history_does_not_inherit_current_goal(self):
        value = completion_snapshot(session([event('turn/start', 0, turn=1, run_id='run'),
                                            event('turn/end', 1, turn=1, run_id='run')]), 1)
        self.assertEqual(value['delivery']['verification'], 'unverified')
        self.assertEqual(value['delivery']['artifact_count'], 0)

    def test_artifacts_preserve_only_original_location_and_user_description(self):
        rows = [event('turn/start', 0, turn=1, run_id='run'),
                event('tool/call', 1, call_id='p', name='present', status='running'),
                event('tool/result', 2, call_id='p', name='present', results=[{'tool_use_id': 'p', 'is_error': False}],
                      display={'workspace_root': '/original', 'files': [{'path': '/original/report.md',
                               'name': '报告 Report.md', 'description': 'Original {raw} 原文', 'extra': 'not exported'}]}),
                event('turn/end', 3, turn=1, run_id='run')]
        value = completion_snapshot(session(rows), 1)['delivery']['artifacts'][0]
        self.assertEqual(value, {'path': '/original/report.md', 'source_seq': 2, 'workspace_root': '/original',
                                 'name': '报告 Report.md', 'description': 'Original {raw} 原文'})
        rows[2].data['display'].pop('workspace_root')
        self.assertNotIn('workspace_root', completion_snapshot(session(rows), 1)['delivery']['artifacts'][0])

    def test_artifact_list_is_bounded_without_losing_total_or_latest_reference(self):
        rows = [event('turn/start', 0, turn=1, run_id='run')]
        for index in range(34):
            rows.extend([event('tool/call', len(rows), call_id=str(index), name='present', status='running'),
                         event('tool/result', len(rows) + 1, call_id=str(index), name='present',
                               results=[{'tool_use_id': str(index), 'is_error': False}],
                               display={'workspace_root': '/original', 'files': [{'path': f'/original/{index % 33}.txt'}]})])
        rows.append(event('turn/end', len(rows), turn=1, run_id='run'))
        value = completion_snapshot(session(rows), 1)['delivery']
        self.assertEqual(value['artifact_count'], 33)
        self.assertEqual(len(value['artifacts']), 32)
        self.assertEqual(value['artifacts'][0]['source_seq'], 68)
