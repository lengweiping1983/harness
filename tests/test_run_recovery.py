"""验证重启恢复只处理权威运行记录，不重放未知副作用。"""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from harness.app.runtime import RunController
from harness.core.run_storage import RunStorage
from harness.core.runs import AgentRun


class RunRecoveryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.home = Path(directory.name)
        self.storage = RunStorage(self.home / 'runs.sqlite3')
        self.session = SimpleNamespace(steering=SimpleNamespace(records={}))
        self.host = SimpleNamespace(config=SimpleNamespace(home=self.home),
            sessions=SimpleNamespace(has=Mock(return_value=True)),
            plan=SimpleNamespace(is_active_session_id=Mock(return_value=False)),
            questions=SimpleNamespace(pending=Mock(return_value=False)),
            get_session=Mock(return_value=self.session), run_async=Mock())

    def save(self, *records, paused=False, order=None):
        with self.storage.db() as db:
            for record in records:
                self.storage.write(db, record, initial=True)
            if paused:
                db.execute('INSERT INTO queue_controls VALUES(?,?,?,?)', ('session', 1, 'user', 1.0))
            if order is not None:
                db.execute('INSERT INTO queue_orders VALUES(?,?)', ('session', json.dumps(order)))

    def stored(self, identity):
        with self.storage.db() as db:
            return db.execute('SELECT data FROM runs WHERE id=?', (identity,)).fetchone()[0]

    def test_summary_and_recovery_preserve_resource_without_changing_version(self):
        from harness.core.language import current_language
        from harness.core.runs import run_failure
        from harness.app.recovery import recovery_preview
        self.session.events = []
        with current_language.set('zh-CN'):
            error = run_failure('run/incomplete', 'ui.run.incomplete', reason='max_steps')
        self.save(AgentRun(id='failed', session_id='session', status='failed', error=error))
        controller = RunController(self.host)
        self.host.runtime = controller
        versions = []
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                summary = controller.history('session', summary=True)['runs'][0]
                self.assertEqual(summary['error']['copy'], error['copy'])
                preview = recovery_preview(self.host, 'session', 'failed')
                self.assertEqual(preview['error_copy'], error['copy'])
                versions.append(preview['version'])
                summary['error']['copy']['params']['reason'] = 'external-change'
                self.assertEqual(controller.get('failed').error['copy']['params']['reason'], 'max_steps')
        self.assertEqual(len(set(versions)), 1)
        self.host.run_async.assert_not_called()

    def test_nested_exception_copy_survives_summary_and_preview(self):
        from harness.core.errors import HarnessError
        from harness.app.recovery import recovery_preview
        self.session.events = []
        error = HarnessError('Original diagnostic 原文', copy={'key': 'ui.run.executionFailed'}).to_dict()
        self.save(AgentRun(id='failed', session_id='session', status='failed', error=error))
        controller = RunController(self.host)
        self.host.runtime = controller
        self.assertEqual(controller.history('session', summary=True)['runs'][0]['error']['copy'], error['details']['copy'])
        self.assertEqual(recovery_preview(self.host, 'session', 'failed')['error_copy'], error['details']['copy'])

    def test_recovery_requires_matching_ordered_tool_evidence(self):
        from harness.app.recovery import recovery_preview
        from harness.session.events import SessionEvent
        from harness.session.turn_delivery import delivery_summary
        self.save(AgentRun(id='failed', session_id='session', status='failed'))
        self.host.runtime = RunController(self.host)
        for variant in ('matched', 'wrong-name', 'wrong-call', 'duplicate', 'reversed-seq', 'missing-start', 'foreign-run', 'not-executed'):
            with self.subTest(variant=variant):
                start = SessionEvent('turn/start', 0, 1, {'turn': 1, 'run_id': 'failed'})
                call = SessionEvent('tool/call', 3 if variant == 'reversed-seq' else 1, 2,
                    {'call_id': 'call', 'name': 'write', 'status': 'running', 'effect': 'external_write'})
                result = SessionEvent('tool/result', 2, 3, {'call_id': 'other' if variant == 'wrong-call' else 'call',
                    'name': 'read' if variant == 'wrong-name' else 'write',
                    'results': [{'tool_use_id': 'call', 'is_error': False, 'content': 'Original receipt 原文'}] * (2 if variant == 'duplicate' else 1)})
                if variant == 'foreign-run':
                    result.data['run_id'] = 'foreign'
                if variant == 'not-executed':
                    result.data['display'] = {'not_executed': True}
                self.session.events = [start, *([] if variant in ('missing-start', 'not-executed') else [call]),
                                       result, SessionEvent('turn/end', 5, 4, {'turn': 1, 'run_id': 'failed'})]
                preview = recovery_preview(self.host, 'session', 'failed')
                expected = 'reported_success' if variant == 'matched' else 'not_executed' if variant == 'not-executed' else 'unknown'
                self.assertEqual(preview['tools'][0]['status'], expected)
                self.assertEqual(preview['verification_required'], expected == 'unknown')
                if variant not in ('foreign-run', 'not-executed'):
                    self.assertEqual(delivery_summary(self.session.events)['unknown_tools'], int(expected == 'unknown'))
        self.host.run_async.assert_not_called()

    def test_unpaused_incomplete_runs_are_interrupted_without_execution(self):
        records = [AgentRun(id=status, session_id='session', status=status) for status in ('queued', 'running', 'stopping')]
        self.save(*records)
        controller = RunController(self.host)
        for original in records:
            recovered = controller.get(original.id)
            self.assertEqual(recovered.status, 'interrupted')
            self.assertEqual(recovered.error['code'], 'run/restarted')
            self.assertIsNotNone(recovered.finished_at)
            self.assertTrue(recovered.settled)
            self.assertEqual(json.loads(self.stored(original.id))['status'], 'interrupted')
        self.assertEqual(controller._pending, {})
        self.assertEqual(controller.active, {})
        self.host.run_async.assert_not_called()

    def test_only_never_started_paused_queue_is_preserved_in_saved_order(self):
        records = [AgentRun(id=identity, session_id='session', goal_id='goal' if identity == 'goal' else None)
                   for identity in ('first', 'second', 'goal')]
        started = AgentRun(id='started', session_id='session', started_at=1.0)
        self.save(*records, started, paused=True, order=['goal', 'second', 'first'])
        controller = RunController(self.host)
        self.assertEqual(list(controller._pending['session']), ['second', 'first', 'goal'])
        self.assertEqual(controller.get('started').status, 'interrupted')
        for record in records:
            self.assertEqual(controller.get(record.id).status, 'queued')
            self.assertIsNone(controller.get(record.id).task)
            self.assertFalse(controller._can_start(controller.get(record.id)))
        self.host.run_async.assert_not_called()

    def test_already_accepted_steering_is_not_restored_as_queued_work(self):
        record = AgentRun(id='steered', session_id='session')
        self.save(record, paused=True)
        self.session.steering.records['queue-steer:steered'] = object()
        controller = RunController(self.host)
        restored = controller.get(record.id)
        self.assertEqual(restored.status, 'interrupted')
        self.assertEqual(restored.error['code'], 'run/steered')
        self.assertEqual(controller._pending, {})
        self.host.run_async.assert_not_called()

    def test_invalid_queue_order_is_rejected_before_recovery_writes(self):
        for order in ('not-an-array', ['duplicate', 'duplicate'], [None]):
            with self.subTest(order=order):
                with self.storage.db() as db:
                    db.execute('DELETE FROM queue_orders')
                    db.execute('DELETE FROM runs')
                record = AgentRun(id='run', session_id='session', status='running')
                self.save(record, order=order)
                before = self.stored(record.id)
                with self.assertRaises(ValueError):
                    RunController(self.host)
                self.assertEqual(self.stored(record.id), before)
        self.host.run_async.assert_not_called()

    def test_main_recovery_does_not_mutate_member_or_terminal_records(self):
        member = AgentRun(id='member', parent_session_id='parent', status='running')
        completed = AgentRun(id='completed', session_id='session', status='completed', result={'text': '已核对'})
        self.save(member, completed)
        before = {record.id: self.stored(record.id) for record in (member, completed)}
        controller = RunController(self.host)
        self.assertEqual(controller.get(completed.id).result, completed.result)
        for identity, raw in before.items():
            self.assertEqual(self.stored(identity), raw)
        self.assertEqual(controller._pending, {})
        self.host.run_async.assert_not_called()
