"""目标控制的执行边界；只停止确切拥有的运行，进程收尾由原作业服务拥有。"""
from __future__ import annotations

import asyncio

from ..core.cleanup import join_cleanup, raise_cleanup_errors
from ..core.language import current_language, resolve_language
from ..i18n import tr
from ..session.turn_scope import turn_events
from ..session.tool_activity import tool_invocations
from ..tools.processes import ProcessCleanupError
from .superagent_inbox import _error
from .superagent_mission_store import TERMINAL
from .superagent_background import background_invocation, background_status


class MissionControlRuntime:
    """持久控制模式与原生运行事实分开，调度不把取消请求当成副作用已撤销。"""

    def __init__(self, coordinator):
        self.coordinator = coordinator
        self._cancelling = {}
        self._job_stops = {}
        self.closed = False

    def sync(self, *, mission_id=None, recover=False):
        coordinator = self.coordinator
        applied = coordinator.control.reconcile(mission_exists=coordinator._mission_exists, mission_id=mission_id)
        cancelled = {row['mission_id'] for row in applied if row['status'] == 'applied' and row['mode'] == 'cancelled'}
        if recover:
            # 已应用取消的目标通常已是终态，重启不能从活动清单恢复它的停止事实。
            cancelled.update(identity for identity in coordinator.control.cancelled_missions()
                             if identity not in self._cancelling)
        if mission_id is not None and coordinator.control.mode(mission_id)['mode'] == 'cancelled':
            cancelled.add(mission_id)
        for identity in cancelled:
            self._cancel(identity)
        self.poll()

    def _cancel(self, identity):
        coordinator = self.coordinator
        mission, jobs = coordinator.store.snapshot(identity)
        if mission['state'] not in TERMINAL:
            coordinator._stop_mission(mission, 'cancelled', 'operator/cancel')
        target = self._cancelling.setdefault(identity, {'runs': {}, 'owners': set(), 'unconfirmed': False})
        for job in jobs:
            if (job['state'] not in ('submitting', 'accepted', 'capturing', 'done', 'blocked', 'unknown', 'checkpointed')
                    or 'payload' not in job['data'] or 'session_id' not in job['data']):
                continue
            # 只读原提交键，不用会话里的另一个运行替代已结束或未知的原运行。
            record = coordinator._record(job)
            if record is None:
                target['unconfirmed'] = True
                continue
            host = coordinator.harness if job['role'] == 'plan' else coordinator.dispatcher.harness
            target['runs'][(id(host), record.id)] = (host, record)
            if record.started_at is not None and record.error and record.error.get('code') == 'run/restarted':
                target['unconfirmed'] = True
            if job['role'] != 'plan':
                target['owners'].add(record.session_id)
                if record.task is None:
                    try:
                        events = turn_events(host.get_session(record.session_id).events, run_id=record.id)
                    except ValueError as exc:
                        raise _error('corrupt') from exc
                    for call in tool_invocations(events):
                        if (background_invocation(call)
                                and not self._background_confirmed(host, record.session_id, call)):
                            target['unconfirmed'] = True
            if record.finished:
                continue
            if record.status == 'queued':
                host.runtime.remove(record.id)
                continue
            current = host.runtime.current(record.session_id)
            task = host.runtime.active.get(record.session_id)
            if current is None or current.id != record.id or task is None or task.done() or task.cancelling():
                continue
            # 从身份复核到投递取消不让出执行权，避免共享规划会话已转入其他目标。
            host.questions.cancel_session(record.session_id)
            agent = host._agents.get(record.session_id)
            if agent is not None:
                agent.cancel('user')
                agent.status.status = 'stopping'
            task.cancel()

    def _background_confirmed(self, host, owner, call):
        """恢复后只采用确切作业回执；旧 PID 或缺失的句柄都不是停止依据。"""
        return background_status(host.jobs, owner, call)['confirmed']

    async def _stop_job(self, identity, owner):
        host = self.coordinator.dispatcher.harness
        with current_language.set(resolve_language(host.config.language).effective_language):
            outcome, view = await host.jobs.kill(identity, owner)
            if outcome == 'cleanup-failed':
                raise ProcessCleanupError(tr('jobs.cleanup.incomplete', job=identity),
                    job_id=identity, detail=view.get('cleanupError'))

    def poll(self):
        """后台作业停止各自拥有一次收尾，慢停止不阻塞其他目标调度。"""
        if self.closed:
            return
        host = self.coordinator.dispatcher.harness
        owners = {owner for target in self._cancelling.values() for owner in target['owners']}
        for job in tuple(host.jobs._jobs.values()):
            if job.owner in owners and not job.is_settled and job.id not in self._job_stops:
                self._job_stops[job.id] = (job.owner, asyncio.create_task(self._stop_job(job.id, job.owner)))
        for _, task in self._job_stops.values():
            if task.done() and not task.cancelled():
                task.exception()

    def stopping(self, identity):
        """只读停止进度，已应用控制模式不是进程或未知副作用已经停止的证据。"""
        target = self._cancelling.get(identity)
        if target is None:
            return None
        tasks = [task for owner, task in self._job_stops.values() if owner in target['owners']]
        failed = sum(task.done() and (task.cancelled() or task.exception() is not None) for task in tasks)
        unsettled = sum(not record.settled for _, record in target['runs'].values())
        background = len(target['owners'] & self.coordinator.dispatcher.harness.jobs.active_owners())
        return {'state': 'unknown' if failed or target['unconfirmed'] else
                'pending' if unsettled or background or any(not task.done() for task in tasks) else 'settled',
                'runs_unsettled': unsettled, 'background_owners': background, 'stop_failures': failed,
                'unconfirmed_execution': target['unconfirmed']}

    def pending(self):
        return (any(not record.settled for target in self._cancelling.values() for _, record in target['runs'].values())
                or any(not task.done() for _, task in self._job_stops.values()))

    def uncertain(self):
        return any(self.stopping(identity)['state'] == 'unknown' for identity in self._cancelling)

    async def aclose(self):
        """关闭等待所有已接受的停止，失败保留原对象，不重复执行未知停止。"""
        self.closed = True
        tasks = [task for _, task in self._job_stops.values()]
        if tasks:
            results = await join_cleanup(asyncio.gather(*tasks, return_exceptions=True))
            raise_cleanup_errors([value for value in results if isinstance(value, BaseException)])

    def admit(self, mission):
        """每个异步准备之后重新采用已排队控制，暂停或取消不能启动迟到的写入。"""
        self.sync(mission_id=mission['id'])
        return self.coordinator.control.mode(mission['id'])['mode'] == 'running'
