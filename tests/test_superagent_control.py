"""目标级 Superagent 控制账本回归。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from harness.app.superagent_control import MissionControl
from harness.core.errors import HarnessError


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


if __name__ == '__main__':
    unittest.main()
