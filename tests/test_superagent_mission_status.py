"""监督摘要的真实运行归属、工具证据、隐私边界和等待状态矩阵。"""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest

from harness.app.superagent_run_evidence import run_progress
from harness.app.superagent_status import dependencies, waiting_reason, snapshot
from harness.app.superagent_coordinator_store import CoordinatorStore
from harness.core.errors import HarnessError
from harness.core.runs import AgentRun
from harness.session.events import SessionEvent


class MissionProgressTests(unittest.IsolatedAsyncioTestCase):
    def create_mission(self, uow, suffix, state='planning'):
        ticket = uow.tickets.create({'ticket_id':'ticket-' + suffix, 'mandate_id':'mandate-' + suffix,
            'origin_trigger_id':'trigger-' + suffix, 'title':'Status fixture'})
        return uow.missions.create({'mission_id':'mission-' + suffix, 'ticket_id':ticket['ticket_id'],
            'sequence':1, 'repo':'/fixture', 'base_commit':'a' * 40, 'state':state})

    def test_older_active_and_attention_missions_remain_visible_ahead_of_completed_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = CoordinatorStore(Path(temporary)/'superagent.sqlite3', instance_id='status', identity={})
            with store.unit_of_work(actor={}) as uow:
                self.create_mission(uow, 'active')
                self.create_mission(uow, 'attention', 'blocked')
                for index in range(35):
                    self.create_mission(uow, str(index), 'completed')
            current = snapshot(store)
            self.assertEqual([m['mission_id'] for m in current['missions']['recent'][:2]], ['mission-active','mission-attention'])
            self.assertTrue(current['missions']['has_more'])
            self.assertEqual(current['missions']['total'], 37)
            self.assertEqual(current['missions']['counts'], {'planning':1,'blocked':1,'completed':35})
            scoped = snapshot(store, mission_id='mission-0')
            self.assertEqual([m['mission_id'] for m in scoped['missions']['recent']], ['mission-0'])
            self.assertFalse(scoped['missions']['has_more'])

    def test_mission_plan_and_jobs_share_one_read_transaction_during_concurrent_planning(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'superagent.sqlite3'
            store = CoordinatorStore(path, instance_id='status', identity={})
            other = CoordinatorStore.open_existing(path, instance_id='status', identity={})
            with store.unit_of_work(actor={}) as uow:
                mission = self.create_mission(uow, 'concurrent')
            connect, seen, errors = store._connect, [], []
            def connected():
                db = connect()
                def trace(sql):
                    if not sql.startswith('SELECT * FROM jobs'):
                        return
                    db.set_trace_callback(None)
                    try:
                        with other.unit_of_work(actor={}) as uow:
                            uow.plans.create(mission['mission_id'], expected_mission_revision=1,
                                plan={'nodes':[{'node_id':'work','role':'work','profile_id':'reader','dependencies':[]}]},
                                acceptance={'kind':'single_worker_report','schema_version':1}, created_by_kind='host', created_by_ref='fixture')
                        seen.append(True)
                    except BaseException as exc:
                        errors.append(exc)
                db.set_trace_callback(trace)
                return db
            store._connect = connected
            try:
                before = snapshot(store)['missions']['recent'][0]
            finally:
                store._connect = connect
            self.assertEqual(errors, [])
            self.assertEqual(seen, [True])
            self.assertEqual((before['plan_revision'], before['jobs']), (0, []))
            after = snapshot(store)['missions']['recent'][0]
            self.assertEqual((after['plan_revision'], len(after['jobs'])), (1, 1))

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
        job = {'job_id': 'join'}
        edges = [{'job_id': 'join', 'depends_on_job_id': 'first'}, {'job_id': 'join', 'depends_on_job_id': 'second'}]
        first = {'job_id': 'first', 'state': 'done'}
        self.assertEqual(dependencies(job, [first], edges), [{'job_id': 'second', 'state': None}])
        first['state'] = 'running'
        self.assertEqual(dependencies(job, [first], edges)[0], {'job_id': 'first', 'state': 'running'})

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
            ('capturing', 'running', None, False, False, False, 'delivery-reconciliation'),
            ('submitting', 'running', None, False, False, False, 'submission-reconciliation'),
            ('unknown', 'unknown', {'status': 'running', 'settled': False}, False, False, False, 'result-reconciliation'),
        ]
        for state, mission_state, progress, blocked, busy, capacity, expected in cases:
            with self.subTest(state=state, mission=mission_state, expected=expected):
                self.assertEqual(waiting_reason({'state': mission_state}, {'state': state, 'role': 'work'}, progress=progress,
                    unresolved=['dependency'] if blocked else [], background=False, conversation_busy=busy, at_capacity=capacity), expected)
