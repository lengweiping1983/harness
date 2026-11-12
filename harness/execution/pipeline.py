"""持久顺序执行已确定的步骤，不调用模型，不重放未知副作用。"""
from __future__ import annotations

import asyncio
from contextvars import ContextVar
from copy import deepcopy
import json
import uuid

from ..core.errors import HarnessError
from ..core.cleanup import join_cleanup
from ..i18n import tr
from ..tools.schema import validate
from .catalog import PIPELINE_ID, PIPELINE_STEP
from .policy import fail, file_version


CURRENT_PIPELINE = ContextVar('execution_pipeline', default=None)
ACTIVE = {'queued', 'running', 'waiting_image'}


def valid_step(step):
    """按种类验证必需字段及互斥字段，不能依赖未支持的 schema 关键字。"""
    if validate(step, PIPELINE_STEP):
        return False
    fields = {'exec': {'kind', 'command'}, 'verify': {'kind', 'verification_id'},
              'image': {'kind', 'image_id'}}[step['kind']]
    return set(step) == fields or step['kind'] == 'exec' and set(step) == fields | {'timeout_ms'}


class PipelineRuntime:
    """流水线复用现有授权、作业、回执和事件存储。"""

    def _pipeline_records(self, sid):
        rows = self.store.db.execute("SELECT data FROM events WHERE session_id=? AND kind='pipeline/state' ORDER BY seq", (sid,))
        values = {}
        for row in rows:
            try:
                value = json.loads(row['data'])
                required = {'pipeline_id', 'session_id', 'project_root', 'revision', 'status', 'index', 'steps', 'skills', 'error_code'}
                if (not isinstance(value, dict) or set(value) != required
                        or validate(value['pipeline_id'], PIPELINE_ID) or value['session_id'] != sid
                        or not isinstance(value['project_root'], str)
                        or type(value['revision']) is not int or value['revision'] < 0
                        or value['status'] not in ACTIVE | {'completed', 'failed', 'interrupted', 'cancelled'}
                        or not isinstance(value['steps'], list) or not 1 <= len(value['steps']) <= 30
                        or type(value['index']) is not int or not 0 <= value['index'] <= len(value['steps'])
                        or not isinstance(value['skills'], list) or len(value['skills']) > 20
                        or not isinstance(value['error_code'], str)):
                    raise ValueError('pipeline')
                for index, step in enumerate(value['steps']):
                    if (not isinstance(step, dict) or set(step) != {'input', 'request_id', 'job_id', 'evidence_id', 'status'}
                            or not valid_step(step['input']) or step['request_id'] != f"{value['pipeline_id']}:{index}"
                            or step['status'] not in {'pending', 'accepted', 'completed', 'failed'}
                            or index < value['index'] and step['status'] != 'completed'
                            or any(item is not None and not isinstance(item, str) for item in (step['job_id'], step['evidence_id']))):
                        raise ValueError('step')
                for skill in value['skills']:
                    if (not isinstance(skill, dict) or set(skill) != {'name', 'sha256'}
                            or not isinstance(skill['name'], str) or not isinstance(skill['sha256'], str)
                            or len(skill['sha256']) != 64):
                        raise ValueError('skill')
                prior = values.get(value['pipeline_id'])
                if value['revision'] != (prior['revision'] + 1 if prior else 0):
                    raise ValueError('revision')
                if prior and (any(value[key] != prior[key] for key in ('project_root', 'skills'))
                              or [step['input'] for step in value['steps']] != [step['input'] for step in prior['steps']]):
                    raise ValueError('plan')
                if (value['status'] == 'completed' and value['index'] != len(value['steps'])
                        or prior and not prior['index'] <= value['index'] <= prior['index'] + 1
                        or any(step['status'] != 'pending' for step in value['steps'][value['index'] + 1:])):
                    raise ValueError('progress')
                values[value['pipeline_id']] = value
            except (ValueError, TypeError, KeyError):
                fail('execution/store-invalid', tr('execution.store.invalid_record'))
        return list(values.values())

    def _pipeline_record(self, sid, pid):
        for value in self._pipeline_records(sid):
            if value['pipeline_id'] == pid:
                return value
        fail('execution/pipeline-not-found', tr('execution.pipeline.not_found'))

    def _pipeline_recover(self):
        """服务重开只记录已丢失的执行所有权，绝不启动命令。"""
        for row in self.store.db.execute('SELECT id FROM sessions').fetchall():
            for value in self._pipeline_records(row['id']):
                if value['status'] in {'queued', 'running'} and value['pipeline_id'] not in self._pipeline_tasks:
                    value['status'] = 'interrupted'
                    value['error_code'] = 'execution/service-interrupted'
                    self._pipeline_save(value)

    def _pipeline_save(self, value, *, initial=False):
        """每次推进先提交完整快照，再执行后续副作用。"""
        if not initial:
            if value == self._pipeline_record(value['session_id'], value['pipeline_id']):
                return
            value['revision'] += 1
        self.store.event(value['session_id'], 'pipeline/state', value)

    def _pipeline_reserved(self, root):
        for row in self.store.db.execute('SELECT id,principal FROM sessions'):
            session = self.store.session(row['id'], row['principal'])
            if session['project_root'] == str(root):
                for value in self._pipeline_records(row['id']):
                    if value['status'] in ACTIVE and value['pipeline_id'] != CURRENT_PIPELINE.get():
                        return value
        return None

    async def _pipeline_skill_versions(self, names, ctx):
        values = []
        for name in names:
            skill = await self._skill_load({'session_id': ctx.session_id, 'name': name}, ctx)
            values.append({'name': name, 'sha256': skill['skill']['version']})
        return values

    async def _pipeline_start(self, args, ctx):
        _, project, _ = self._policy(args, ctx)
        if not project.commands:
            fail('execution/command-denied', tr('execution.runtime.c05a2a5a06b3'))
        self._assert_idle(project)
        steps = args['steps']
        if not all(valid_step(step) for step in steps) or len(set(args.get('skills', []))) != len(args.get('skills', [])):
            fail('execution/pipeline-invalid', tr('execution.pipeline.invalid'))
        for step in steps:
            if step['kind'] == 'image':
                self.store.images(ctx.session_id, step['image_id'])
            if step['kind'] == 'verify' and step['verification_id'] not in self._verification(ctx.session_id, project):
                fail('execution/pipeline-invalid', tr('execution.pipeline.invalid'))
        pid = 'pl_' + uuid.uuid4().hex
        value = {'pipeline_id': pid, 'session_id': ctx.session_id, 'project_root': str(project.root),
                 'revision': 0, 'status': 'queued', 'index': 0, 'error_code': '',
                 'skills': await self._pipeline_skill_versions(args.get('skills', []), ctx),
                 'steps': [{'input': deepcopy(step), 'request_id': f'{pid}:{index}', 'job_id': None,
                            'evidence_id': None, 'status': 'pending'} for index, step in enumerate(steps)]}
        self._pipeline_save(value, initial=True)
        self._pipeline_schedule(ctx.services['principal'], ctx.session_id, pid)
        return self._pipeline_view(value)

    def _pipeline_view(self, value):
        value = deepcopy(value)
        task = self._pipeline_tasks.get(value['pipeline_id'])
        if value['status'] in {'queued', 'running'} and (task is None or task.done()):
            value['status'] = 'interrupted'
        value['next_action'] = {'waiting_image': 'generate_and_deliver_image', 'completed': 'review_and_finalize',
                                'failed': 'inspect_failure', 'cancelled': 'none', 'interrupted': 'reconcile_then_resume'}.get(value['status'], 'wait')
        value['instruction'] = tr('execution.pipeline.guidance')
        return value

    async def _pipeline_get(self, args, ctx):
        self._policy(args, ctx)
        values = ([self._pipeline_record(ctx.session_id, args['pipeline_id'])] if 'pipeline_id' in args
                  else self._pipeline_records(ctx.session_id))
        return {'pipelines': [self._pipeline_view(value) for value in values]}

    def _pipeline_schedule(self, principal, sid, pid):
        task = self._pipeline_tasks.get(pid)
        if self.closed or task is not None and not task.done():
            return
        task = asyncio.create_task(self._pipeline_run(principal, sid, pid), name=pid)
        self._pipeline_tasks[pid] = task
        def finished(done):
            if self._pipeline_tasks.get(pid) is done:
                self._pipeline_tasks.pop(pid, None)
            if not done.cancelled():
                done.exception()
        task.add_done_callback(finished)

    def _pipeline_wake_images(self, principal, sid):
        for value in self._pipeline_records(sid):
            if value['status'] == 'waiting_image':
                self._pipeline_schedule(principal, sid, value['pipeline_id'])

    async def _pipeline_run(self, principal, sid, pid):
        token = CURRENT_PIPELINE.set(pid)
        value = self._pipeline_record(sid, pid)
        try:
            while value['index'] < len(value['steps']):
                if self.closed:
                    return
                session, project = self._session(sid, principal)
                if str(project.root) != value['project_root']:
                    fail('execution/project-changed', tr('execution.runtime.73db6e7068e2'))
                from ..tools.registry import ToolRunContext
                ctx = ToolRunContext(call_id=pid, session_id=sid, cwd=str(project.root), turn=0, step=0,
                                     services={'principal': principal})
                if value['skills'] != await self._pipeline_skill_versions([skill['name'] for skill in value['skills']], ctx):
                    fail('execution/pipeline-skill-changed', tr('execution.pipeline.skill_changed'))
                step = value['steps'][value['index']]
                data = step['input']
                if data['kind'] == 'image':
                    image = self.store.images(sid, data['image_id'])[0]
                    if image['status'] != 'delivered':
                        value['status'] = 'waiting_image'
                        value['error_code'] = '' if image['status'] == 'pending' else 'execution/image-delivery-unconfirmed'
                        self._pipeline_save(value)
                        return
                    policy = self._policy({'session_id': sid}, ctx)[2]
                    if file_version(policy.resolve(image['path'])) != image['delivery']['sha256']:
                        fail('execution/file-conflict', tr('execution.runtime.eb0c1fb39952'))
                else:
                    tool = 'harness_exec' if data['kind'] == 'exec' else 'harness_verify'
                    if step['status'] == 'pending':
                        step['status'] = 'accepted'
                        value['status'] = 'running'
                        self._pipeline_save(value)
                        call_args = {key: item for key, item in data.items() if key != 'kind'}
                        submission = asyncio.create_task(self.call(principal, tool, {**call_args, 'session_id': sid,
                            'request_id': step['request_id'], 'yield_ms': 0}))
                        try:
                            result = await asyncio.shield(submission)
                        except asyncio.CancelledError:
                            try:
                                await join_cleanup(submission)
                            finally:
                                if submission.done() and not submission.cancelled() and submission.exception() is None:
                                    result = submission.result()
                                    if not result['is_error']:
                                        step['job_id'] = result['data']['job']['id']
                                        self._pipeline_save(value)
                            raise
                    else:
                        result = self.store.call_receipt(principal, sid, tool, step['request_id'])
                        if result['state'] != 'finished':
                            fail('execution/outcome-unknown', tr('execution.store.f8ddf9268667'))
                        result = result['result']
                    if result['is_error']:
                        fail(result['data']['error']['code'], result['data']['error']['message'])
                    step['job_id'] = result['data']['job']['id']
                    self._pipeline_save(value)
                    while True:
                        output = await self._job_poll({'session_id': sid, 'job_id': step['job_id'], 'wait_ms': 1000}, ctx)
                        if output['job']['status'] != 'running':
                            break
                    monitor = self._job_monitors.get(step['job_id'])
                    if monitor is not None:
                        await asyncio.shield(monitor)
                        output = await self._job_poll({'session_id': sid, 'job_id': step['job_id']}, ctx)
                    step['evidence_id'] = output['evidence_id']
                    if (output['job']['status'] != 'completed' or output['job'].get('exitCode') != 0
                            or output['job'].get('encoding_errors') or data['kind'] == 'verify' and not output['evidence_id']):
                        fail('execution/pipeline-step-failed', tr('execution.pipeline.step_failed'))
                    if data['kind'] == 'verify' and not self.store.get_evidence(sid, output['evidence_id']).get('passed'):
                        fail('execution/pipeline-step-failed', tr('execution.pipeline.step_failed'))
                step['status'] = 'completed'
                value['error_code'] = ''
                value['index'] += 1
                value['status'] = 'queued'
                self._pipeline_save(value)
            value['status'] = 'completed'
            self._pipeline_save(value)
        except asyncio.CancelledError:
            value['status'] = 'interrupted'
            self._pipeline_save(value)
            raise
        except Exception as exc:
            value['status'] = 'interrupted' if isinstance(exc, HarnessError) and exc.code == 'execution/outcome-unknown' else 'failed'
            value['error_code'] = exc.code if isinstance(exc, HarnessError) else 'execution/pipeline-error'
            self._pipeline_save(value)
        finally:
            CURRENT_PIPELINE.reset(token)

    async def _pipeline_control(self, args, ctx):
        lock = self._pipeline_controls.setdefault(args['pipeline_id'], asyncio.Lock())
        async with lock:
            return await self._pipeline_control_locked(args, ctx)

    async def _pipeline_control_locked(self, args, ctx):
        _, project, _ = self._policy(args, ctx)
        value = self._pipeline_record(ctx.session_id, args['pipeline_id'])
        if value['revision'] != args['expected_revision']:
            fail('execution/revision-conflict', tr('execution.store.e5ed3877a4c9'))
        task = self._pipeline_tasks.get(value['pipeline_id'])
        if args['action'] == 'cancel':
            if value['status'] in {'completed', 'failed', 'cancelled'}:
                return self._pipeline_view(value)
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                value = self._pipeline_record(ctx.session_id, args['pipeline_id'])
            if value['index'] < len(value['steps']):
                step = value['steps'][value['index']]
                job_id = step['job_id']
                if step['status'] == 'accepted' and not job_id:
                    tool = 'harness_exec' if step['input']['kind'] == 'exec' else 'harness_verify'
                    receipt = self.store.call_receipt(ctx.services['principal'], ctx.session_id, tool, step['request_id'])
                    if receipt['state'] != 'finished':
                        value['status'] = 'interrupted'
                        value['error_code'] = 'execution/outcome-unknown'
                        self._pipeline_save(value)
                        return self._pipeline_view(value)
                    if not receipt['result']['is_error']:
                        job_id = receipt['result']['data']['job']['id']
                        step['job_id'] = job_id
                if job_id and job_id in self.jobs._jobs:
                    _, job = await self.jobs.kill(job_id, ctx.session_id, tr('execution.runtime.510d61ee5a9b'))
                    if job['status'] not in {'completed', 'failed', 'killed'}:
                        value['status'] = 'interrupted'
                        value['error_code'] = 'execution/pipeline-cancel-unconfirmed'
                        self._pipeline_save(value)
                        return self._pipeline_view(value)
                elif job_id:
                    output = await self._job_poll({'session_id': ctx.session_id, 'job_id': job_id}, ctx)
                    if output['job']['status'] not in {'completed', 'failed', 'killed'}:
                        value['status'] = 'interrupted'
                        value['error_code'] = 'execution/outcome-unknown'
                        self._pipeline_save(value)
                        return self._pipeline_view(value)
            value['status'] = 'cancelled'
            self._pipeline_save(value)
        elif value['status'] not in {'completed', 'failed', 'cancelled'}:
            if not project.commands:
                fail('execution/command-denied', tr('execution.runtime.c05a2a5a06b3'))
            token = CURRENT_PIPELINE.set(value['pipeline_id'])
            try:
                if self._pipeline_reserved(project.root):
                    fail('execution/pipeline-busy', tr('execution.pipeline.busy'))
            finally:
                CURRENT_PIPELINE.reset(token)
            self._pipeline_schedule(ctx.services['principal'], ctx.session_id, value['pipeline_id'])
        else:
            fail('execution/pipeline-terminal', tr('execution.pipeline.terminal'))
        return self._pipeline_view(value)

    async def _request_get(self, args, ctx):
        session, _, _ = self._policy(args, ctx)
        scope = session['project_id'] if args['tool'] == 'harness_session_open' else ctx.session_id
        return self.store.call_receipt(ctx.services['principal'], scope, args['tool'], args['request_id'])
