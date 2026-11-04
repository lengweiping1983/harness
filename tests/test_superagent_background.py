"""后台拒绝、未知副作用、输出活性与恢复取消使用同一原始证据。"""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from harness.app.superagent_background import background_invocation, background_status
from harness.app.superagent_control import MissionControl
from harness.app.superagent_mission_control import MissionControlRuntime
from harness.app.superagent_missions import SuperagentMissions
from harness.app.superagent_watchdog import MissionWatchdog
from harness.core.runs import AgentRun
from harness.session.events import SessionEvent
from harness.session.tool_activity import tool_invocations
from harness.tools.builtin.job_tools import JobRecord, JobRegistry
from harness.tools.builtin.shell_tools import build_shell_tools
from harness.tools.registry import ToolRunContext


class BackgroundEvidenceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.registry = JobRegistry()
        self.events = []
        self.record = AgentRun('original', session_id='owner', status='completed')
        self.job = {'id': 'work', 'role': 'work', 'state': 'accepted',
                    'data': {'session_id': 'owner', 'payload': 'original'}}
        self.host = SimpleNamespace(jobs=self.registry, runtime=SimpleNamespace(active={}),
                                    get_session=lambda _: SimpleNamespace(events=self.events))
        self.coordinator = SuperagentMissions.__new__(SuperagentMissions)
        self.coordinator.harness = self.host
        self.coordinator.dispatcher = SimpleNamespace(harness=self.host)
        self.coordinator._record = Mock(return_value=self.record)
        self.coordinator._submission = Mock(return_value=self.record)

    def receipt(self, display, *, requested=True, error=False, duplicate=False):
        self.events[:] = [
            SessionEvent('turn/start', 0, 100, {'turn': 1, 'run_id': 'original'}),
            SessionEvent('tool/call', 1, 110, {'name': 'bash', 'call_id': 'call', 'status': 'running',
                'input': {'command': 'private command', 'run_in_background': requested}}),
            SessionEvent('tool/result', 2, 120, {'name': 'bash', 'call_id': 'call', 'display': display,
                'results': [{'tool_use_id': 'call', 'is_error': error, 'content': 'private output'}]}),
        ]
        if duplicate:
            self.events.append(SessionEvent('tool/result', 3, 130, self.events[-1].data))
        self.events.append(SessionEvent('turn/end', len(self.events), 140, {'turn': 1, 'run_id': 'original'}))
        return tool_invocations(self.events)[0]

    def live(self, *, owner='owner', status='running'):
        job = JobRecord('background', 'bash', 'private label', owner, 100, status=status)
        self.registry._jobs[job.id] = job
        return job

    async def test_real_rejected_background_start_does_not_hold_delivery_or_cancel_recovery(self):
        tool = build_shell_tools(registry=self.registry)[0]
        arguments = {'command': 'must never execute', 'cwd': 'missing', 'run_in_background': True}
        value = await tool.execute(arguments,
                                   ToolRunContext('call', 'owner', str(self.root), 1, 1))
        self.assertEqual(value['execution_state'], 'not-started')
        call = self.receipt(tool.describe(arguments, value), error=True)
        self.assertEqual(self.registry._jobs, {})
        self.assertTrue(background_invocation(call))
        self.assertFalse(self.coordinator._background_jobs_active(self.job, record=self.record))
        self.assertTrue(MissionControlRuntime(self.coordinator)._background_confirmed(self.host, 'owner', call))
        self.live(status='completed')
        self.receipt({'background': True, 'job_id': 'background'})
        self.assertFalse(self.coordinator._background_jobs_active(self.job, record=self.record))

    def test_ambiguous_results_keep_side_effects_unknown(self):
        for display, error, duplicate in (
                ({'execution_state': 'not-started'}, False, False),
                ({'error': True}, True, False),
                ({'not_executed': True}, True, True),
                ({'not_executed': True, 'job_id': 'background'}, True, False),
                ({'background': True}, False, False)):
            with self.subTest(display=display, duplicate=duplicate):
                call = self.receipt(display, error=error, duplicate=duplicate)
                self.assertFalse(background_status(self.registry, 'owner', call)['settled'])
                self.assertTrue(self.coordinator._background_jobs_active(self.job, record=self.record))

    def test_redundant_completion_never_overrides_canonical_rejection(self):
        self.receipt({'not_executed': True}, error=True)
        self.events.append(SessionEvent('tool/call', 5, 150, {'name': 'bash', 'call_id': 'call',
            'status': 'error', 'display': {'background': True, 'job_id': 'invented'}}))
        call = tool_invocations(self.events)[0]
        self.assertEqual(call['display']['job_id'], 'invented')
        self.assertTrue(background_status(self.registry, 'owner', call)['settled'])

    def test_conflicting_promoted_results_never_confirm_foreground_completion(self):
        for reverse in (False, True):
            with self.subTest(reverse=reverse):
                displays = [{'execution_state': 'exited', 'exit_code': 0},
                            {'background': True, 'promoted': True, 'job_id': 'lost'}]
                if reverse:
                    displays.reverse()
                self.receipt(displays[0], requested=False)
                self.events.insert(-1, SessionEvent('tool/result', 3, 130, {
                    **self.events[2].data, 'display': displays[1]}))
                call = tool_invocations(self.events)[0]
                self.assertTrue(background_invocation(call))
                self.assertFalse(background_status(self.registry, 'owner', call)['confirmed'])
                self.assertTrue(self.coordinator._background_jobs_active(self.job, record=self.record))
                self.coordinator.store = SimpleNamespace(snapshot=lambda _: ({'state': 'cancelled'}, [self.job]))
                runtime = MissionControlRuntime(self.coordinator)
                runtime._cancel('mission')
                self.assertEqual(runtime.stopping('mission')['state'], 'unknown')

    def test_background_output_is_activity_without_reading_or_consuming_it(self):
        live = self.live()
        self.receipt({'background': True, 'job_id': live.id})
        mission = {'id': 'mission', 'state': 'running', 'revision': 1, 'round': 1}
        watchdog = MissionWatchdog(self.root / 'watchdog.sqlite3', instance_id='team', identity='config',
            limits={'stale_after': 10, 'max_stale_ticks': 2, 'action': 'block'})
        before = self.coordinator._watchdog_activity([self.job])
        watchdog.observe(mission, [], activity=before, now=100)
        self.assertEqual(watchdog.observe(mission, [], activity=before, now=110)['state'], 'stalled')
        live.stdout.append_text('private output 独立增量')
        after = self.coordinator._watchdog_activity([self.job])
        self.assertNotEqual(before, after)
        self.assertNotIn('private', json.dumps(after))
        self.assertEqual(live.cursors, {'stdout': 0, 'stderr': 0})
        self.assertEqual(watchdog.observe(mission, [], activity=after, now=120)['state'], 'healthy')
        live.stderr.append_text('private diagnostic')
        changed = self.coordinator._watchdog_activity([self.job])
        self.assertNotEqual(changed, after)
        self.assertEqual(watchdog.observe(mission, [], activity=changed, now=130)['state'], 'healthy')
        self.assertEqual(watchdog.observe(mission, [], activity=changed, now=150)['state'], 'blocked')

    def test_foreign_owner_or_missing_live_handle_cannot_manufacture_activity(self):
        live = self.live(owner='another-owner')
        self.receipt({'background': True, 'job_id': live.id})
        before = self.coordinator._watchdog_activity([self.job])
        live.stdout.append_text('foreign progress')
        self.assertEqual(before, self.coordinator._watchdog_activity([self.job]))
        self.registry._jobs.clear()
        self.registry.archive = Mock()
        self.registry.archive.output.side_effect = LookupError(live.id)
        self.assertEqual(before, self.coordinator._watchdog_activity([self.job]))
        self.registry.archive.output.assert_not_called()
        self.assertTrue(self.coordinator._background_jobs_active(self.job, record=self.record))

    def test_promoted_background_remains_unknown_during_restart_cancellation(self):
        self.receipt({'background': True, 'promoted': True, 'job_id': 'lost'}, requested=False)
        self.coordinator.store = SimpleNamespace(snapshot=lambda _: ({'state': 'cancelled'}, [self.job]))
        runtime = MissionControlRuntime(self.coordinator)
        runtime._cancel('mission')
        self.assertEqual(runtime.stopping('mission')['state'], 'unknown')
        self.assertTrue(runtime.stopping('mission')['unconfirmed_execution'])

    def test_applied_terminal_cancellation_is_recovered_without_replaying_stop(self):
        self.receipt({'background': True, 'job_id': 'lost'})
        path = self.root / 'control.sqlite3'
        control = MissionControl(path, instance_id='team', identity='config')
        control.enqueue(command_id='cancel', mission_id='mission', action='cancel')
        control.reconcile(mission_exists=lambda _: True)
        self.assertEqual(control.command('cancel')['status'], 'applied')
        self.coordinator.store = SimpleNamespace(snapshot=Mock(return_value=({'state': 'cancelled'}, [self.job])),
                                                 active_missions=lambda: [])
        self.coordinator._mission_exists = lambda _: True
        self.coordinator.control = MissionControl(path, instance_id='team', identity='config')
        runtime = MissionControlRuntime(self.coordinator)
        with unittest.mock.patch.object(self.registry, 'kill', side_effect=AssertionError('重放旧停止')):
            runtime.sync(recover=True)
            self.assertEqual(runtime.stopping('mission')['state'], 'unknown')
            self.assertTrue(runtime.uncertain())
            runtime.sync(recover=True)
        self.coordinator.store.snapshot.assert_called_once_with('mission')

    def test_archive_requires_terminal_without_cleanup_error(self):
        self.registry.archive = Mock()
        call = self.receipt({'background': True, 'job_id': 'archived'})
        for status, cleanup, settled in (('unavailable', None, False), ('completed', 'original failure', False),
                                        ('completed', None, True), ('failed', None, True)):
            with self.subTest(status=status, cleanup=cleanup):
                self.registry.archive.output.return_value = {'job': {'status': status, 'cleanupError': cleanup}}
                self.assertEqual(background_status(self.registry, 'owner', call)['settled'], settled)
        self.registry.archive.output.assert_called_with('owner', 'archived', limit=1)
