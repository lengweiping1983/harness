"""监督摘要的真实运行归属、工具证据、隐私边界和等待状态矩阵。"""
import asyncio
import json
import unittest

from harness.app.superagent_mission_status import dependencies, run_progress, waiting_reason
from harness.core.errors import HarnessError
from harness.core.runs import AgentRun
from harness.session.events import SessionEvent


class MissionProgressTests(unittest.IsolatedAsyncioTestCase):
    def events(self):
        return [
            SessionEvent('turn/start', 0, 100, {'turn': 1, 'run_id': 'first'}),
            SessionEvent('tool/call', 1, 110, {'turn': 1, 'run_id': 'first', 'name': 'bash', 'call_id': 'old',
                'status': 'running', 'input': {'command': 'secret-argument-sentinel'}}),
            SessionEvent('tool/result', 2, 120, {'turn': 1, 'run_id': 'first', 'name': 'bash', 'call_id': 'old',
                'results': [{'tool_use_id': 'old', 'is_error': False, 'content': 'secret-output-sentinel'}]}),
            SessionEvent('turn/end', 3, 130, {'turn': 1, 'run_id': 'first'}),
            SessionEvent('turn/start', 4, 200, {'turn': 2, 'run_id': 'second'}),
            SessionEvent('assistant/message', 5, 210, {'turn': 2, 'run_id': 'second', 'text': 'secret-message-sentinel'}),
            SessionEvent('tool/call', 6, 220, {'turn': 2, 'run_id': 'second', 'name': 'read', 'call_id': 'failed',
                'status': 'running', 'input': {'path': 'secret-file-sentinel'}}),
            SessionEvent('tool/result', 7, 230, {'turn': 2, 'run_id': 'second', 'name': 'read', 'call_id': 'failed',
                'results': [{'tool_use_id': 'failed', 'is_error': True, 'content': 'secret-error-sentinel'}]}),
            SessionEvent('tool/call', 8, 240, {'turn': 2, 'run_id': 'second', 'name': 'grep', 'call_id': 'pending',
                'status': 'running', 'input': {'pattern': 'secret-query-sentinel'}}),
        ]

    def test_tool_progress_is_derived_from_current_run_instead_of_unused_counter(self):
        record = AgentRun('second', session_id='shared', status='running', tool_calls=999, started_at=0.2)
        result = run_progress(record, self.events())
        self.assertEqual(result['tools'], {'started': 2, 'completed': 1, 'unconfirmed': 1, 'errors': 1,
                                          'active': ['grep'], 'active_truncated': False})
        self.assertEqual(result['last_activity'], {'seq': 8, 'time': 240, 'type': 'tool/call'})
        self.assertFalse(result['settled'])
        self.assertNotIn('secret-', json.dumps(result))

    def test_previous_run_in_reused_conversation_keeps_its_own_terminal_activity(self):
        result = run_progress(AgentRun('first', session_id='shared', status='completed', finished_at=0.13), self.events())
        self.assertEqual(result['last_activity'], {'seq': 3, 'time': 130, 'type': 'turn/end'})
        self.assertEqual(result['tools']['started'], 1)
        self.assertEqual(result['tools']['active'], [])
        self.assertTrue(result['settled'])
        self.assertNotIn('secret-', json.dumps(result))

    def test_queued_run_does_not_inherit_another_turns_progress(self):
        result = run_progress(AgentRun('queued', session_id='shared'), self.events())
        self.assertIsNone(result['last_activity'])
        self.assertEqual(result['tools']['started'], 0)
        self.assertEqual(result['tools']['active'], [])

    def test_duplicate_run_anchor_is_an_explicit_corrupt_state(self):
        events = self.events() + [SessionEvent('turn/start', 9, 250, {'turn': 3, 'run_id': 'second'})]
        with self.assertRaises(HarnessError) as caught:
            run_progress(AgentRun('second', status='running'), events)
        self.assertEqual(caught.exception.code, 'superagent/corrupt')

    def test_interrupted_or_terminal_tool_without_result_stays_unconfirmed(self):
        events = self.events()
        interrupted = events + [SessionEvent('turn/end', 9, 250, {'turn': 2, 'run_id': 'second'})]
        for status, scoped in (('running', interrupted), ('failed', events), ('interrupted', interrupted)):
            with self.subTest(status=status):
                result = run_progress(AgentRun('second', status=status), scoped)
                self.assertEqual(result['tools']['active'], [])
                self.assertEqual(result['tools']['started'], 2)
                self.assertEqual(result['tools']['completed'], 1)
                self.assertEqual(result['tools']['unconfirmed'], 1)

    def test_unpaired_or_duplicate_result_does_not_fabricate_completion(self):
        events = self.events()
        for data in ({'call_id': 'failed', 'name': 'grep'}, {'call_id': 'failed', 'name': 'read'}):
            with self.subTest(data=data):
                result = SessionEvent('tool/result', 9, 250, {'turn': 2, 'run_id': 'second', **data,
                    'results': [{'tool_use_id': 'failed', 'is_error': False, 'content': 'secret-output-sentinel'}]})
                progress = run_progress(AgentRun('second', status='running'), events + [result])
                self.assertEqual(progress['tools']['completed'], 0)
                self.assertEqual(progress['tools']['unconfirmed'], 2)
                self.assertNotIn('secret-', json.dumps(progress))

    def test_active_names_are_bounded_and_report_truncation(self):
        events = [SessionEvent('turn/start', 0, 100, {'turn': 1, 'run_id': 'many'})]
        events.extend(SessionEvent('tool/call', index + 1, 110, {'turn': 1, 'run_id': 'many',
            'call_id': str(index), 'name': 'x' * 129, 'status': 'running'}) for index in range(17))
        result = run_progress(AgentRun('many', status='running'), events)
        self.assertEqual(result['tools']['started'], 17)
        self.assertEqual(result['tools']['unconfirmed'], 17)
        self.assertEqual(result['tools']['active'], ['x' * 128] * 16)
        self.assertTrue(result['tools']['active_truncated'])

    async def test_terminal_run_cleanup_and_background_wait_have_distinct_meaning(self):
        mission = {'state': 'running'}
        job = {'state': 'accepted', 'role': 'work'}
        record = AgentRun('first', status='completed')
        record.finalization_task = asyncio.get_running_loop().create_future()
        def reason(background):
            return waiting_reason(mission, job, progress=run_progress(record, self.events()), unresolved=[],
                                  background=background, conversation_busy=False, at_capacity=False)
        self.assertEqual(reason(True), 'run-cleanup')
        record.finalization_task.set_result(None)
        self.assertEqual(reason(True), 'background-jobs')
        self.assertEqual(reason(False), 'delivery-capture')
        job['state'] = 'done'
        self.assertIsNone(reason(True))

    def test_dependency_detail_keeps_missing_creation_explicit(self):
        mission = {'plan': {'steps': [{'id': 'first'}, {'id': 'second'}]}}
        job = {'role': 'work', 'attempt': 1, 'dependencies': ['first', 'second']}
        first = {'id': 'first-job', 'role': 'work', 'node': 'first', 'attempt': 1, 'state': 'done'}
        self.assertEqual(dependencies(mission, job, [first]), [{'node': 'second', 'job_id': None, 'state': None}])
        first['state'] = 'accepted'
        self.assertEqual(dependencies(mission, job, [first])[0], {'node': 'first', 'job_id': 'first-job', 'state': 'accepted'})

    def test_waiting_matrix_preserves_capacity_queue_unknown_and_halted_boundaries(self):
        cases = [
            ('pending', 'running', None, True, False, False, 'dependencies'),
            ('pending', 'running', None, False, False, True, 'worker-capacity'),
            ('ready', 'running', None, False, True, True, 'conversation-busy'),
            ('ready', 'running', None, False, False, False, 'dispatch'),
            ('accepted', 'running', {'status': 'queued', 'settled': False}, False, False, False, 'run-queue'),
            ('accepted', 'running', {'status': 'running', 'settled': False}, False, False, False, 'run'),
            ('accepted', 'suppressed', {'status': 'stopping', 'settled': False}, False, False, False, 'run-stop'),
            ('accepted', 'running', {'status': 'failed', 'settled': True}, False, False, False, 'failure-settlement'),
            ('pending', 'suppressed', None, True, False, True, 'mission-halted'),
            ('importing', 'running', None, False, False, False, 'delivery-reconciliation'),
            ('submitting', 'running', None, False, False, False, 'submission-reconciliation'),
            ('unknown', 'unknown', {'status': 'running', 'settled': False}, False, False, False, 'result-reconciliation'),
        ]
        for state, mission_state, progress, blocked, busy, capacity, expected in cases:
            with self.subTest(state=state, mission=mission_state, expected=expected):
                self.assertEqual(waiting_reason({'state': mission_state}, {'state': state, 'role': 'work'}, progress=progress,
                    unresolved=['dependency'] if blocked else [], background=False, conversation_busy=busy, at_capacity=capacity), expected)
