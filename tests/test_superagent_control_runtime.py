"""真实多目标、工作树和后台进程的暂停、取消及控制回执回归。"""
import asyncio
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from harness.app.superagent_local import control_local, read_control_receipt
from harness.app.superagent_service import SuperagentService
from harness.cli import build_parser, main
from harness.core.errors import HarnessError
from harness.core.language import current_language
from harness.i18n import tr
from tests import test_superagent_local as local_fixture
from tests.test_superagent_missions import SuperagentMissionFixture, PlannerTransport, frame_of


class HeldPlanner(PlannerTransport):
    def __init__(self):
        super().__init__()
        self.entered, self.release = asyncio.Event(), asyncio.Event()

    async def post_stream(self, url, *, headers, body, timeout):
        if frame_of(json.loads(body))['request'] == 'Second objective':
            self.entered.set()
            await self.release.wait()
        async for chunk in super().post_stream(url, headers=headers, body=body, timeout=timeout):
            yield chunk


class SuperagentControlRuntimeTests(SuperagentMissionFixture):
    def mission(self, receipt):
        return self.service.router.store.mission_id(receipt['id'])

    def control(self, identity, action, request_id):
        return control_local(self.path, action, identity, request_id=request_id)

    async def test_cancelling_an_old_goal_never_interrupts_another_goal_in_the_shared_planner(self):
        self.planner = HeldPlanner()
        self.worker.work_gate = asyncio.Event()
        self.worker.gate_request = 'First objective'
        service = await self.open()
        first = self.receive('first', 'First objective')
        await self.advance_until(lambda value: any(mission['state'] == 'running' for mission in value['missions']['recent']))
        second = self.receive('second', 'Second objective')
        try:
            await self.advance_until(lambda _: self.planner.entered.is_set(), settle=False)
            running = service.router_host.runtime.current(service.router.session_id)
            self.assertIsNotNone(running)
            self.assertEqual(frame_of(self.planner.requests[-1])['request'], 'First objective')
            self.control(self.mission(first), 'cancel', 'cancel-first')
            await service.tick()
            self.assertEqual(service.router.store.mission(self.mission(first))['state'], 'cancelled')
            self.assertEqual(service.router_host.runtime.current(service.router.session_id).id, running.id)
            self.assertFalse(service.router_host.runtime.active[service.router.session_id].cancelling())
            self.assertEqual(running.status, 'running')
            self.assertEqual(service.router.store.mission(self.mission(second))['state'], 'planning')
        finally:
            self.worker.work_gate.set()
            self.planner.release.set()
        self.assertEqual(await service.serve(asyncio.Event(), once=True), 0)
        self.assertEqual([draft['event_id'] for draft in service.results.drafts()], ['second'])

    async def test_cancelled_goals_release_admission_capacity_without_replaying_the_old_goal(self):
        self.spec['autonomy']['max_missions'] = 1
        service = await self.open()
        first = self.receive('first')
        await service.tick()
        self.assertFalse(self.planner.requests)
        self.control(self.mission(first), 'cancel', 'cancel-first')
        second = self.receive('second')
        self.assertEqual(await service.serve(asyncio.Event(), once=True), 0)
        self.assertEqual(service.router.store.mission(self.mission(first))['state'], 'cancelled')
        self.assertEqual(service.router.store.mission(self.mission(second))['state'], 'completed')
        self.assertEqual(len(self.planner.requests), 1)
        self.control(self.mission(first), 'resume', 'resume-cancelled')
        await service.tick()
        receipt = read_control_receipt(self.path, 'resume-cancelled')
        self.assertEqual((receipt['status'], receipt['result']['error']), ('rejected', 'control/terminal'))
        self.assertEqual(service.router.store.mission(self.mission(first))['state'], 'cancelled')

    async def test_pause_during_import_preflight_blocks_late_write_and_resume_uses_original_resources(self):
        service = await self.open()
        trigger = self.receive()
        await self.advance_until(lambda value: sum(job['role'] == 'work' and job['state'] == 'accepted'
            for mission in value['missions']['recent'] for job in mission['jobs']) == 2)
        await self.settle()
        manager = service.worker_host.worktrees
        original = manager.plan_delivery_merge
        def paused(*args, **kwargs):
            result = original(*args, **kwargs)
            self.control(self.mission(trigger), 'pause', 'pause-import')
            return result
        with patch.object(manager, 'plan_delivery_merge', side_effect=paused), \
                patch.object(manager, 'apply_delivery_merge', side_effect=AssertionError('暂停后导入了文件')):
            await service.tick()
        self.assertEqual(service.control.mode(self.mission(trigger))['mode'], 'paused')
        worker_count = sum(service.factory.status()['states'].values())
        self.assertEqual(await service.serve(asyncio.Event(), once=True), 0)
        self.assertFalse(service.results.drafts())
        self.control(self.mission(trigger), 'resume', 'resume-import')
        self.assertEqual(await service.serve(asyncio.Event(), once=True), 0)
        self.assertEqual(service.router.store.mission(self.mission(trigger))['state'], 'completed')
        self.assertEqual(sum(service.factory.status()['states'].values()), worker_count + 2)

    async def test_pending_counts_include_active_goals_hidden_by_the_recent_list(self):
        self.spec['autonomy']['max_missions'] = 32
        service = await self.open()
        identities = [self.mission(self.receive(str(index))) for index in range(17)]
        service.router._collect_missions()
        service.router._collect_missions()
        for index, identity in enumerate(identities[1:]):
            self.control(identity, 'pause', 'pause-' + str(index))
        service.control.reconcile(mission_exists=service.router._mission_exists)
        before = {path.name: path.read_bytes() for path in service.root.glob('*.sqlite3')}
        with patch.object(service.router_host.runtime, 'submit', side_effect=AssertionError('状态读取提交了运行')):
            status = service.router.status()
        self.assertEqual(status['inputs']['pending'], 1)
        self.assertTrue(status['missions']['has_more'])
        self.assertNotIn(identities[0], [mission['id'] for mission in status['missions']['recent']])
        self.assertEqual({path.name: path.read_bytes() for path in service.root.glob('*.sqlite3')}, before)

    async def test_cancellation_finds_an_old_active_goal_beyond_a_hundred_recent_goals(self):
        self.spec['autonomy']['max_missions'] = 128
        self.worker.work_gate = asyncio.Event()
        service = await self.open()
        first = self.receive('old')
        try:
            await self.advance_until(lambda _: self.worker.work_entered.is_set(), settle=False)
            for index in range(100):
                self.receive('new-' + str(index))
            for _ in range(7):
                service.router._collect_missions()
            self.assertEqual(len(service.router.store.active_missions()), 101)
            _, _, recent = service.router.store.status_snapshots(limit=100)
            self.assertNotIn(self.mission(first), [mission['id'] for mission, _ in recent])
            self.control(self.mission(first), 'cancel', 'cancel-old')
            await service.tick()
            await self.settle()
            self.assertEqual(service.router.store.mission(self.mission(first))['state'], 'cancelled')
            self.assertEqual(service.router.control_runtime.stopping(self.mission(first))['runs_unsettled'], 0)
            self.assertEqual(service.router.status()['inputs']['pending'], 100)
        finally:
            self.worker.work_gate.set()

    async def test_slow_background_cancellation_does_not_block_another_goal(self):
        self.worker.background_marker = self.root / 'release-background'
        service = await self.open()
        first = self.receive('first')
        await self.advance_until(lambda value: any(job.get('waiting_for') == 'background-jobs'
            for mission in value['missions']['recent'] for job in mission['jobs']))
        first_job = next(job for job in service.router.store.jobs(self.mission(first)) if job['node'] == 'first')
        owner = first_job['data']['session_id']
        original = service.worker_host.jobs.kill
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def slow(identity, selected_owner, reason=None):
            calls.append((identity, selected_owner))
            entered.set()
            await release.wait()
            return await original(identity, selected_owner, reason)
        self.worker.background_marker = None
        self.control(self.mission(first), 'cancel', 'cancel-background')
        try:
            with patch.object(service.worker_host.jobs, 'kill', side_effect=slow):
                await service.tick()
                await asyncio.wait_for(entered.wait(), 5)
                second = self.receive('second')
                await self.advance_until(lambda _: service.router.store.mission(self.mission(second))['state'] == 'completed', settle=False)
                status = next(item for item in service.router.status()['missions']['recent'] if item['id'] == self.mission(first))
                self.assertEqual(status['control']['stopping']['state'], 'pending')
                self.assertEqual([draft['event_id'] for draft in service.results.drafts()], ['second'])
                self.assertEqual(len(calls), 1)
        finally:
            release.set()
        self.assertEqual(await service.serve(asyncio.Event(), once=True), 0)
        self.assertNotIn(owner, service.worker_host.jobs.active_owners())
        status = next(item for item in service.router.status()['missions']['recent'] if item['id'] == self.mission(first))
        self.assertEqual(status['control']['stopping']['state'], 'settled')
        self.assertEqual(len(calls), 1)

    async def test_large_valid_profile_configuration_opens_and_closes_without_model_requests(self):
        self.spec['profiles']['coding']['description'] = 'x' * 4096
        service = await self.open()
        self.assertGreater(len(service.control.identity), 4096)
        self.assertFalse(self.worker.requests)
        self.assertFalse(self.planner.requests)

    async def test_cancel_does_not_resolve_or_replay_an_unknown_submission(self):
        service = await self.open()
        trigger = self.receive()
        await service.tick()
        with patch.object(service.router_host.runtime, 'submit', side_effect=OSError('unknown submission')) as submit:
            with self.assertRaisesRegex(OSError, 'unknown submission'):
                await service.tick()
            await service.tick()
            self.control(self.mission(trigger), 'cancel', 'cancel-unknown')
            await service.tick()
            await service.tick()
            self.assertEqual(submit.call_count, 1)
        status = service.router.status()['missions']['recent'][0]
        self.assertEqual(status['state'], 'unknown')
        self.assertEqual(status['jobs'][0]['state'], 'unknown')
        self.assertEqual(status['control']['stopping']['state'], 'unknown')
        self.assertTrue(status['control']['stopping']['unconfirmed_execution'])
        self.assertFalse(self.planner.requests)

    async def test_restart_uses_exact_background_receipt_and_never_signals_an_old_process_id(self):
        for unknown in (False, True):
            with self.subTest(unknown=unknown):
                self.spec['state_dir'] = 'restart-' + str(unknown)
                self.worker.background_marker = self.root / ('release-' + str(unknown))
                service = await self.open()
                trigger = self.receive()
                await self.advance_until(lambda value: any(job.get('waiting_for') == 'background-jobs'
                    for mission in value['missions']['recent'] for job in mission['jobs']))
                work = next(job for job in service.router.store.jobs(self.mission(trigger)) if job['node'] == 'first')
                archive = service.worker_host.jobs.archive
                self.control(self.mission(trigger), 'cancel', 'cancel-before-restart')
                await service.tick()
                self.assertEqual(service.control.mode(self.mission(trigger))['mode'], 'cancelled')
                self.assertIsNotNone(service.router.control_runtime.stopping(self.mission(trigger)))
                await service.aclose()
                if unknown:
                    with archive.db() as db:
                        row = db.execute('SELECT id,metadata FROM jobs WHERE owner=?', (work['data']['session_id'],)).fetchone()
                        metadata = json.loads(row['metadata'])
                        metadata['status'] = 'running'
                        metadata.pop('finishedAt', None)
                        db.execute('UPDATE jobs SET metadata=? WHERE id=?', (json.dumps(metadata), row['id']))
                reopened = await self.open()
                with (patch.object(reopened.router_host.runtime, 'submit', side_effect=AssertionError('重启重放规划')),
                        patch.object(reopened.worker_host.runtime, 'submit', side_effect=AssertionError('重启重放工作')),
                        patch.object(reopened.worker_host.jobs, 'kill', side_effect=AssertionError('向旧进程发送停止'))):
                    self.assertEqual(await reopened.serve(asyncio.Event(), once=True), 2 if unknown else 0)
                status = reopened.router.status()['missions']['recent'][0]
                self.assertEqual(status['state'], 'cancelled')
                self.assertEqual(status['control']['stopping']['state'], 'unknown' if unknown else 'settled')
                await reopened.aclose()

    async def test_failed_background_stop_stays_unknown_and_is_not_replayed_by_control(self):
        self.worker.background_marker = self.root / 'release-background'
        self.path.write_text(json.dumps(self.spec), encoding='utf-8')
        service = await SuperagentService.open(self.path, worker_transport=self.worker, router_transport=self.planner)
        self.service = service
        original_error = OSError('owned stop result was lost')
        async def close():
            with self.assertRaises(OSError) as caught:
                await service.aclose()
            self.assertIs(caught.exception, original_error)
        self.addAsyncCleanup(close)
        trigger = self.receive()
        await self.advance_until(lambda value: any(job.get('waiting_for') == 'background-jobs'
            for mission in value['missions']['recent'] for job in mission['jobs']))
        self.control(self.mission(trigger), 'cancel', 'cancel-failing-job')
        with patch.object(service.worker_host.jobs, 'kill', side_effect=original_error) as stop:
            await service.tick()
            await asyncio.sleep(0)
            await service.tick()
            await service.tick()
            status = next(item for item in service.router.status()['missions']['recent'] if item['id'] == self.mission(trigger))
            self.assertEqual(status['control']['stopping']['state'], 'unknown')
            self.assertEqual(status['control']['stopping']['stop_failures'], 1)
            self.assertEqual(stop.call_count, 1)


class SuperagentControlLocalTests(unittest.TestCase):
    save = local_fixture.SuperagentLocalTests.save

    def setUp(self):
        local_fixture.SuperagentLocalTests.setUp(self)
        self.spec['autonomy'] = {'max_steps': 8, 'max_attempts': 3, 'max_missions': 16,
            'integrator_profile': 'inspect', 'reviewer_profile': 'inspect'}
        self.save()

    def test_invalid_control_parameters_do_not_create_a_state_directory(self):
        for action, mission, request in (([], 'mission', 'id'), ('invalid', 'mission', 'id'),
                ('pause', None, 'id'), ('pause', 'mission', ''), ('pause', 'mission', 'x' * 201)):
            with self.subTest(action=action), self.assertRaises(HarnessError):
                control_local(self.path, action, mission, request_id=request)
            self.assertFalse((self.root / 'state').exists())

    def test_missing_receipt_is_read_only_and_known_receipts_keep_original_status(self):
        with patch('harness.app.superagent_service.Harness', side_effect=AssertionError('控制入口构造了模型宿主')):
            with self.assertRaises(HarnessError):
                read_control_receipt(self.path, 'missing')
            self.assertFalse((self.root / 'state').exists())
            queued = control_local(self.path, 'pause', 'mission', request_id='stable')
            self.assertTrue(queued['queued'])
            path = self.root / 'state' / 'control.sqlite3'
            before = path.read_bytes()
            receipt = read_control_receipt(self.path, 'stable')
            self.assertEqual(receipt['id'], queued['id'])
            self.assertEqual(path.read_bytes(), before)
            with redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(['--superagent', str(self.path), '--superagent-control-receipt', 'stable']), 0)
            self.assertEqual(json.loads(output.getvalue()), receipt)
            self.assertEqual(path.read_bytes(), before)

    def test_cli_selectors_need_a_control_action_and_configuration(self):
        for args in (['--superagent-mission', 'mission'], ['--superagent-control-receipt', 'id'],
                ['--superagent', str(self.path), '--superagent-pause'],
                ['--superagent', str(self.path), '--superagent-control-receipt', 'id', '--superagent-once']):
            with self.subTest(args=args), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                main(args)
            self.assertEqual(caught.exception.code, 2)
        self.assertFalse((self.root / 'state').exists())

    def test_control_receipt_help_consumes_current_language_in_one_process(self):
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                actions = {action.dest: action for action in build_parser()._actions}
                self.assertEqual(actions['superagent_control_receipt'].help, tr('superagent.cli.control-receipt'))

    def test_once_control_exit_code_comes_from_the_original_receipt(self):
        from harness.app.superagent_control import MissionControl
        from harness.app.superagent_service import read_config
        control_local(self.path, 'cancel', 'mission', request_id='cancel')
        spec = read_config(self.path)
        control = MissionControl(self.root / 'state' / 'control.sqlite3', instance_id=spec['instance_id'],
                                 identity=json.dumps(spec, ensure_ascii=False, sort_keys=True))
        control.reconcile(mission_exists=lambda _: True)
        async def runner(*args, **kwargs):
            control.reconcile(mission_exists=lambda _: True)
            return 0
        with patch('harness.superagent_cli.run', side_effect=runner), redirect_stdout(io.StringIO()):
            result = main(['--superagent', str(self.path), '--superagent-resume', '--superagent-mission', 'mission',
                           '--superagent-request-id', 'resume', '--superagent-once'])
        self.assertEqual(result, 2)
        self.assertEqual(read_control_receipt(self.path, 'resume')['status'], 'rejected')
