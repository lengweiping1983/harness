"""验证长任务 Watchdog 的停滞证据、持久化和配置边界。"""
import tempfile
import unittest
import math
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from harness.app.superagent_control import MissionControl
from harness.app.superagent_missions import SuperagentMissions
from harness.app.superagent_watchdog import MissionWatchdog, validate_watchdog
from harness.core.errors import HarnessError
from tests import test_superagent_mission_snapshots as snapshots


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


class WatchdogCoordinatorTests(unittest.IsolatedAsyncioTestCase):
    setUp = snapshots.MissionSnapshotTests.setUp
    add_mission = snapshots.MissionSnapshotTests.add_mission

    def coordinator(self):
        router = SuperagentMissions.__new__(SuperagentMissions)
        router.store, router.inbox = self.store, self.inbox
        router.control = MissionControl(self.root / 'control.sqlite3', instance_id='team', identity='config')
        router.watchdog = MissionWatchdog(self.root / 'watchdog.sqlite3', instance_id='team', identity='config',
            limits={'stale_after': 10, 'max_stale_ticks': 2, 'action': 'block'})
        router._watchdog_next_sample, router._watchdog_cache = {}, {}
        router.control_runtime, router.guidance = None, None
        router._open, router._collect_missions = Mock(), Mock()
        router._allowed, router._schedule, router._advance_job = Mock(return_value=True), Mock(), AsyncMock()
        router.worker_factory = SimpleNamespace(status=lambda: {})
        router.dispatcher = SimpleNamespace(harness=SimpleNamespace(jobs=SimpleNamespace(active_owners=lambda: set()),
            runtime=SimpleNamespace(records=SimpleNamespace(active={}), active={})), max_active=3)
        router.harness = router.dispatcher.harness
        return router

    async def sample(self, router, stamp):
        with patch('harness.app.superagent_missions.time.monotonic', return_value=stamp), \
                patch('harness.app.superagent_watchdog.time.time', return_value=stamp):
            await router._tick()

    async def test_resume_after_restart_starts_a_new_window_then_detects_real_stall(self):
        router = self.coordinator()
        await self.sample(router, 100)
        await self.sample(router, 110)
        self.assertEqual(router.watchdog.get(self.identity)['state'], 'stalled')
        router.control.enqueue(command_id='pause', mission_id=self.identity, action='pause')
        router.control.reconcile(mission_exists=lambda _: True)
        await self.sample(router, 1000)
        self.assertEqual(router.watchdog.get(self.identity)['last_seen'], 110)
        reopened = self.coordinator()
        self.assertEqual(reopened.control.mode(self.identity)['mode'], 'paused')
        await self.sample(reopened, 2000)
        reopened.control.enqueue(command_id='resume', mission_id=self.identity, action='resume')
        reopened.control.reconcile(mission_exists=lambda _: True)
        await self.sample(reopened, 2001)
        self.assertEqual(self.store.mission(self.identity)['state'], 'running')
        self.assertEqual(reopened.watchdog.get(self.identity)['state'], 'healthy')
        await self.sample(reopened, 2011)
        self.assertEqual(reopened.watchdog.get(self.identity)['state'], 'stalled')
        await self.sample(reopened, 2021)
        self.assertEqual(self.store.mission(self.identity)['reason'], 'watchdog/stalled')
        self.assertEqual(reopened.status()['watchdog']['total'], 0)
        history = reopened.status()['missions']['recent'][0]['watchdog']
        self.assertFalse(history['monitoring'])
        self.assertEqual(history['mission_state'], 'blocked')

    async def test_terminal_and_paused_goals_leave_current_alerts_but_keep_history(self):
        router = self.coordinator()
        identities = [self.add_mission('old-' + str(index)) for index in range(35)]
        for identity in [*identities, self.identity]:
            mission = self.store.mission(identity)
            router.watchdog.observe(mission, [], now=100)
            router._watchdog_cache[identity] = router.watchdog.observe(mission, [], now=110)
        for index, identity in enumerate(identities):
            self.store.update(identity, before='running', state=('completed', 'cancelled', 'blocked')[index % 3])
        status = router.status()
        self.assertEqual(status['watchdog']['total'], 1)
        self.assertEqual([item['mission'] for item in status['watchdog']['items']], [self.identity])
        router.control.enqueue(command_id='pause', mission_id=self.identity, action='pause')
        router.control.reconcile(mission_exists=lambda _: True)
        self.assertEqual(router.status()['watchdog']['total'], 0)
        self.assertFalse(router.status()['missions']['recent'][0]['watchdog']['monitoring'])
        reopened = self.coordinator()
        self.assertEqual(reopened.status()['watchdog']['total'], 0)
        self.assertEqual(reopened.watchdog.status()['states']['stalled'], 36)


if __name__ == '__main__':
    unittest.main()
