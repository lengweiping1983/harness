"""验证长任务 Watchdog 的停滞证据、持久化和配置边界。"""
import tempfile
import unittest
import math
from pathlib import Path

from harness.app.superagent_watchdog import MissionWatchdog, validate_watchdog
from harness.core.errors import HarnessError


class SuperagentWatchdogTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / 'watchdog.sqlite3'
        self.mission = {'id': 'mission_1', 'revision': 1, 'state': 'running', 'round': 1, 'reason': None}

    def test_stall_is_sampled_and_survives_restart(self):
        limits = {'stale_after': 10, 'max_stale_ticks': 2, 'action': 'attention'}
        watchdog = MissionWatchdog(self.path, instance_id='team', identity='identity', limits=limits)
        self.assertEqual(watchdog.observe(self.mission, [], now=0)['state'], 'healthy')
        self.assertEqual(watchdog.observe(self.mission, [], now=9)['stagnant_ticks'], 0)
        self.assertEqual(watchdog.observe(self.mission, [], now=10)['state'], 'stalled')
        self.assertEqual(watchdog.observe(self.mission, [], now=20)['stagnant_ticks'], 2)
        reopened = MissionWatchdog(self.path, instance_id='team', identity='identity', limits=limits)
        self.assertEqual(reopened.get('mission_1')['reason'], 'watchdog/stalled')
        self.assertEqual(reopened.status()['states']['stalled'], 1)

    def test_block_action_requires_repeated_stale_windows(self):
        limits = {'stale_after': 1, 'max_stale_ticks': 2, 'action': 'block'}
        watchdog = MissionWatchdog(self.path, instance_id='team', identity='identity', limits=limits)
        watchdog.observe(self.mission, [], now=0)
        self.assertEqual(watchdog.observe(self.mission, [], now=1)['state'], 'stalled')
        self.assertEqual(watchdog.observe(self.mission, [], now=2)['state'], 'blocked')

    def test_progress_resets_stall_without_replaying_work(self):
        limits = {'stale_after': 1, 'max_stale_ticks': 2, 'action': 'block'}
        watchdog = MissionWatchdog(self.path, instance_id='team', identity='identity', limits=limits)
        watchdog.observe(self.mission, [], now=0)
        watchdog.observe(self.mission, [], now=1)
        changed = {**self.mission, 'revision': 2}
        current = watchdog.observe(changed, [], now=2)
        self.assertEqual(current['state'], 'healthy')
        self.assertEqual(current['stagnant_ticks'], 0)
        self.assertIsNone(current['reason'])

    def test_clock_rollback_does_not_move_last_seen_backwards(self):
        limits = {'stale_after': 10, 'max_stale_ticks': 2, 'action': 'attention'}
        watchdog = MissionWatchdog(self.path, instance_id='team', identity='identity', limits=limits)
        watchdog.observe(self.mission, [], now=20)
        current = watchdog.observe(self.mission, [], now=5)
        self.assertEqual(current['last_seen'], 20)
        self.assertEqual(current['last_progress'], 20)

    def test_confirmed_activity_resets_stagnation(self):
        limits = {'stale_after': 1, 'max_stale_ticks': 2, 'action': 'block'}
        watchdog = MissionWatchdog(self.path, instance_id='team', identity='identity', limits=limits)
        watchdog.observe(self.mission, [], activity={'last_event': 1}, now=0)
        watchdog.observe(self.mission, [], activity={'last_event': 1}, now=1)
        current = watchdog.observe(self.mission, [], activity={'last_event': 2}, now=2)
        self.assertEqual(current['state'], 'healthy')
        self.assertEqual(current['stagnant_ticks'], 0)

    def test_configuration_is_strict(self):
        with self.assertRaises(HarnessError):
            validate_watchdog({'stale_after': 0, 'max_stale_ticks': 1, 'action': 'attention'})
        with self.assertRaises(HarnessError):
            validate_watchdog({'stale_after': 1, 'max_stale_ticks': 1, 'action': 'retry'})
        with self.assertRaises(HarnessError):
            validate_watchdog({'stale_after': math.inf, 'max_stale_ticks': 1, 'action': 'attention'})


if __name__ == '__main__':
    unittest.main()
