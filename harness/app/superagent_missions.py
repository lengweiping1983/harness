"""有限目标图、真实交付交接和独立复核；未知副作用只回查。"""
from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from pathlib import Path

from ..core.cleanup import join_cleanup, raise_cleanup_errors
from ..core.errors import HarnessError
from ..core.language import current_language, resolve_language
from ..i18n import tr
from ..session.tool_activity import confirmed_tool_result, tool_invocations
from ..session.turn_scope import turn_events
from .superagent_inbox import _error, _json, _page, _text
from .superagent_mission_store import TERMINAL, digest, packed, validate_plan
from .superagent_mission_status import dependencies, run_progress, waiting_reason
from .superagent_mission_control import MissionControlRuntime
from .superagent_checkpoints import validate_checkpoint, work_heads, status_jobs
from .superagent_router import SuperagentRouter
from .worktree_delivery import snapshot_tree


class MissionResults:
    """信道只消费最终独立复核的本地候选，任务事实仍在目标账本。"""

    def __init__(self, coordinator, reply_limits=None):
        self.coordinator = coordinator
        self._reply_limits = dict(reply_limits or {})

    @property
    def reply_limits(self):
        return dict(self._reply_limits)

    def status(self):
        with self.coordinator.store.db() as db:
            count = db.execute("SELECT count(*) FROM missions WHERE instance=? AND state='completed'",
                               (self.coordinator.inbox.instance_id,)).fetchone()[0]
        return {'draft': count} if count else {}

    def uncollected(self):
        return False

    def _get(self, identity):
        mission = self.coordinator.store.mission(identity)
        if mission['state'] != 'completed':
            raise _error('event-unavailable')
        with self.coordinator.store.db() as db:
            publication = db.execute('SELECT seq FROM publications WHERE mission=?', (identity,)).fetchone()
        if publication is None:
            raise _error('corrupt')
        jobs = self.coordinator.store.jobs(identity)
        review = next(job for job in jobs if job['role'] == 'review' and job['attempt'] == mission['round'])
        integration = next(job for job in jobs if job['role'] == 'integrate' and job['attempt'] == mission['round'])
        self.coordinator._confirmed_review(mission, review)
        if (integration['state'] != 'done' or review['state'] != 'done'
                or mission['reply'] != review['data']['verdict']['reply']
                or not all(check['passed'] for check in review['data']['verdict']['checks'])):
            raise _error('corrupt')
        return {**{key: mission[key] for key in ('id', 'source_id', 'event_id', 'revision', 'reply')}, 'seq': publication[0],
                'task_id': self.coordinator.worker_factory.task_id(integration['resource'], mission['trigger_id']),
                'worker_run': integration['data']['run_id'], 'router_run': review['data']['run_id'], 'state': 'draft'}

    def drafts(self, *, after=0, limit=100):
        _page(after, limit)
        with self.coordinator.store.db() as db:
            ids = [row[0] for row in db.execute("SELECT m.id FROM publications p JOIN missions m ON p.mission=m.id WHERE m.instance=? AND m.state='completed' AND p.seq>? ORDER BY p.seq LIMIT ?",
                                               (self.coordinator.inbox.instance_id, after, limit))]
        return [self._get(identity) for identity in ids]


class SuperagentMissions(SuperagentRouter):
    """确定性调度拥有资源、预算和代次，模型只提出计划与复核结论。"""

    def __init__(self, dispatcher, router_harness, *, session_id, worker_factory, store, reply_limits=None, control=None):
        super().__init__(dispatcher, router_harness, session_id=session_id, workers={}, worker_factory=worker_factory)
        self.store = store
        self.guidance = None
        self.control = control
        self.control_runtime = MissionControlRuntime(self) if control is not None else None
        self.results = MissionResults(self, reply_limits)
        self._tick_task = None

    def _allowed(self, mission):
        event = self.inbox.event(source_id=mission['source_id'], event_id=mission['event_id'])
        source = self.inbox.source(mission['source_id'])
        return (event['id'] == mission['trigger_id'] and event['disposition'] == 'accepted'
                and event['human'] and event['explicit'] and source['revision'] == mission['revision']
                and source['mode'] != 'ignore' and event['actor_id'] in source['actors']
                and all(thread in source['threads'] for thread in event['thread_ids']))

    def _payload(self, key, frame):
        with current_language.set(resolve_language(self.harness.config.language).effective_language):
            result = tr(key, frame=packed(frame))
        return _text(result, 'mission_payload', limit=524288)

    def _collect_missions(self):
        with self.store.db(write=True) as db:
            cursor = db.execute('SELECT cursor FROM coordinator WHERE instance=?', (self.inbox.instance_id,)).fetchone()[0]
            if type(cursor) is not int or cursor < 0:
                raise _error('corrupt')
            active = db.execute("SELECT count(*) FROM missions WHERE instance=? AND state NOT IN ('completed','blocked','unknown','rejected','suppressed','cancelled')",
                                (self.inbox.instance_id,)).fetchone()[0]
            if active >= self.store.limits['max_missions']:
                return
            events = self.inbox.events(after=cursor, limit=min(16, self.store.limits['max_missions'] - active))['events']
            for event in events:
                if (event['disposition'] != 'accepted' or not event['human'] or not event['explicit']
                        or not any(route['kind'] == 'router' for route in event['routes'])):
                    continue
                db.execute("""INSERT INTO missions(id,instance,source_id,event_id,trigger_id,revision,state,round)
                    VALUES(?,?,?,?,?,?,'planning',1)""", (self.store.mission_id(event['id']), self.inbox.instance_id,
                    event['source_id'], event['event_id'], event['id'], event['policy_revision']))
            if events:
                db.execute('UPDATE coordinator SET cursor=? WHERE instance=?', (events[-1]['seq'], self.inbox.instance_id))

    def _stop_mission(self, mission, state, reason):
        self.store.update(mission['id'], before=mission['state'], state=state, reason=reason)

    def _control_allows(self, mission):
        return self.control_runtime is None or self.control_runtime.admit(mission)

    def _record(self, job):
        self._check_job_resource(job)
        return self._submission(job)

    def _submission(self, job):
        """只核对原提交事实，监督读取不触发资源创建、Git 探测或账本写入。"""
        host = self.harness if job['role'] == 'plan' else self.dispatcher.harness
        data = job['data']
        record = host.runtime.find_submission(data['session_id'], job['id'])
        if record is not None and (record.text != data['payload'] or record.request_id != job['id']
                or record.session_id != data['session_id'] or record.prompt_input is not None
                or data.get('run_id') is not None and data['run_id'] != record.id):
            raise _error('conflict')
        if record is None and job['state'] not in ('submitting', 'unknown'):
            raise _error('corrupt')
        return record

    def _check_job_resource(self, job):
        if job['role'] == 'plan':
            if job['data'].get('session_id') != self.session_id or 'workspace_id' in job['data']:
                raise _error('corrupt')
        else:
            self._worker_row(self.store.mission(job['mission']), job)

    def _reconcile_job(self, job):
        record = self._record(job)
        state = 'accepted' if record is not None else 'unknown'
        return self.store.change_job(job, state, **({'run_id': record.id} if record is not None else {})), record

    async def _submit_job(self, job):
        self._check_job_resource(job)
        host = self.harness if job['role'] == 'plan' else self.dispatcher.harness
        active = host.runtime.active.get(job['data']['session_id'])
        if (active is not None and not active.done()
                or job['role'] != 'plan' and len(host.runtime.records.active) >= self.dispatcher.max_active):
            return
        await host.ensure_started()
        self._open()
        if not self._control_allows(self.store.mission(job['mission'])):
            return
        self._check_job_resource(job)
        mission = self.store.mission(job['mission'])
        if not self._allowed(mission):
            self._stop_mission(mission, 'suppressed', 'source/revoked')
            return
        if job['role'] == 'plan':
            host._agent_for(host.get_session(self.session_id)).tools = host.tools.restricted([])
        claimed = self.store.change_job(job, 'submitting')
        if claimed is None:
            return
        try:
            host.runtime.submit(claimed['data']['session_id'], claimed['data']['payload'], key=job['id'], request_id=job['id'])
            self._reconcile_job(claimed)
        except BaseException as original:
            errors = [original]
            try:
                self._reconcile_job(self.store.job(job['id']))
            except BaseException as recovery:
                if recovery is not original:
                    errors.append(recovery)
            raise_cleanup_errors(errors)

    def _dependency_jobs(self, mission, job):
        jobs = self.store.jobs(mission['id'])
        heads = work_heads(jobs)
        if job['role'] == 'work':
            if job['attempt'] > 1:
                return [next(item for item in jobs if item['role'] == 'work' and item['node'] == job['node']
                             and item['attempt'] == job['attempt'] - 1)]
            return [heads[name] for name in job['dependencies']]
        if job['role'] == 'integrate':
            return list(heads.values()) if job['attempt'] == 1 else []
        if job['role'] == 'review':
            return [next(item for item in jobs if item['role'] == 'integrate' and item['attempt'] == job['attempt'])]
        return []

    def _report_frame(self, job):
        record = self._record(job)
        return {'job_id': job['id'], 'run_id': record.id, 'status': record.status,
                'workspace_id': job['data'].get('workspace_id'), 'delivery': job['data'].get('delivery'),
                'output': record.output[:8192], 'output_chars': len(record.output), 'output_truncated': len(record.output) > 8192}

    def _check_dependencies(self, dependencies, *, continuation=False):
        """导入之前核对原运行、所属资源及当前冻结交付，不能先写入再检查。"""
        sources = []
        for job in dependencies:
            record = self._record(job)
            expected = 'checkpointed' if continuation else 'done'
            if job['state'] != expected or record.status != 'completed' or not record.settled:
                raise _error('corrupt')
            if job['role'] == 'work' and 'max_work_turns' in self.store.limits:
                self._confirmed_checkpoint(job)
            workspace = self.dispatcher.harness.worktrees.get(job['data']['workspace_id'])
            if workspace.get('delivery') != job['data']['delivery']:
                raise _error('conflict')
            delivery = job['data']['delivery']
            sources.append({'id': workspace['id'], **{key: delivery[key] for key in ('tree', 'ref', 'commit')}})
        return sources

    async def _native(self, operation):
        """Git 写入归所属任务；等待者取消不遗落正在执行的线程。"""
        return await join_cleanup(asyncio.create_task(asyncio.to_thread(operation)))

    def _worker_row(self, mission, job):
        task_id = self.worker_factory.task_id(job['resource'], mission['trigger_id'])
        row = self.worker_factory.worker(task_id)
        if (row['state'] != 'ready' or row['session_id'] != job['data'].get('session_id')
                or row['workspace_id'] != job['data'].get('workspace_id')
                or row['profile_id'] != job['profile'] or row['batch_id'] != job['resource']
                or row['source_id'] != mission['source_id'] or row['event_id'] != mission['event_id']
                or row['revision'] != mission['revision']
                or self.worker_factory._session(row) is None):
            raise _error('worker-unavailable')
        return row

    async def _prepare_job(self, mission, job):
        if not self._control_allows(mission):
            return
        if job['role'] == 'plan':
            event = self.inbox.event(source_id=mission['source_id'], event_id=mission['event_id'])
            profiles = {key: {'description': value['description'], 'tools': value['tools'], 'repository_id': digest(value['repo'])[:24]}
                        for key, value in self.store.profiles.items() if mission['source_id'] in value['sources']}
            payload = self._payload('superagent.mission.plan', {'mission_id': mission['id'], 'request': event['text'],
                'profiles': profiles, 'max_steps': self.store.limits['max_steps'],
                'integrator_profile': self.store.limits['integrator_profile'],
                'reviewer_profile': self.store.limits['reviewer_profile'],
                **({'max_work_turns': self.store.limits['max_work_turns']} if 'max_work_turns' in self.store.limits else {}),
                'attempt': job['attempt'], 'previous_plan_error': mission['reason']})
            self.store.change_job(job, 'ready', payload=payload, session_id=self.session_id)
            return
        dependencies = self._dependency_jobs(mission, job)
        continuation = job['role'] == 'work' and job['attempt'] > 1
        if any(item['state'] != ('checkpointed' if continuation else 'done') for item in dependencies):
            return
        if len(self.dispatcher.harness.runtime.records.active) >= self.dispatcher.max_active:
            return
        item = {'id': mission['trigger_id'], 'source_id': mission['source_id'],
                'event_id': mission['event_id'], 'revision': mission['revision']}
        worker = await self.worker_factory.ensure(batch_id=job['resource'], item=item, profile_id=job['profile'])
        self._open()
        if not self._control_allows(mission):
            return
        if worker['state'] != 'ready':
            if worker['state'] in ('unknown', 'skipped'):
                self._stop_mission(mission, 'unknown' if worker['state'] == 'unknown' else 'suppressed', 'worker/' + worker['state'])
            return
        job = self.store.change_job(job, 'pending', session_id=worker['session_id'], workspace_id=worker['workspace_id'])
        if job is None:
            return
        if not self._allowed(mission):
            self._stop_mission(mission, 'suppressed', 'source/revoked')
            return
        if dependencies:
            confirmed = self._check_dependencies(dependencies, continuation=continuation)
            sources = [item['id'] for item in confirmed]
            plan = await self._native(lambda: self.dispatcher.harness.worktrees.plan_delivery_merge(worker['workspace_id'], sources))
            self._open()
            if not self._control_allows(mission):
                return
            if not self._allowed(mission):
                self._stop_mission(mission, 'suppressed', 'source/revoked')
                return
            if not plan['clean']:
                self._stop_mission(mission, 'blocked', 'git/delivery-conflicts')
                return
            importing = self.store.change_job(job, 'importing', import_plan=plan)
            if importing is None:
                return
            try:
                await self._native(lambda: self.dispatcher.harness.worktrees.apply_delivery_merge(worker['workspace_id'], sources,
                    expected_target_tree=plan['target_tree'], expected_tree=plan['tree']))
            except BaseException as original:
                self._recover_native(importing, original, importing=True)
            self._finish_import(mission, self.store.job(job['id']))
        else:
            self._ready_worker(mission, job)

    def _ready_worker(self, mission, job):
        event = self.inbox.event(source_id=mission['source_id'], event_id=mission['event_id'])
        frame = {'mission_id': mission['id'], 'job_id': job['id'], 'role': job['role'],
                 'request': event['text'], 'acceptance': mission['plan']['acceptance'],
                 'attempt': job['attempt'], 'max_attempts': self.store.limits['max_attempts'],
                 'predecessors': [self._report_frame(item) for item in self._dependency_jobs(mission, job)]}
        if self.guidance is not None and job['role'] in ('integrate', 'review'):
            frame['applied_guidance'] = self.guidance.applied_context(mission['id'])
        if job['role'] == 'work':
            frame['instruction'] = next(step['instruction'] for step in mission['plan']['steps'] if step['id'] == job['node'])
            if 'max_work_turns' in self.store.limits:
                del frame['max_attempts']
                frame['max_work_turns'] = self.store.limits['max_work_turns']
                if job['attempt'] > 1:
                    previous = self._dependency_jobs(mission, job)[0]
                    frame['checkpoint'] = self._confirmed_checkpoint(previous)
                    if self.guidance is not None:
                        frame['applied_guidance'] = self.guidance.applied_context(mission['id'], node=job['node'])
        if job['role'] == 'integrate' and job['attempt'] > 1:
            previous = next(item for item in self.store.jobs(mission['id'])
                            if item['role'] == 'review' and item['attempt'] == job['attempt'] - 1)
            frame['feedback'] = previous['data']['verdict']
        key = ('superagent.mission.checkpoint' if job['role'] == 'work' and 'max_work_turns' in self.store.limits
               else 'superagent.mission.review' if job['role'] == 'review' else 'superagent.mission.work')
        self.store.change_job(job, 'ready', payload=self._payload(key, frame))

    def _finish_import(self, mission, job):
        if not self._control_allows(mission):
            return
        plan = job['data']['import_plan']
        if plan['sources'] != self._check_dependencies(self._dependency_jobs(mission, job),
                continuation=job['role'] == 'work' and job['attempt'] > 1):
            raise _error('corrupt')
        row = self._worker_row(mission, job)
        workspace = self.dispatcher.harness.worktrees.get(row['workspace_id'])
        imported = workspace.get('delivery_inputs')
        if (not isinstance(imported, dict) or any(imported.get(key) != plan[key] for key in ('sources', 'tree', 'target_tree'))):
            self.store.change_job(job, 'unknown')
            self._stop_mission(mission, 'unknown', 'git/import-unconfirmed')
            return
        self._ready_worker(mission, job)

    def _recover_native(self, job, original, *, importing=False):
        errors = [original]
        try:
            mission = self.store.mission(job['mission'])
            if importing:
                self._finish_import(mission, self.store.job(job['id']))
            else:
                self._finish_capture(mission, self.store.job(job['id']))
        except BaseException as recovery:
            if recovery is not original:
                errors.append(recovery)
        raise_cleanup_errors(errors)

    async def _capture(self, mission, job):
        row = self._worker_row(mission, job)
        manager = self.dispatcher.harness.worktrees
        if job['data']['session_id'] in self.dispatcher.harness.jobs.active_owners():
            return
        checkpoint = None
        if job['role'] == 'work' and 'max_work_turns' in self.store.limits:
            progress = self._checkpoint_progress(job)
            if progress['tools']['unconfirmed']:
                self.store.change_job(job, 'unknown')
                self._stop_mission(mission, 'unknown', 'work/effects-unconfirmed')
                return
            checkpoint = self._read_checkpoint(job)
            if checkpoint is None or checkpoint['status'] == 'blocked':
                self.store.change_job(job, 'blocked', **({'checkpoint': checkpoint} if checkpoint else {}))
                self._stop_mission(mission, 'blocked', 'work/blocked' if checkpoint else 'work/checkpoint-invalid')
                return
        workspace = manager.get(row['workspace_id'])
        tree = await self._native(lambda: snapshot_tree(manager, workspace)[1])
        self._open()
        if not self._control_allows(mission):
            return
        if not self._allowed(mission):
            self._stop_mission(mission, 'suppressed', 'source/revoked')
            return
        if job['role'] == 'review' and tree != job['data']['import_plan']['tree']:
            self._stop_mission(mission, 'blocked', 'review/input-changed')
            return
        capturing = self.store.change_job(job, 'capturing', tree=tree,
            capture_before=(workspace.get('delivery') or {}).get('ref'), **({'checkpoint': checkpoint} if checkpoint else {}))
        if capturing is None:
            return
        try:
            await self._native(lambda: manager.capture_delivery(workspace['id']))
        except BaseException as original:
            self._recover_native(capturing, original)
        self._finish_capture(mission, self.store.job(job['id']))

    def _finish_capture(self, mission, job):
        if not self._control_allows(mission):
            return
        row = self._worker_row(mission, job)
        manager = self.dispatcher.harness.worktrees
        workspace = manager.get(row['workspace_id'])
        delivery = workspace.get('delivery')
        if (not isinstance(delivery, dict) or delivery.get('tree') != job['data']['tree']
                or delivery.get('ref') == job['data'].get('capture_before')):
            self.store.change_job(job, 'unknown')
            self._stop_mission(mission, 'unknown', 'git/capture-unconfirmed')
            return
        if not self._allowed(mission):
            self._stop_mission(mission, 'suppressed', 'source/revoked')
            return
        if job['role'] == 'review':
            verdict = self._verdict(mission, job)
            if verdict is None:
                self.store.change_job(job, 'blocked', delivery=delivery)
                self._stop_mission(mission, 'blocked', 'review/evidence-invalid')
                return
            self.store.change_job(job, 'done', delivery=delivery, verdict=verdict)
        else:
            state = 'done'
            if job['role'] == 'work' and 'max_work_turns' in self.store.limits:
                checkpoint = self._confirmed_checkpoint(job)
                if checkpoint['status'] == 'continue':
                    state = 'checkpointed'
                    earlier = [item for item in self.store.jobs(mission['id']) if item['role'] == 'work'
                               and item['node'] == job['node'] and item['attempt'] < job['attempt']]
                    for previous in earlier:
                        if self._confirmed_checkpoint(previous) == checkpoint and previous['data']['tree'] == delivery['tree']:
                            self.store.change_job(job, 'blocked', delivery=delivery)
                            self._stop_mission(mission, 'blocked', 'work/no-progress')
                            return
            self.store.change_job(job, state, delivery=delivery)

    def _checkpoint_progress(self, job):
        """接续只采用确切已完成且已收尾的原运行，不推测失败命令可以重试。"""
        record = self._record(job)
        if record.status != 'completed' or not record.settled:
            raise _error('corrupt')
        session = self.dispatcher.harness.get_session(job['data']['session_id'])
        progress = run_progress(record, session.events)
        if progress['turn'] is None:
            raise _error('corrupt')
        return progress

    def _read_checkpoint(self, job):
        try:
            return validate_checkpoint(_json(_text(self._record(job).output, 'checkpoint', limit=65536)))
        except (ValueError, TypeError, KeyError, RecursionError, HarnessError):
            return None

    def _confirmed_checkpoint(self, job):
        """消费前回查原正文及工具配对，修改缓存哈希不能把中间阶段提升为就绪。"""
        checkpoint = self._read_checkpoint(job)
        if (checkpoint is None or checkpoint != job['data'].get('checkpoint')
                or self._checkpoint_progress(job)['tools']['unconfirmed']):
            raise _error('corrupt')
        return checkpoint

    def _verdict(self, mission, job):
        """逐条引用当前复核轮的真实结果，命令证据额外核对前台退出和编码。"""
        record = self._record(job)
        try:
            if record.status != 'completed' or not record.settled:
                raise ValueError
            value = _json(_text(record.output, 'review_output', limit=65536))
            if (not isinstance(value, dict) or set(value) != {'checks', 'summary', 'reply'}
                    or not isinstance(value['checks'], list) or len(value['checks']) != len(mission['plan']['acceptance'])):
                raise ValueError
            _text(value['summary'], 'summary', limit=8192)
            if value['reply'] is not None:
                _text(value['reply'], 'reply', limit=4096)
                limit = self.results.reply_limits.get(mission['source_id'], 100)
                if len(re.findall(r'[\u3400-\u9fff]|[^\s\u3400-\u9fff]+', value['reply'])) > limit:
                    raise ValueError
            session = self.dispatcher.harness.get_session(job['data']['session_id'])
            starts = [event for event in session.events_of_type('turn/start') if event.data.get('run_id') == record.id]
            if len(starts) != 1:
                raise ValueError
            events = turn_events(session.events, turn=starts[0].data['turn'])
            invocations = tool_invocations(events)
            rows = {row['call_id']: row for row in invocations if confirmed_tool_result(row)
                    and sum(item['call_id'] == row['call_id'] for item in invocations) == 1}
            expected = {item['id']: item for item in mission['plan']['acceptance']}
            seen = set()
            for check in value['checks']:
                if (not isinstance(check, dict) or set(check) != {'id', 'passed', 'evidence', 'reason'}
                        or not isinstance(check['id'], str) or check['id'] not in expected or check['id'] in seen
                        or type(check['passed']) is not bool or not isinstance(check['evidence'], list)
                        or len(check['evidence']) > 16):
                    raise ValueError
                _text(check['reason'], 'reason', limit=4096)
                seen.add(check['id'])
                if check['passed'] and not check['evidence']:
                    raise ValueError
                confirmed = []
                for reference in check['evidence']:
                    if not isinstance(reference, str) or not reference:
                        raise ValueError
                    row = rows.get(reference)
                    if row is None:
                        raise ValueError
                    display = row['_result_event'].data.get('display', {})
                    if isinstance(display, Mapping) and display.get('not_executed'):
                        raise ValueError
                    if expected[check['id']]['kind'] == 'command':
                        if (row['name'] not in ('bash', 'pwsh') or not isinstance(display, Mapping)
                                or display.get('execution_state') != 'exited' or type(display.get('exit_code')) is not int
                                or display.get('background') or display.get('encoding_errors')
                                or check['passed'] and display['exit_code'] != 0):
                            raise ValueError
                        arguments = row['_start_event'].data['input']
                        cwd = Path(arguments.get('cwd') or session.header.cwd)
                        if not cwd.is_absolute():
                            cwd = Path(session.header.cwd) / cwd
                        if cwd.resolve() != Path(session.header.cwd).resolve():
                            raise ValueError
                    elif check['passed'] and row['name'] not in ('read', 'glob', 'grep'):
                        raise ValueError
                    elif check['passed']:
                        arguments = row['_start_event'].data['input']
                        path = Path(arguments.get('path') or '.')
                        root = Path(session.header.cwd).resolve()
                        if not path.is_absolute():
                            path = root / path
                        if not path.resolve().is_relative_to(root):
                            raise ValueError
                    if check['passed'] and row['_result']['is_error']:
                        raise ValueError
                    confirmed.append({'call_id': reference, 'result_seq': row['_result_event'].seq,
                                      'tool': row['name'], 'run_id': record.id})
                check['evidence'] = confirmed
            if seen != set(expected):
                raise ValueError
            return value
        except (ValueError, TypeError, KeyError, RecursionError, HarnessError):
            return None

    def _confirmed_review(self, mission, job):
        """消费持久结论前回到原运行核对，损坏缓存不能提升失败结论。"""
        verdict = self._verdict(mission, job)
        integration = next(item for item in self.store.jobs(mission['id'])
                           if item['role'] == 'integrate' and item['attempt'] == job['attempt'])
        if (job['state'] != 'done' or verdict is None or verdict != job['data'].get('verdict')
                or integration['state'] != 'done'
                or job['data']['delivery']['tree'] != integration['data']['delivery']['tree']):
            raise _error('corrupt')
        return verdict

    async def _advance_job(self, mission, job):
        if job['state'] == 'pending':
            await self._prepare_job(mission, job)
        elif job['state'] == 'importing':
            self._finish_import(mission, job)
        elif job['state'] == 'ready':
            await self._submit_job(job)
        elif job['state'] in ('submitting', 'accepted', 'unknown'):
            job, record = self._reconcile_job(job)
            if record is None:
                self._stop_mission(mission, 'unknown', 'run/submission-unconfirmed')
            elif record.settled:
                if record.status != 'completed':
                    self.store.change_job(job, 'blocked')
                    self._stop_mission(mission, 'blocked', 'run/' + record.status)
                elif job['role'] == 'plan':
                    self._accept_plan(mission, job, record)
                else:
                    await self._capture(mission, job)
        elif job['state'] == 'capturing':
            self._finish_capture(mission, job)

    def _accept_plan(self, mission, job, record):
        try:
            plan = validate_plan(_json(_text(record.output, 'plan_output', limit=65536)), self.store.profiles,
                                 mission['source_id'], self.store.limits)
        except (ValueError, TypeError, KeyError, RecursionError, HarnessError):
            self.store.change_job(job, 'blocked')
            if mission['round'] < self.store.limits['max_attempts']:
                self.store.update(mission['id'], before='planning', round=mission['round'] + 1, reason='plan/invalid')
            else:
                self._stop_mission(mission, 'rejected', 'plan/invalid')
            return
        repo = self.store.profiles[plan['steps'][0]['profile_id']]['repo']
        base = self.dispatcher.harness.worktrees.git(repo, 'rev-parse', '--verify', 'HEAD^{commit}').strip()
        self.store.update(mission['id'], before='planning', state='running', plan=packed(plan), plan_hash=digest(packed(plan)),
                          base_commit=base, repo=repo, round=1, reason=None)
        self.store.change_job(job, 'done')

    def _schedule(self, mission):
        if not self._control_allows(mission):
            return
        if not self._allowed(mission):
            self._stop_mission(mission, 'suppressed', 'source/revoked')
            return
        jobs = self.store.jobs(mission['id'])
        if mission['state'] == 'planning':
            self.store.add_job(mission, role='plan', node='goal', attempt=mission['round'])
            return
        if mission['state'] == 'running':
            for step in mission['plan']['steps']:
                self.store.add_job(mission, role='work', node=step['id'], profile=step['profile_id'], dependencies=step['depends_on'])
            work = list(work_heads(self.store.jobs(mission['id'])).values())
            for job in work:
                if job['state'] == 'checkpointed':
                    self._confirmed_checkpoint(job)
                    if job['attempt'] >= self.store.limits['max_work_turns']:
                        self._stop_mission(mission, 'blocked', 'budget/work-turns-exhausted')
                        return
                    self.store.add_job(mission, role='work', node=job['node'], attempt=job['attempt'] + 1,
                                       profile=job['profile'], dependencies=job['dependencies'])
            if all(job['state'] == 'done' for job in work):
                self.store.update(mission['id'], before='running', state='integrating')
        elif mission['state'] == 'integrating':
            initial = mission['id'] + '/integrate:goal:1'
            job = self.store.add_job(mission, role='integrate', node='goal', attempt=mission['round'],
                                     profile=self.store.limits['integrator_profile'], resource=initial)
            if job['state'] == 'done':
                self.store.update(mission['id'], before='integrating', state='reviewing')
        elif mission['state'] == 'reviewing':
            job = self.store.add_job(mission, role='review', node='goal', attempt=mission['round'],
                                     profile=self.store.limits['reviewer_profile'])
            if job['state'] != 'done':
                return
            verdict = self._confirmed_review(mission, job)
            if all(check['passed'] for check in verdict['checks']):
                if verdict['reply'] is None:
                    self._stop_mission(mission, 'blocked', 'review/reply-missing')
                else:
                    self.store.complete(mission, verdict['reply'])
            elif mission['round'] >= self.store.limits['max_attempts']:
                self._stop_mission(mission, 'blocked', 'budget/attempts-exhausted')
            else:
                self.store.update(mission['id'], before='reviewing', state='integrating', round=mission['round'] + 1)

    async def tick(self, *, include_status=True):
        if type(include_status) is not bool:
            raise _error('invalid', field='include_status')
        self._open()
        loop = asyncio.get_running_loop()
        owner = self.dispatcher.harness.runtime.claim_loop(loop)
        if owner is not loop:
            return await join_cleanup(asyncio.wrap_future(asyncio.run_coroutine_threadsafe(self.tick(include_status=include_status), owner)))
        if self.harness.runtime.claim_loop(loop) is not loop:
            raise _error('invalid', field='router_loop')
        if self._tick_task is None or self._tick_task.done():
            self._tick_task = asyncio.create_task(self._tick())
        await join_cleanup(self._tick_task)
        return self.status() if include_status else None

    async def aclose(self):
        """停止调度准入，等待原推进及控制层已经接受的所属作业收尾。"""
        self.close()
        errors = []
        if self._tick_task is not None:
            try:
                await join_cleanup(self._tick_task)
            except HarnessError as exc:
                if exc.code != 'superagent/closed':
                    errors.append(exc)
            except BaseException as exc:
                errors.append(exc)
        if self.control_runtime is not None:
            try:
                await self.control_runtime.aclose()
            except BaseException as exc:
                if not any(error is exc for error in errors):
                    errors.append(exc)
        raise_cleanup_errors(errors)

    async def _tick(self):
        if self.control_runtime is not None:
            self.control_runtime.sync(recover=True)
        self._collect_missions()
        if self.control_runtime is not None:
            self.control_runtime.sync(recover=True)
        if self.guidance is not None:
            await self.guidance.tick()
        with self.store.db() as db:
            cursor = db.execute('SELECT scheduling_cursor FROM coordinator WHERE instance=?', (self.inbox.instance_id,)).fetchone()[0]
            rows = db.execute("SELECT id,seq FROM missions WHERE instance=? AND state NOT IN ('completed','blocked','unknown','rejected','suppressed','cancelled') ORDER BY (seq<=?),seq LIMIT 8",
                              (self.inbox.instance_id, cursor)).fetchall()
        for row in rows:
            self._open()
            mission = self.store.mission(row['id'])
            if self.control is not None and self.control.mode(mission['id'])['mode'] != 'running':
                continue
            if not self._allowed(mission):
                self._stop_mission(mission, 'suppressed', 'source/revoked')
                continue
            self._schedule(mission)
            for job in self.store.jobs(mission['id']):
                mission = self.store.mission(mission['id'])
                if mission['state'] in TERMINAL:
                    break
                if job['state'] in ('done', 'blocked', 'checkpointed'):
                    continue
                try:
                    await self._advance_job(mission, job)
                except HarnessError as exc:
                    if exc.code in ('superagent/worker-capacity', 'git/delivery-conflicts', 'git/file-changed'):
                        self._stop_mission(self.store.mission(mission['id']), 'blocked', exc.code)
                    else:
                        raise
            mission = self.store.mission(mission['id'])
            if mission['state'] not in TERMINAL:
                self._schedule(mission)
        if rows:
            with self.store.db(write=True) as db:
                db.execute('UPDATE coordinator SET scheduling_cursor=? WHERE instance=?', (rows[-1]['seq'], self.inbox.instance_id))

    def _mission_exists(self, mission_id):
        """控制请求只在目标已写入任务账本后才消费。"""
        try:
            self.store.mission(mission_id)
        except HarnessError as exc:
            if exc.code == 'superagent/event-unavailable':
                return False
            raise
        return True

    def _job_status(self, mission, job, jobs, *, events, background, at_capacity, control=None):
        item = {**{key: job[key] for key in ('id', 'role', 'node', 'attempt', 'state', 'dependencies')},
                **{key: job['data'][key] for key in ('run_id', 'session_id', 'workspace_id', 'delivery') if key in job['data']}}
        host = self.harness if job['role'] == 'plan' else self.dispatcher.harness
        sid = job['data'].get('session_id')
        progress = None
        if sid and 'payload' in job['data'] and job['state'] in ('submitting', 'accepted', 'capturing', 'done', 'blocked', 'unknown', 'checkpointed'):
            record = self._submission(job)
            if record is not None:
                key = (id(host), sid)
                if key not in events:
                    events[key] = host.get_session(sid).events
                progress = run_progress(record, events[key])
                item['run'] = progress
        unresolved = dependencies(mission, job, jobs)
        active = host.runtime.active.get(sid)
        reason = waiting_reason(mission, job, progress=progress, unresolved=unresolved,
            background=sid in background, conversation_busy=active is not None and not active.done(), at_capacity=at_capacity)
        if reason is not None:
            item['waiting_for'] = reason
        if unresolved:
            item['unresolved_dependencies'] = unresolved
        if mission['state'] in TERMINAL and job['state'] not in ('done', 'blocked', 'checkpointed'):
            item['scheduling_halted'] = True
        if control is not None and control['mode'] != 'running' and job['state'] not in ('done', 'blocked', 'checkpointed'):
            item['scheduling_halted'] = True
            if reason not in ('run', 'run-stop', 'run-cleanup', 'background-jobs', 'delivery-reconciliation',
                              'submission-reconciliation', 'result-reconciliation'):
                item['waiting_for'] = 'control-' + control['mode']
        if job['role'] == 'work' and 'max_work_turns' in self.store.limits:
            item['max_work_turns'] = self.store.limits['max_work_turns']
        if 'checkpoint' in job['data']:
            checkpoint = job['data']['checkpoint']
            item['checkpoint'] = {'status': checkpoint['status'], 'summary': checkpoint['summary'][:512],
                'summary_truncated': len(checkpoint['summary']) > 512,
                'next_steps': [step[:256] for step in checkpoint['next_steps'][:4]],
                'next_steps_total': len(checkpoint['next_steps']), 'next_steps_has_more': len(checkpoint['next_steps']) > 4,
                'next_steps_truncated': any(len(step) > 256 for step in checkpoint['next_steps'][:4])}
        if 'verdict' in job['data']:
            verdict = job['data']['verdict']
            item['review'] = {'summary': verdict['summary'][:512], 'summary_truncated': len(verdict['summary']) > 512,
                              'checks': [{'id': check['id'], 'passed': check['passed']} for check in verdict['checks']]}
        return item

    def status(self):
        """状态与阻塞原因携明确运行引用；复核通过不表示已发布或已合并主分支。"""
        cursor, counts, snapshots, active = self.store.status_snapshots(include_active=True)
        background = self.dispatcher.harness.jobs.active_owners()
        at_capacity = len(self.dispatcher.harness.runtime.records.active) >= self.dispatcher.max_active
        events = {}
        recent = []
        controls = self.control.modes([mission['id'] for mission, _ in snapshots]) if self.control is not None else {}
        for mission, jobs in snapshots:
            control = controls.get(mission['id'], {'mission_id': mission['id'], 'mode': 'running', 'reason': 'default', 'revision': 0, 'updated_at': None})
            if self.control_runtime is not None and control['mode'] == 'cancelled':
                control['stopping'] = self.control_runtime.stopping(mission['id'])
            visible = status_jobs(jobs) if 'max_work_turns' in self.store.limits else jobs
            recent.append({**{key: mission[key] for key in ('id', 'state', 'reason', 'round', 'base_commit', 'source_id', 'event_id', 'revision', 'thread_ids')},
                'budget': {'attempt': mission['round'], 'max_attempts': self.store.limits['max_attempts'],
                           'phase': 'planning' if mission['plan'] is None else 'delivery'},
                'work': {'total': len(mission['plan']['steps']) if mission['plan'] else None,
                         'delivered': sum(job['state'] == 'done' for job in work_heads(jobs).values())},
                'control': control,
                'acceptance': [{**check, 'criterion': check['criterion'][:512],
                                'criterion_truncated': len(check['criterion']) > 512}
                               for check in mission['plan']['acceptance']] if mission['plan'] else [],
                'jobs': [self._job_status(mission, job, jobs, events=events, background=background, at_capacity=at_capacity,
                                          control=control) for job in visible],
                'jobs_total': len(jobs), 'jobs_shown': len(visible), 'jobs_has_more': len(jobs) > len(visible)})
        active_controls = self.control.modes([mission['id'] for mission in active]) if self.control is not None else {}
        inputs = {'pending': sum(active_controls.get(mission['id'], {}).get('mode', 'running') == 'running' for mission in active),
                  'unknown': counts.get('unknown', 0),
                  'rejected': sum(counts.get(state, 0) for state in ('blocked', 'rejected', 'suppressed'))}
        return {'instance_id': self.inbox.instance_id, 'cursor': cursor, 'inputs': inputs, 'batches': [],
                'workers': self.worker_factory.status(), 'results': {'draft': counts['completed']} if counts.get('completed') else {},
                'missions': {'states': counts, 'recent': recent, 'total': sum(counts.values()),
                             'shown': len(recent), 'has_more': sum(counts.values()) > len(recent)},
                **({'control': self.control.status()} if self.control is not None else {}),
                **({'guidance': self.guidance.status()} if self.guidance is not None else {})}
