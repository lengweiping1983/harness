"""目标级 Superagent 控制账本回归。"""
from __future__ import annotations

import tempfile
import json
import unittest
from pathlib import Path
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor

from harness.app.superagent_control import MissionControl
from harness.core.errors import HarnessError
from harness.core.language import current_language
from harness.i18n import tr


class MissionControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.control = MissionControl(Path(self.temp.name) / 'control.sqlite3',
                                      instance_id='instance', identity='config-v1')

    def tearDown(self):
        self.temp.cleanup()

    def test_commands_are_idempotent_and_conflicts_are_preserved(self):
        first = self.control.enqueue(command_id='request-1', mission_id='mission-1', action='pause')
        again = self.control.enqueue(command_id='request-1', mission_id='mission-1', action='pause')
        self.assertEqual(first['status'], again['status'])
        self.assertEqual(first['id'], again['id'])
        with self.assertRaises(HarnessError) as caught:
            self.control.enqueue(command_id='request-1', mission_id='mission-1', action='cancel')
        self.assertEqual(caught.exception.code, 'superagent/event-conflict')

    def test_unknown_mission_stays_queued_until_target_exists(self):
        self.control.enqueue(command_id='request-1', mission_id='mission-1', action='pause')
        self.assertEqual(self.control.reconcile(mission_exists=lambda _: False), [])
        self.assertEqual(self.control.command('request-1')['status'], 'queued')
        applied = self.control.reconcile(mission_exists=lambda _: True)
        self.assertEqual(applied[0]['status'], 'applied')
        self.assertEqual(self.control.mode('mission-1')['mode'], 'paused')

    def test_cancel_is_terminal_and_resume_is_rejected(self):
        self.control.enqueue(command_id='pause', mission_id='mission-1', action='pause')
        self.control.reconcile(mission_exists=lambda _: True)
        self.control.enqueue(command_id='resume', mission_id='mission-1', action='resume')
        self.control.reconcile(mission_exists=lambda _: True)
        self.assertEqual(self.control.mode('mission-1')['mode'], 'running')
        self.control.enqueue(command_id='cancel', mission_id='mission-1', action='cancel')
        self.control.reconcile(mission_exists=lambda _: True)
        self.assertEqual(self.control.mode('mission-1')['mode'], 'cancelled')
        self.control.enqueue(command_id='late-resume', mission_id='mission-1', action='resume')
        result = self.control.reconcile(mission_exists=lambda _: True)
        self.assertEqual(result[0]['status'], 'rejected')
        self.assertEqual(result[0]['result']['error'], 'control/terminal')
        self.assertEqual(self.control.mode('mission-1')['mode'], 'cancelled')

    def test_status_contains_audit_trail_and_revision(self):
        self.control.enqueue(command_id='request-1', mission_id='mission-1', action='pause')
        self.control.reconcile(mission_exists=lambda _: True)
        status = self.control.status()
        self.assertEqual(status['revision'], 1)
        self.assertEqual(status['missions'][0]['revision'], 1)
        self.assertEqual(status['commands'][0]['status'], 'applied')

    def test_wall_clock_rollback_does_not_reorder_control_intent(self):
        with patch('harness.app.superagent_control.time.time', side_effect=[100, 90, 80, 70]):
            self.control.enqueue(command_id='z-first', mission_id='mission', action='pause')
            self.control.enqueue(command_id='a-second', mission_id='mission', action='resume')
            rows = self.control.reconcile(mission_exists=lambda _: True)
        self.assertEqual([row['command_id'] for row in rows], ['z-first', 'a-second'])
        self.assertEqual(self.control.mode('mission')['mode'], 'running')

    def test_unknown_targets_do_not_starve_later_valid_control_or_reorder_one_target(self):
        for index in range(65):
            self.control.enqueue(command_id='unknown-' + str(index), mission_id='unknown-' + str(index), action='pause')
        self.control.enqueue(command_id='valid', mission_id='valid', action='pause')
        self.assertEqual(self.control.reconcile(mission_exists=lambda identity: identity == 'valid'), [])
        self.control.reconcile(mission_exists=lambda identity: identity == 'valid')
        self.assertEqual(self.control.command('valid')['status'], 'applied')
        self.assertEqual(self.control.command('unknown-0')['status'], 'queued')
        self.control.enqueue(command_id='late-resume', mission_id='unknown-0', action='resume')
        for _ in range(3):
            self.control.reconcile(mission_exists=lambda identity: identity in ('valid', 'unknown-0'))
        self.assertEqual(self.control.command('unknown-0')['result']['revision'], 1)
        self.assertEqual(self.control.command('late-resume')['result']['revision'], 2)
        self.assertEqual(self.control.mode('unknown-0')['mode'], 'running')

    def test_rejected_resume_reports_the_existing_revision_without_fabricating_a_change(self):
        self.control.enqueue(command_id='cancel', mission_id='mission', action='cancel')
        self.control.reconcile(mission_exists=lambda _: True)
        self.control.enqueue(command_id='resume', mission_id='mission', action='resume')
        self.control.reconcile(mission_exists=lambda _: True)
        self.assertEqual(self.control.command('resume')['result']['revision'], self.control.mode('mission')['revision'])
        self.assertEqual(self.control.mode('mission')['revision'], 1)

    def test_corrupt_command_is_rejected_on_read_duplicate_and_reconcile_without_repair(self):
        self.control.enqueue(command_id='original', mission_id='mission', action='pause')
        with self.control.db() as db:
            original = dict(db.execute('SELECT * FROM commands').fetchone())
        for values in ({'payload': '{"action":"cancel","action":"pause","mission_id":"mission"}'},
                {'action': 'cancel'}, {'status': 'applied', 'result': 'null'},
                {'created_at': 'not-a-time'}, {'result': '{"mission_id":"mission"}'}):
            with self.subTest(values=values):
                with self.control.db(write=True) as db:
                    db.execute('UPDATE commands SET ' + ','.join(key + '=?' for key in values), tuple(values.values()))
                before = self.control.path.read_bytes()
                for operation in (lambda: self.control.command('original'), self.control.status,
                        lambda: self.control.enqueue(command_id='original', mission_id='mission', action='pause')):
                    with self.assertRaises(HarnessError) as caught:
                        operation()
                    self.assertEqual(caught.exception.code, 'superagent/corrupt')
                    self.assertEqual(self.control.path.read_bytes(), before)
                if values.get('status') != 'applied':
                    with self.assertRaises(HarnessError):
                        self.control.reconcile(mission_exists=lambda _: True)
                    self.assertEqual(self.control.path.read_bytes(), before)
                with self.control.db(write=True) as db:
                    db.execute('UPDATE commands SET ' + ','.join(key + '=?' for key in original), tuple(original.values()))

    def test_mode_and_meta_corruption_are_not_treated_as_default_running(self):
        self.control.enqueue(command_id='pause', mission_id='mission', action='pause')
        self.control.reconcile(mission_exists=lambda _: True)
        for field, value in (('reason', 'operator/resume'), ('revision', -1), ('updated_at', 'invalid')):
            with self.subTest(field=field):
                with self.control.db() as db:
                    original = db.execute('SELECT ' + field + ' FROM mission_control').fetchone()[0]
                with self.control.db(write=True) as db:
                    db.execute('UPDATE mission_control SET ' + field + '=?', (value,))
                before = self.control.path.read_bytes()
                for operation in (lambda: self.control.mode('mission'), lambda: self.control.modes(['mission']), self.control.status):
                    with self.assertRaises(HarnessError):
                        operation()
                    self.assertEqual(self.control.path.read_bytes(), before)
                with self.control.db(write=True) as db:
                    db.execute('UPDATE mission_control SET ' + field + '=?', (original,))
        with self.control.db(write=True) as db:
            db.execute('UPDATE control_meta SET revision=-1')
        with self.assertRaises(HarnessError):
            self.control.status()

    def test_read_only_receipt_uses_original_identity_and_cannot_create_or_write(self):
        self.control.enqueue(command_id='pause', mission_id='mission', action='pause')
        before = self.control.path.read_bytes()
        read = MissionControl(self.control.path, instance_id='instance', identity='config-v1', read_only=True)
        self.assertEqual(read.command('pause'), self.control.command('pause'))
        with self.assertRaises(HarnessError):
            read.enqueue(command_id='resume', mission_id='mission', action='resume')
        self.assertEqual(self.control.path.read_bytes(), before)
        missing = Path(self.temp.name) / 'missing' / 'control.sqlite3'
        with self.assertRaises(HarnessError):
            MissionControl(missing, instance_id='instance', identity='config-v1', read_only=True)
        self.assertFalse(missing.parent.exists())
        with self.assertRaises(HarnessError):
            MissionControl(self.control.path, instance_id='different', identity='config-v1')
        self.assertEqual(self.control.path.read_bytes(), before)

    def test_status_keeps_global_totals_and_nested_receipts_isolated(self):
        for index in range(3):
            self.control.enqueue(command_id=str(index), mission_id=str(index), action='pause')
        self.control.reconcile(mission_exists=lambda _: True)
        result = self.control.status(limit=1)
        self.assertEqual(result['states'], {'applied': 3})
        self.assertEqual(result['commands_total'], 3)
        self.assertTrue(result['commands_has_more'])
        self.assertEqual(result['missions_total'], 3)
        result['commands'][0]['result']['mode'] = 'cancelled'
        self.assertEqual(self.control.command('2')['result']['mode'], 'paused')

    def test_large_valid_configuration_identity_is_not_limited_to_a_single_description(self):
        path = Path(self.temp.name) / 'large.sqlite3'
        identity = json.dumps({'description': 'x' * 8192})
        control = MissionControl(path, instance_id='large', identity=identity)
        control.enqueue(command_id='pause', mission_id='mission', action='pause')
        read = MissionControl(path, instance_id='large', identity=identity, read_only=True)
        self.assertEqual(read.command('pause')['status'], 'queued')

    def test_concurrent_operators_share_one_stable_control_receipt(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            receipts = list(pool.map(lambda _: self.control.enqueue(command_id='shared', mission_id='mission', action='pause'), range(4)))
        self.assertTrue(all(receipt == receipts[0] for receipt in receipts))
        self.assertEqual(self.control.status()['commands_total'], 1)
        self.control.reconcile(mission_exists=lambda _: True)
        with self.assertRaises(HarnessError) as caught:
            self.control.enqueue(command_id='shared', mission_id='mission', action='cancel')
        self.assertEqual(caught.exception.code, 'superagent/event-conflict')
        self.assertEqual(self.control.command('shared')['result']['mode'], 'paused')

    def test_full_history_keeps_old_receipts_and_rejects_new_intent_without_cleanup(self):
        with self.control.db(write=True) as db:
            db.executemany('INSERT INTO commands VALUES(?,?,?,?,?,?,?,?,?)', [
                (str(index), 'instance', 'mission', 'pause', json.dumps({'action': 'pause', 'mission_id': 'mission'}, sort_keys=True),
                 1, 'queued', None, None) for index in range(1024)])
        before = self.control.path.read_bytes()
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language), self.assertRaises(HarnessError) as caught:
                self.control.enqueue(command_id='overflow', mission_id='mission', action='pause')
            self.assertEqual(caught.exception.code, 'superagent/control-capacity')
            with current_language.set(language):
                self.assertIn(tr('superagent.control-capacity'), str(caught.exception))
        self.assertEqual(self.control.path.read_bytes(), before)
        self.assertEqual(self.control.enqueue(command_id='0', mission_id='mission', action='pause')['id'], '0')

    def test_async_existence_guards_are_rejected_without_executing_or_leaking_a_coroutine(self):
        self.control.enqueue(command_id='pause', mission_id='mission', action='pause')
        async def guard(_):
            raise AssertionError('异步准入不得被执行。')
        for callback in (guard, lambda identity: guard(identity), lambda _: 'yes'):
            with self.subTest(callback=callback), self.assertRaises(HarnessError):
                self.control.reconcile(mission_exists=callback)
        self.assertEqual(self.control.command('pause')['status'], 'queued')


if __name__ == '__main__':
    unittest.main()
