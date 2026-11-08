"""原生后台拒绝、未知副作用、输出活性和精确停止的业务回归。"""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from harness.app.superagent_background import background_invocation, background_status
from harness.app.superagent_executor import HarnessEvidenceProvider, HarnessExecutorAdapter
from harness.core.errors import HarnessError
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
        self.registry, self.events = JobRegistry(), []
        self.record = AgentRun('original', session_id='owner', status='completed', finished_at=1)
        self.host = SimpleNamespace(jobs=self.registry, runtime=SimpleNamespace(get=lambda _: self.record, active={}, closed=False), _close_task=None,
                                    get_session=lambda _: SimpleNamespace(events=self.events))
        self.host.worktrees = object()
        self.host.sessions = SimpleNamespace(root=self.root)
        self.host.config = SimpleNamespace(**{name:False for name in ('enable_agents', 'enable_skills', 'enable_goal', 'enable_plan_mode', 'enable_subagent', 'enable_ralph')})
        self.evidence = HarnessEvidenceProvider(self.host)

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

    def unsettled(self):
        return self.evidence.inspect('owner', 'original')['unsettled_background']

    async def test_real_rejected_background_start_does_not_hold_delivery_or_cancel_recovery(self):
        tool = build_shell_tools(registry=self.registry)[0]
        arguments = {'command': 'must never execute', 'cwd': 'missing', 'run_in_background': True}
        value = await tool.execute(arguments, ToolRunContext('call', 'owner', str(self.root), 1, 1))
        self.assertEqual(value['execution_state'], 'not-started')
        call = self.receipt(tool.describe(arguments, value), error=True)
        self.assertEqual(self.registry._jobs, {})
        self.assertTrue(background_invocation(call))
        self.assertEqual(self.unsettled(), 0)
        adapter = object.__new__(HarnessExecutorAdapter)
        adapter.harness, adapter.closed = self.host, False
        result = await adapter.stop({'session_id': 'owner'}, {'run_id': 'original', 'session_id': 'owner'})
        self.assertTrue(result['run_settled'])
        self.live(status='completed')
        self.receipt({'background': True, 'job_id': 'background'})
        self.assertEqual(self.unsettled(), 0)

    def test_ambiguous_results_keep_side_effects_unknown(self):
        for display, error, duplicate in (({'execution_state': 'not-started'}, False, False),
                ({'error': True}, True, False), ({'not_executed': True}, True, True),
                ({'not_executed': True, 'job_id': 'background'}, True, False), ({'background': True}, False, False)):
            with self.subTest(display=display, duplicate=duplicate):
                call = self.receipt(display, error=error, duplicate=duplicate)
                self.assertFalse(background_status(self.registry, 'owner', call)['settled'])
                self.assertEqual(self.unsettled(), 1)

    def test_redundant_completion_never_overrides_canonical_rejection(self):
        self.receipt({'not_executed': True}, error=True)
        self.events.append(SessionEvent('tool/call', 5, 150, {'name': 'bash', 'call_id': 'call',
            'status': 'error', 'display': {'background': True, 'job_id': 'invented'}}))
        call = tool_invocations(self.events)[0]
        self.assertEqual(call['display']['job_id'], 'invented')
        self.assertTrue(background_status(self.registry, 'owner', call)['settled'])
        self.assertEqual(self.unsettled(), 0)

    async def test_conflicting_promoted_results_never_confirm_foreground_completion(self):
        for reverse in (False, True):
            displays = [{'execution_state': 'exited', 'exit_code': 0}, {'background': True, 'promoted': True, 'job_id': 'lost'}]
            if reverse:
                displays.reverse()
            self.receipt(displays[0], requested=False)
            self.events.insert(-1, SessionEvent('tool/result', 3, 130, {**self.events[2].data, 'display': displays[1]}))
            call = tool_invocations(self.events)[0]
            self.assertTrue(background_invocation(call))
            self.assertFalse(background_status(self.registry, 'owner', call)['confirmed'])
            self.assertEqual(self.unsettled(), 1)
            await self.refuses_stop()

    def test_background_output_is_activity_without_reading_or_consuming_it(self):
        live = self.live()
        call = self.receipt({'background': True, 'job_id': live.id})
        before = background_status(self.registry, 'owner', call, archived=False)
        live.stdout.append_text('private output 独立增量')
        after = background_status(self.registry, 'owner', call, archived=False)
        self.assertNotEqual(before, after)
        self.assertNotIn('private', json.dumps(after))
        self.assertEqual(live.cursors, {'stdout': 0, 'stderr': 0})
        live.stderr.append_text('private diagnostic')
        self.assertNotEqual(after, background_status(self.registry, 'owner', call, archived=False))
        self.assertEqual(live.cursors, {'stdout': 0, 'stderr': 0})
        self.assertEqual(self.unsettled(), 1)

    def test_foreign_owner_or_missing_live_handle_cannot_manufacture_activity(self):
        live = self.live(owner='another-owner')
        call = self.receipt({'background': True, 'job_id': live.id})
        before = background_status(self.registry, 'owner', call, archived=False)
        live.stdout.append_text('foreign progress')
        self.assertEqual(before, background_status(self.registry, 'owner', call, archived=False))
        self.registry._jobs.clear()
        self.registry.archive = Mock()
        self.registry.archive.output.side_effect = LookupError(live.id)
        self.assertEqual(before, background_status(self.registry, 'owner', call, archived=False))
        self.registry.archive.output.assert_not_called()
        self.assertEqual(self.unsettled(), 1)

    async def refuses_stop(self):
        adapter = object.__new__(HarnessExecutorAdapter)
        adapter.harness, adapter.closed = self.host, False
        with patch.object(self.registry, 'kill', side_effect=AssertionError('不得重放未知停止')):
            with self.assertRaises(HarnessError) as caught:
                await adapter.stop({'session_id': 'owner'}, {'run_id': 'original', 'session_id': 'owner'})
        self.assertEqual(caught.exception.code, 'superagent/background-unknown')

    async def test_promoted_background_remains_unknown_during_restart_cancellation(self):
        self.receipt({'background': True, 'promoted': True, 'job_id': 'lost'}, requested=False)
        await self.refuses_stop()

    async def test_applied_terminal_cancellation_is_recovered_without_replaying_stop(self):
        self.receipt({'background': True, 'job_id': 'lost'})
        await self.refuses_stop()
        await self.refuses_stop()

    def test_archive_requires_terminal_without_cleanup_error(self):
        self.registry.archive = Mock()
        call = self.receipt({'background': True, 'job_id': 'archived'})
        for status, cleanup, settled in (('unavailable', None, False), ('completed', 'original failure', False),
                                        ('completed', None, True), ('failed', None, True)):
            self.registry.archive.output.return_value = {'job': {'status': status, 'cleanupError': cleanup}}
            self.assertEqual(background_status(self.registry, 'owner', call)['settled'], settled)
        self.registry.archive.output.assert_called_with('owner', 'archived', limit=1)
