"""与 HTTP 无关的运行控制：串行化、取消、排队与幂等提交。"""
from __future__ import annotations

from weakref import WeakValueDictionary

from ..core.run_storage import RunStorage
from ..llm.context_accounting import estimate_tokens

from harness.i18n import tr

import asyncio
import hashlib
import json
from copy import deepcopy
import logging
import time
import uuid
import sqlite3
import threading
from dataclasses import dataclass, replace
from typing import Any, Awaitable, Callable

from ..core.errors import HarnessError, PersistenceError
from ..core.cleanup import join_cleanup, preserve_cleanup_errors, raise_cleanup_errors
from ..core.runs import ACTIVE_STATES, AgentRun, turn_outcome, run_failure, run_error_copy
from .run_store import RunKeys, RunRecords
from .queue_edit import check_queue_version, queued_view, check_queue_order


class RunController:
    def __init__(self, harness: Any) -> None:
        self.harness = harness
        self.loop: asyncio.AbstractEventLoop | None = None
        self._loop_lock = threading.Lock()
        self._locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()
        self.active: dict[str, asyncio.Task] = {}
        self._executions: dict[asyncio.Task, AgentRun] = {}
        self.storage = RunStorage(harness.config.home / "runs.sqlite3")
        self.path = self.storage.path
        self.db = self.storage.db
        self.records = RunRecords(self.db)
        self._context_controls: dict[str, AgentRun] = {}
        self._keys = RunKeys(self.db)
        self._pending: dict[str, dict[str, AgentRun]] = {}
        self._task_records: dict[asyncio.Task, AgentRun] = {}
        self._queue_wake: dict[str, asyncio.Future] = {}
        self._queue_controls: dict[str, dict[str, Any]] = {}
        self._steering: set[str] = set()
        self._preserved_on_close: set[str] = set()
        self.closed = False
        self._close_task: asyncio.Task[None] | None = None
        self.storage_error: str | None = None
        recovered: list[AgentRun] = []
        with self.db() as db:
            queue_orders = {}
            for sid, raw in db.execute('SELECT session_id, data FROM queue_orders'):
                order = json.loads(raw)
                if (not isinstance(order, list) or any(not isinstance(key, str) or not key for key in order)
                        or len(order) != len(set(order))):
                    raise ValueError(tr('queue.order.invalid'))
                queue_orders[sid] = order
            for sid, paused, reason, updated in db.execute('SELECT * FROM queue_controls'):
                if paused not in (0, 1) or reason not in ('', 'user', 'stopped'):
                    raise ValueError(tr('python.runtime.f87a9b09f13c'))
                self._queue_controls[sid] = {'paused': bool(paused), 'pause_reason': reason, 'updated_at': updated}
            for row in db.execute(self.records.SELECT + " WHERE json_extract(r.data,'$.parent_session_id') IS NULL AND json_extract(r.data,'$.status') IN ('queued','running','stopping') ORDER BY r.rowid"):
                record = self.records.decode(row)
                data = record.storage_dict()
                changed = False
                preserved = (data.get('kind', 'prompt') == 'prompt' and data['status'] == 'queued' and data.get('started_at') is None and
                             self._queue_controls.get(data['session_id'], {}).get('paused', False))
                steered = (data['status'] == 'queued' and self.harness.sessions.has(data['session_id']) and
                           'queue-steer:' + data['id'] in self.harness.get_session(data['session_id']).steering.records)
                if steered:
                    changed = True
                    preserved = False
                    data.update(status='interrupted', finished_at=time.time(),
                                error=run_failure('run/steered', 'ui.run.steered'))
                elif data["status"] in ACTIVE_STATES and not preserved:
                    changed = True
                    data["status"] = "interrupted"
                    data["error"] = run_failure('run/restarted', 'ui.run.restarted')
                    data["finished_at"] = time.time()
                record = AgentRun(**data)
                if changed:
                    recovered.append(record)
                self.records[record.id] = record
                if preserved:
                    self._pending.setdefault(record.session_id, {})[record.id] = record
        for sid, pending in self._pending.items():
            ranks = {key: index for index, key in enumerate(queue_orders.get(sid, []))}
            self._pending[sid] = dict(sorted(pending.items(), key=lambda row: (
                row[1].goal_id is not None, ranks.get(row[0], len(ranks)))))
        if recovered:
            with self.db() as db:
                for record in recovered:
                    self.storage.write(db, record)

    def require_submitter(self, record):
        """实际执行前核对真人当前能力与加入周期；重新加入不能恢复旧派工。"""
        if record.requester is not None:
            state = self.harness.session_access.require(record.session_id, record.requester['subject'], 'contribute')
            if state['member_epochs'].get(record.requester['subject']) != record.access_epoch:
                raise HarnessError('access/denied', code='access/denied')

    def message_origin(self, session_id):
        """消息归属从当前运行派生，不信任用户提示词或客户端作者字段。"""
        record = self.current(session_id)
        if record is None or record.requester is None:
            return {}
        return {'requester': dict(record.requester), 'request_id': record.request_id, 'run_id': record.id}

    def live_records(self):
        """活动查询不扫描终态历史；返回快照以隔离调用方遍历。"""
        return tuple(self.records.active.values())

    def current(self, session_id: str) -> AgentRun | None:
        """由真实执行句柄找回同一运行，不扫描历史或猜测最近提交。"""
        task = self.active.get(session_id)
        return self._executions.get(task) if task is not None and not task.done() else None

    def claim_loop(self, loop: asyncio.AbstractEventLoop) -> asyncio.AbstractEventLoop:
        with self._loop_lock:
            if self.loop is None or self.loop.is_closed() or not self.loop.is_running():
                self.loop = loop
            return self.loop

    def _storage_failure(self) -> PersistenceError:
        self.storage_error = tr('python.runtime.29966fbfa5fb')
        return PersistenceError(self.storage_error)

    def _save(self, record: AgentRun, **changes: Any) -> None:
        """状态变更先提交副本，成功后更新原对象，保持活动运行的唯一身份。"""
        pending = replace(record, **changes) if changes else record
        try:
            with self.db() as db:
                self.storage.write(db, pending)
            for name, value in changes.items():
                setattr(record, name, value)
            self.records.remember(record)
            if record.kind == 'context_compact' and record.status not in ACTIVE_STATES:
                current = self._context_controls.get(record.session_id)
                if current is not None and current.id == record.id:
                    self._context_controls.pop(record.session_id, None)
        except sqlite3.Error as exc:
            raise self._storage_failure() from exc

    def _commit_start(self, record: AgentRun) -> None:
        """启动事实先写盘，再更新同一运行对象；失败不得声称已经执行。"""
        self._save(record, status='running', started_at=time.time())

    async def execute(self, session: Any, operation: Callable[[], Awaitable[Any]], *, record: AgentRun) -> Any:
        if self.closed:
            raise HarnessError(tr('python.runtime.5ad5897727ec'), code="run/closed")
        if self.storage_error:
            raise PersistenceError(self.storage_error)
        lock = self._locks.setdefault(session.id, asyncio.Lock())
        queued = self._task_records.get(asyncio.current_task())
        direct = queued is None
        record = queued or record
        preparations = getattr(self.harness, 'workspace_preparations', None)
        while True:
            if not direct:
                await self._wait_queue_front(record)
            if preparations is not None:
                await preparations.wait(session.header.cwd or self.harness.config.cwd)
            await lock.acquire()
            try:
                self.require_submitter(record)
                self.harness.jobs.assert_ready(session.id)
                if self.closed:
                    raise HarnessError(tr('python.runtime.5ad5897727ec'), code='run/closed')
                if self.storage_error:
                    raise PersistenceError(self.storage_error)
                if session.persistence_error:
                    raise HarnessError(session.persistence_error, code="session/persistence")
                if not direct and not self._can_start(record):
                    continue
                if session.open_turn is not None:
                    from ..session.events import TurnEndReason
                    await self.harness._agent_for(session)._settle_unfinished_tools(session)
                    await session.end_turn(TurnEndReason(kind="interrupted", message=tr('python.runtime.9816cfc9d02a')))
                # 获取锁或清理工具期间发生的暂停仍必须生效。
                # 等待前释放锁，让直接 SDK 调用仍可使用。
                if not direct and not self._can_start(record):
                    continue
                if preparations is not None and preparations.blocked(session.header.cwd or self.harness.config.cwd):
                    continue
                if self.storage_error:
                    raise PersistenceError(self.storage_error)
                record.root_session_id = record.root_session_id or session.id
                self._commit_start(record)
                if not direct:
                    self._dequeue(record)
                async def authorized_operation():
                    self.require_submitter(record)
                    return await operation()
                task = asyncio.create_task(authorized_operation())
                if direct:
                    record.task = task
                record.session = session
                self.active[session.id] = task
                self._executions[task] = record
                failures: list[BaseException] = []
                try:
                    await self.harness.bus.emit('queue_state', self.queue_control(session.id))
                    result = await task
                    self.harness.jobs.assert_ready(session.id)
                    if direct:
                        record.result = result.to_dict()
                        record.status, record.error = turn_outcome(result)
                    return result
                except BaseException as exc:
                    failures.append(exc)
                    if direct:
                        record.status = 'interrupted' if isinstance(exc, asyncio.CancelledError) else 'failed'
                        if isinstance(exc, HarnessError):
                            record.error = exc.to_dict()
                        else:
                            record.error = run_failure('run/cancelled', 'ui.agent.interrupted') if isinstance(exc, asyncio.CancelledError) else run_failure('run/error', 'ui.run.executionFailed')
                    task.cancel()
                    async def cleanup():
                        await asyncio.gather(task, return_exceptions=True)
                        await self.harness.subagents.shutdown(session.id)
                    try:
                        await join_cleanup(asyncio.create_task(cleanup()))
                    except BaseException as cleanup_error:
                        failures.append(cleanup_error)
                finally:
                    with preserve_cleanup_errors(failures):
                        self.active.pop(session.id, None)
                        self._executions.pop(task, None)
                        record.session = None
                        if direct:
                            record.finished_at = time.time()
                            self._save(record)
                            await self.harness.bus.emit('run/state', record.to_dict())
            finally:
                lock.release()

    def queue_control(self, session_id: str) -> dict[str, Any]:
        session = self.harness.get_session(session_id)
        pending = len(self._pending.get(session_id, {}))
        running = session_id in self.active
        preparations = getattr(self.harness, 'workspace_preparations', None)
        waiting = bool(pending and not running and preparations is not None and preparations.blocked(session.header.cwd or self.harness.config.cwd))
        return {'session_id': session_id, 'paused': False, 'pause_reason': '', 'updated_at': None,
                **self._queue_controls.get(session_id, {}),
                'pending': pending, 'running': running, 'waiting_reason': 'environment' if waiting else ''}

    async def set_queue_paused(self, session_id: str, paused: bool, *, reason: str = 'user') -> dict[str, Any]:
        if type(paused) is not bool or reason not in ('user', 'stopped'):
            raise HarnessError(tr('python.runtime.bdc6d8e1e74a'), code='run/invalid')
        owner = self.claim_loop(asyncio.get_running_loop())
        if owner is not asyncio.get_running_loop():
            return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(self.set_queue_paused(session_id, paused, reason=reason), owner))
        current = self.queue_control(session_id)
        if self.closed:
            raise HarnessError(tr('python.runtime.5ad5897727ec'), code='run/closed')
        if self.storage_error:
            raise PersistenceError(self.storage_error)
        new_reason = reason if paused else ''
        if current['paused'] != paused or current['pause_reason'] != new_reason:
            updated = max(time.time(), (current['updated_at'] or 0) + .000001)
            try:
                with self.db() as db:
                    db.execute('INSERT INTO queue_controls VALUES(?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET paused=excluded.paused, reason=excluded.reason, updated_at=excluded.updated_at',
                               (session_id, int(paused), new_reason, updated))
            except sqlite3.Error as exc:
                self._notify_queue(session_id)
                raise self._storage_failure() from exc
            self._queue_controls[session_id] = {'paused': paused, 'pause_reason': new_reason, 'updated_at': updated}
        self._notify_queue(session_id)
        if not paused:
            for record in tuple(self._pending.get(session_id, {}).values()):
                if record.task is None:
                    self._start_record(record)
        state = self.queue_control(session_id)
        await self.harness.bus.emit('queue_state', state)
        return state

    def _notify_queue(self, session_id: str) -> None:
        wake = self._queue_wake.pop(session_id, None)
        if wake is not None and not wake.done():
            wake.set_result(None)

    def _can_start(self, record: AgentRun) -> bool:
        pending = self._pending.get(record.session_id, {})
        session = self.harness.get_session(record.session_id)
        if record.goal_id and (self.harness.plan.is_active_session_id(record.session_id) or self.harness.questions.pending(record.session_id)):
            self.harness.goal_runtime.wait_for_readiness(record.session_id, record.goal_id)
            return False
        # 已接受的引导不能再次作为独立排队回合执行，即使
        # 引导提交后持久化队列移除失败。
        return (record.id not in self._steering and 'queue-steer:' + record.id not in session.steering.records
                and not self._queue_controls.get(record.session_id, {}).get('paused', False)
                and next(iter(pending), None) == record.id)

    async def _wait_queue_front(self, record: AgentRun) -> None:
        while True:
            if self.closed:
                raise HarnessError(tr('python.runtime.5ad5897727ec'), code='run/closed')
            if self.storage_error:
                raise PersistenceError(self.storage_error)
            if self._can_start(record):
                return
            wake = self._queue_wake.get(record.session_id)
            if wake is None:
                wake = asyncio.get_running_loop().create_future()
                self._queue_wake[record.session_id] = wake
            # 取消一个排队任务不能取消所有等待者的通知信号。
            await asyncio.shield(wake)

    def _dequeue(self, record: AgentRun) -> None:
        pending = self._pending.get(record.session_id)
        if pending is not None:
            pending.pop(record.id, None)
            if not pending:
                self._pending.pop(record.session_id, None)
        self._notify_queue(record.session_id)

    def submission(self, session_id: str, key: str) -> AgentRun | None:
        """查询已提交记录；恢复连接时只核实，不重新执行。"""
        self.harness.get_session(session_id)
        found = self._keys.get((session_id, key))
        return self.records.get(found[1]) if found else None

    def submit(self, session_id: str, text: str, *, key: str | None = None, request_id: str | None = None,
               retry_of: str | None = None, recovery: dict[str, Any] | None = None, goal_id: str | None = None,
               prompt_input: dict[str, Any] | None = None, recipients: list[str] | None = None, recipient_team: str | None = None, selection_revision: int | None = None, recipient_identities: dict[str, str] | None = None, subject: str | None = None) -> AgentRun:
        if self.closed:
            raise HarnessError(tr('python.runtime.5ad5897727ec'), code="run/closed")
        if self.storage_error:
            raise PersistenceError(self.storage_error)
        if not isinstance(text, str):
            raise HarnessError(tr('web.input.invalid_text'), code='run/invalid')
        session = self.harness.get_session(session_id)
        if session.persistence_error:
            raise HarnessError(session.persistence_error, code="session/persistence")
        if self.harness.sessions.root is not None and not (self.harness.sessions._dir(session_id) / "header.json").exists():
            try:
                self.harness.sessions._write_header(session)
            except OSError as exc:
                session.persistence_error = tr('python.runtime.6f2b436f1fc6')
                raise PersistenceError(session.persistence_error, session_id=session_id) from exc
        if not text.strip():
            raise HarnessError(tr('python.runtime.cf67ddc55eb9'), code="run/empty")
        if retry_of is not None and (not isinstance(retry_of, str) or not retry_of.strip()):
            raise HarnessError(tr('python.runtime.b711c47d6f16'), code="run/invalid")
        if recovery is not None:
            if retry_of is None or not isinstance(recovery, dict) or recovery.get('mode') not in ('inspect', 'continue'):
                raise HarnessError(tr('python.runtime.b48bc9351f3c'), code='run/invalid')
            recovery = dict(recovery)
        requester, access_epoch = None, None
        if subject is not None:
            state = self.harness.session_access.require(session_id, subject, 'contribute')
            if text.lstrip().startswith('/'):
                raise HarnessError('access/host-command', code='access/host-command')
            requester = {'subject': subject, 'display_name': state['profiles'].get(subject, {}).get('display_name', subject)}
            access_epoch = state['member_epochs'][subject]
        payload = text if retry_of is None else json.dumps([text, retry_of], ensure_ascii=False)
        if recovery is not None:
            payload = json.dumps([text, retry_of, recovery], ensure_ascii=False, sort_keys=True)
        if prompt_input is not None:
            prompt_input = json.loads(json.dumps(prompt_input, ensure_ascii=False))
            payload = json.dumps([prompt_input, retry_of, recovery], ensure_ascii=False, sort_keys=True)
        team_route = None
        if recipient_identities is not None and recipients is None:
            raise HarnessError(tr('collaboration.invalid_recipients'), code='team/invalid-recipients')
        if recipients is not None:
            if text.strip().split(maxsplit=1)[0] in {'/agent', '/runner', '/skill', '/goal', '/plan'}:
                raise HarnessError(tr('collaboration.invalid_recipients'), code='team/invalid-recipients')
            from ..collaboration.routing import prepare_team_route
            team_route = prepare_team_route(self.harness, session, recipients, expected_team=recipient_team, selection_revision=selection_revision, recipient_identities=recipient_identities)
        elif retry_of is not None:
            original_route = getattr(self.records.get(retry_of), 'team_route', None)
            if original_route is not None:
                team_route = json.loads(json.dumps(original_route))
        if team_route is not None:
            payload = json.dumps([payload, team_route], ensure_ascii=False, sort_keys=True)
        if requester is not None:
            payload = json.dumps([payload, requester['subject'], access_epoch], ensure_ascii=False)
        fingerprint = hashlib.sha256(payload.encode()).hexdigest()
        if key and (session_id, key) in self._keys:
            old, run_id = self._keys[(session_id, key)]
            if old != fingerprint:
                raise HarnessError(tr('python.runtime.414842c23b85'), code="run/idempotency-conflict")
            return self.records[run_id]
        if retry_of is not None:
            original = self.get(retry_of)
            if original.session_id != session_id:
                raise HarnessError(tr('python.runtime.47c29ab69a98'), code="run/not-found")
            if original.kind != "prompt" or original.status not in {"failed", "interrupted"} or (original.error or {}).get('code') in {'run/removed', 'run/steered'}:
                raise HarnessError(tr('python.runtime.3a3277c70aea'), code="run/not-retryable")
        self.harness.jobs.assert_ready(session_id)
        record = AgentRun("run_" + uuid.uuid4().hex, session_id, text, request_id=request_id or uuid.uuid4().hex,
                           retry_of=retry_of, recovery=recovery, goal_id=goal_id, prompt_input=prompt_input, team_route=team_route, requester=requester, access_epoch=access_epoch)
        try:
            with self.db() as db:
                self.storage.write(db, record, initial=True)
                if key:
                    db.execute("INSERT INTO run_keys VALUES(?,?,?,?)", (session_id, key, fingerprint, record.id))
        except sqlite3.Error as exc:
            raise self._storage_failure() from exc
        self.records[record.id] = record
        pending = self._pending.setdefault(session_id, {})
        pending[record.id] = record
        if goal_id is None:
            # 用户请求优先于尚未获准执行的自动任务。
            self._pending[session_id] = {**{k: r for k, r in pending.items() if r.goal_id is None},
                                         **{k: r for k, r in pending.items() if r.goal_id is not None}}
        self._notify_queue(session_id)
        self._start_record(record)
        return record

    def find_submission(self, session_id: str, key: str) -> AgentRun | None:
        """只读回查稳定提交键，不建立对话、运行或重放原操作。"""
        if not isinstance(session_id, str) or not session_id.strip() or not isinstance(key, str) or not key.strip():
            raise HarnessError(tr('web.input.invalid_text'), code='run/invalid')
        identity = self._keys.get((session_id, key))
        return self.records[identity[1]] if identity is not None else None

    def _start_record(self, record: AgentRun) -> None:
        session_id = record.session_id
        async def drive() -> None:
            # 等待与 SDK / Web 共用的会话锁；开始事件才改变状态。
            try:
                await self.harness.bus.emit('queue_state', self.queue_control(session_id))
                result = await self.harness.run_async(session_id, record.text)
                record.result = result.to_dict()
                record.status, record.error = turn_outcome(result)
            except asyncio.CancelledError:
                if record.id not in self._preserved_on_close:
                    record.status = "interrupted"
            except HarnessError as exc:
                record.error = exc.to_dict()
                record.status = "failed"
            except Exception:
                record.error = run_failure('run/error', 'ui.run.executionFailed')
                record.status = "failed"
            finally:
                if record.id not in self._preserved_on_close:
                    self._dequeue(record)
                    record.finished_at = time.time()
                try:
                    self._save(record)
                except PersistenceError as exc:
                    record.status = "failed"
                    record.error = exc.to_dict()
                await self.harness.bus.emit("run/state", record.to_dict())
                if record.error and record.error.get('code') not in {'run/removed', 'run/steered'}:
                    await self.harness.bus.emit("error", {"session_id": session_id, **record.error})
                await self.harness.bus.emit("queue_state", self.queue_control(session_id))
                if getattr(self.harness, "goal_runtime", None) is not None:
                    try:
                        await self.harness.goal_runtime.finished(record)
                    except Exception:
                        logging.getLogger(__name__).exception(tr('feedback.059c8d7111ca'))
                        await self.harness.bus.emit("error", {"session_id": session_id, **run_failure("goal/checkpoint-failed", "ui.run.goalCheckpointFailed")})
        record.task = asyncio.create_task(drive())
        self._task_records[record.task] = record
        record.task.add_done_callback(lambda task: self._task_records.pop(task, None))

    def context_control(self, session_id, request_id=None):
        """只读查询控制任务，用于写入结果未知后的核实。"""
        self.harness.get_session(session_id)
        if request_id is not None:
            identity = self._keys.get((session_id, 'context_compact:' + request_id))
            if identity is not None and identity[0] == 'context_compact' and self.records[identity[1]].kind == 'context_compact':
                return self.records[identity[1]]
        record = self._context_controls.get(session_id) or self.records.latest_control(session_id)
        return record if record is not None and (request_id is None or record.request_id == request_id) else None


    async def submit_compaction(self, session_id, request_id, *, restart=False, mode='compact'):
        """持久受理后等待会话锁或请求安全边界，不进入普通发送队列。"""
        owner = self.claim_loop(asyncio.get_running_loop())
        if owner is not asyncio.get_running_loop():
            return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(self.submit_compaction(session_id, request_id, restart=restart, mode=mode), owner))
        if self.closed or self.storage_error:
            raise HarnessError(self.storage_error or tr('python.runtime.5ad5897727ec'), code='run/closed')
        session = self.harness.get_session(session_id)
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 200:
            raise HarnessError(tr('context.control.invalid'), code='run/invalid')
        if not isinstance(mode, str) or mode not in {'compact', 'project'} or type(restart) is not bool or mode == 'project' and restart:
            raise HarnessError(tr('context.control.invalid'), code='run/invalid')
        operation = 'project' if mode == 'project' else 'restart' if restart else ''
        existing = self.context_control(session_id, request_id)
        if existing is not None:
            if existing.text != operation:
                raise HarnessError(tr('context.control.intentMismatch'), code='run/idempotency-conflict')
            return existing
        if (session_id, 'context_compact:' + request_id) in self._keys:
            raise HarnessError(tr('python.runtime.414842c23b85'), code='run/idempotency-conflict')
        pending = self.context_control(session_id)
        if pending is not None and pending.status in {'queued', 'running'}:
            if pending.text != operation:
                raise HarnessError(tr('context.control.busy'), code='run/busy')
            try:
                with self.db() as db:
                    db.execute('INSERT INTO run_keys VALUES(?,?,?,?)', (session_id, 'context_compact:' + request_id, 'context_compact', pending.id))
            except sqlite3.Error as exc:
                raise self._storage_failure() from exc
            return pending
        record = AgentRun('run_' + uuid.uuid4().hex, session_id, operation, request_id=request_id, kind='context_compact')
        try:
            with self.db() as db:
                self.storage.write(db, record, initial=True)
                db.execute('INSERT INTO run_keys VALUES(?,?,?,?)', (session_id, 'context_compact:' + request_id, 'context_compact', record.id))
        except sqlite3.Error as exc:
            raise self._storage_failure() from exc
        self.records[record.id] = record
        self._context_controls[session_id] = record

        async def idle():
            try:
                async with self._locks.setdefault(session_id, asyncio.Lock()):
                    if record.status != 'queued':
                        return
                    agent = self.harness._agent_for(session)
                    agent.reset()
                    from ..llm.types import TokenUsage
                    from ..core.language import current_language, resolve_language
                    agent.language = resolve_language(agent.language_preference()).effective_language
                    agent._usage_total = TokenUsage()
                    agent._compaction_charged = 0
                    agent._compaction_usage = TokenUsage()
                    agent.budget = ExecutionBudget(self.harness.config.token_budget)
                    self.active[session_id] = asyncio.current_task()
                    token = current_language.set(agent.language)
                    try:
                        await self.process_compaction(session, agent, agent.config, 0)
                    finally:
                        current_language.reset(token)
                        self.active.pop(session_id, None)
            except asyncio.CancelledError:
                if record.status == 'queued':
                    record.status = 'interrupted'
                    record.finished_at = time.time()
                    self._save(record)
                raise
        record.task = asyncio.create_task(idle())
        self._task_records[record.task] = record
        record.task.add_done_callback(lambda task: self._task_records.pop(task, None))
        await self.harness.bus.emit('run/state', record.to_dict())
        return record

    async def process_compaction(self, session, agent, config, turn, runtime_messages=()):
        """唯一领取点：持有会话锁的运行在安全边界处理待办控制。"""
        record = self._context_controls.get(session.id)
        if record is None or record.status != 'queued':
            return
        self._commit_start(record)
        await self.harness.bus.emit('run/state', record.to_dict())
        try:
            projection_only = record.text == 'project'
            if projection_only:
                from ..core.compaction import recover_committed
                await recover_committed(session, agent._emit)
            before = estimate_tokens(agent._build_messages(session, runtime_messages=runtime_messages)) + estimate_tokens(agent._active_tool_schemas())
            projected = await agent._project_tool_history(session, turn, runtime_messages=runtime_messages) if projection_only else 0
            if not projection_only:
                await agent._maybe_compact(session, config, turn, force=True, manual=True, runtime_messages=runtime_messages, restart=record.text == 'restart')
            after = estimate_tokens(agent._build_messages(session, runtime_messages=runtime_messages)) + estimate_tokens(agent._active_tool_schemas())
            record.result = {'before_tokens': before, 'after_tokens': after,
                             'changed': after < before, 'through': session.event_count - 1,
                             'reason': ('projected' if projected else 'projection_disabled' if agent.tool_result_max_chars is None else 'nothing_to_project')
                                 if projection_only else 'compacted' if after < before else 'no_eligible_history',
                             'mode': 'project' if projection_only else 'compact', 'projected_results': projected}
            record.status = 'completed'
        except asyncio.CancelledError:
            record.status = 'interrupted'
            record.error = run_failure('run/cancelled', 'ui.run.compactionCancelled')
            raise
        except Exception as exc:
            record.status = 'failed'
            record.error = exc.to_dict() if isinstance(exc, HarnessError) else {'code': 'context/compact-failed', 'message': str(exc)}
        finally:
            record.finished_at = time.time()
            self._save(record)
            await self.harness.bus.emit('run/state', record.to_dict())
        return record.to_dict()

    def get(self, run_id: str) -> AgentRun:
        record = self.records.get(run_id)
        if record is None:
            raise HarnessError(tr('python.runtime.99ecffeea105'), code="run/not-found")
        return record

    def reorder_queue(self, session_id: str, expected: Any, order: Any) -> None:
        """持久化排序后再修改执行准入顺序；当前回合不受影响。"""
        self.harness.get_session(session_id)
        keys = check_queue_order(self.queue(session_id), expected, order)
        pending = self._pending.get(session_id, {})
        records = [pending[key] for key in keys]
        if records != sorted(records, key=lambda record: record.goal_id is not None):
            raise HarnessError(tr('queue.order.invalid'), code='run/invalid')
        try:
            with self.db() as db:
                db.execute('INSERT INTO queue_orders VALUES(?,?) ON CONFLICT(session_id) DO UPDATE SET data=excluded.data',
                           (session_id, json.dumps(keys)))
        except sqlite3.Error as exc:
            raise self._storage_failure() from exc
        self._pending[session_id] = {key: pending[key] for key in keys}
        self._notify_queue(session_id)

    def queue(self, session_id: str) -> list[dict[str, Any]]:
        return [queued_view(r) for r in self._pending.get(session_id, {}).values()]

    def history(self, session_id: str, *, limit: int = 30, before: str | None = None,
                include_queued: bool = True, summary: bool = False) -> dict[str, Any]:
        """最新优先的稳定游标；只保留一页候选，不为每次读取排序整个历史。"""
        self.harness.get_session(session_id)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise HarnessError(tr('python.runtime.07cae6382d9e'), code="run/invalid")
        boundary = None
        if before is not None:
            cursor = self.get(before)
            if cursor.session_id != session_id:
                raise HarnessError(tr('python.runtime.459c31edbbdc'), code="run/not-found")
            boundary = (cursor.created_at, cursor.id)
        page = self.records.page(session_id, limit + 1, boundary, include_queued)
        more = len(page) > limit
        rows = page[:limit]
        def project(record):
            data = record.to_dict()
            if summary:
                # 只沿可信的同会话关联查找，不从用户文本解析执行标识或提示词。
                # 关联链损坏、循环或过深时，回退到当前执行。
                origin = record
                seen = {record.id}
                for _ in range(64):
                    if not origin.retry_of:
                        data['task_text'] = origin.text[:1200]
                        break
                    parent = self.records.get(origin.retry_of)
                    if parent is None or parent.session_id != record.session_id or parent.id in seen:
                        break
                    seen.add(parent.id)
                    origin = parent
                data['summary'] = True
                data['text_truncated'] = len(record.text) > 1200
                data['text'] = record.text[:1200]
                data['result'] = None
                data['error'] = {key: str(value)[:4000] for key, value in (record.error or {}).items()
                                 if key in {'code', 'message', 'error'}} or None
                copy = run_error_copy(record.error)
                if copy is not None:
                    data['error'] = {**(data['error'] or {}), 'copy': deepcopy(copy)}
                if data['error'] is None and record.result and record.result.get('error'):
                    data['error'] = {'message': str(record.result['error'])[:4000]}
            return data
        return {"runs": [project(record) for record in rows], "has_more": more,
                "next_before": rows[-1].id if rows and more else None}

    def require_queue_owner(self, record, subject):
        """共享成员只能管理当前加入周期内属于自己的未执行需求。"""
        if subject is None:
            return
        if record.requester is None or record.requester['subject'] != subject:
            raise HarnessError('access/denied', code='access/denied')
        self.require_submitter(record)

    def edit(self, run_id: str, text: str, *, expected_version: str | None = None, subject: str | None = None) -> AgentRun:
        if not isinstance(text, str):
            raise HarnessError(tr('web.input.invalid_text'), code='run/invalid')
        record = self.get(run_id)
        self.require_queue_owner(record, subject)
        self._check_steering(record)
        if record.status != "queued":
            raise HarnessError(tr('python.runtime.731fd786a7dd'), code="run/not-queued")
        check_queue_version(record.id, record.text, expected_version, display_text=(record.prompt_input or {}).get("text"))
        if not text.strip() and not (record.prompt_input or {}).get("attachment_refs"):
            raise HarnessError(tr('python.runtime.cf67ddc55eb9'), code="run/empty")
        if record.team_route is not None and text.strip().split(maxsplit=1)[:1] in [['/agent'], ['/runner'], ['/skill'], ['/goal'], ['/plan']]:
            raise HarnessError(tr('collaboration.invalid_recipients'), code='team/invalid-recipients')
        if record.requester is not None and text.lstrip().startswith('/'):
            raise HarnessError('access/host-command', code='access/host-command')
        supplied = record.prompt_input
        if supplied is not None:
            from ..web.prompt import AttachmentPart, compose_prompt
            supplied = {**supplied, "text": text}
            text = compose_prompt(text, [AttachmentPart(**part) for part in supplied["parts"]])
        self._save(replace(record, text=text, prompt_input=supplied))
        record.text = text
        record.prompt_input = supplied
        return record

    def remove(self, run_id: str, *, expected_version: str | None = None, subject: str | None = None) -> None:
        record = self.get(run_id)
        self.require_queue_owner(record, subject)
        self._check_steering(record)
        if record.status != "queued":
            raise HarnessError(tr('python.runtime.65b99eb809df'), code="run/not-queued")
        check_queue_version(record.id, record.text, expected_version, display_text=(record.prompt_input or {}).get("text"))
        removed = replace(record, status='interrupted', finished_at=time.time(),
                          error=run_failure('run/removed', 'ui.run.removed'))
        self._save(removed)
        record.status, record.finished_at, record.error = removed.status, removed.finished_at, removed.error
        self._dequeue(record)
        if record.task is not None:
            record.task.cancel()

    def _check_steering(self, record: AgentRun) -> None:
        if record.id in self._steering or 'queue-steer:' + record.id in self.harness.get_session(record.session_id).steering.records:
            raise HarnessError(tr('python.runtime.d0f908d09abd'), code='run/not-queued')

    async def steer_queued(self, session_id: str, run_id: str, *, expected_turn: int, expected_version: str) -> dict[str, Any]:
        """将排队输入转换为引导，避免删除与发送之间的竞态或重放。"""
        owner = self.claim_loop(asyncio.get_running_loop())
        if owner is not asyncio.get_running_loop():
            return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(
                self.steer_queued(session_id, run_id, expected_turn=expected_turn, expected_version=expected_version), owner))
        record = self.get(run_id)
        if record.session_id != session_id:
            raise HarnessError(tr('python.runtime.47c29ab69a98'), code='run/not-found')
        if record.team_route is not None:
            raise HarnessError(tr('collaboration.targeted_steering'), code='team/targeted-steering-unsupported')
        session = self.harness.get_session(session_id)
        request_id = 'queue-steer:' + record.id
        existing = session.steering.records.get(request_id)
        if existing is None and (record.status != 'queued' or record.id in self._steering):
            raise HarnessError(tr('python.runtime.fd47602a27a1'), code='run/not-queued')
        check_queue_version(record.id, record.text, expected_version, display_text=(record.prompt_input or {}).get("text"))
        self._steering.add(record.id)
        try:
            return await self.harness.steer(session_id, record.text, expected_turn=expected_turn, request_id=request_id,
                attachments=(record.prompt_input or {}).get("attachment_refs", []),
                original_text=(record.prompt_input or {}).get("text"))
        finally:
            try:
                # 引导被持久接受后才移除排队项；被拒绝或过期的请求
                # 保留在原队列中的位置和内容。
                if request_id in session.steering.records and record.status == 'queued':
                    removed = replace(record, status='interrupted', finished_at=time.time(),
                                      error=run_failure('run/steered', 'ui.run.steered'))
                    self._save(removed)
                    record.status, record.finished_at, record.error = removed.status, removed.finished_at, removed.error
                    self._dequeue(record)
                    if record.task is not None:
                        record.task.cancel()
            finally:
                self._steering.discard(record.id)
                self._notify_queue(session_id)

    def begin_close(self) -> asyncio.Task[None]:
        """在所属循环的同步边界停止准入并取消根任务，首次调用固定回收归属。"""
        if self._close_task is None:
            self._preserved_on_close = {record.id for sid, pending in self._pending.items()
                                        if self._queue_controls.get(sid, {}).get('paused', False)
                                        for record in pending.values() if record.started_at is None}
            self.closed = True
            # 先标记来源，再取消；用户已有的停止意图继续保留。
            for session_id in self.active:
                agent = self.harness._agents.get(session_id)
                if agent is not None and not agent.cancelled:
                    agent.cancel('disposed')
            tasks = list({*self.active.values(), *self._task_records})
            settling_drivers = {record.task for operation, record in self._executions.items() if operation.cancelling()}
            for task in tasks:
                record = self._executions.get(task)
                driver = record.task if record is not None else None
                # 排队运行由驱动任务拥有，取消驱动会沿 await 传给其操作。
                # 不能再直接取消操作，或再次打断已经开始的 finally。
                if driver in self._task_records and driver is not task and not driver.done():
                    continue
                if task not in settling_drivers and not task.done() and not task.cancelling():
                    task.cancel()
            self._close_task = asyncio.create_task(self._close(tasks), name='run-controller-close')
        return self._close_task

    async def close(self) -> None:
        """等待同一次关闭，取消等待者不再次取消执行或所属收尾。"""
        await join_cleanup(self.begin_close())

    async def _close(self, tasks: list[asyncio.Task]) -> None:
        """观察首次关闭固定的全部任务；根任务负责逐层取消和协议收尾。"""
        results = await asyncio.gather(*tasks, return_exceptions=True)
        errors = []
        for result in results:
            if (isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError)
                    and not any(result is previous for previous in errors)):
                errors.append(result)
        raise_cleanup_errors(errors)


@dataclass(frozen=True)
class IdentityContext:
    """宿主注入的身份；不得从模型参数推导。"""
    user_id: str = "local"
    tenant_id: str = "local"
    roles: tuple[str, ...] = ()


def _check_budget_count(value: Any, field: str, *, nullable: bool = False, minimum: int = 0) -> None:
    """预算计数不接受布尔、浮点或文本，校验必须早于账本修改。"""
    if value is None and nullable:
        return
    if type(value) is not int or value < minimum:
        raise HarnessError(tr('runtime.budget.invalid', field=field), code='run/invalid-budget')


@dataclass
class BudgetReservation:
    budget: ExecutionBudget
    allocated: int
    max_tokens: int | None
    settled: bool = False

    def settle(self, tokens: int = 0) -> None:
        if self.settled:
            return
        _check_budget_count(tokens, 'tokens')
        self.settled = True
        self.budget.reserved -= self.allocated
        self.budget.consume(tokens)


class ExecutionBudget:
    """同一任务树共享的软预算；并发请求先预留，响应后按用量结算。"""
    def __init__(self, limit: int | None) -> None:
        _check_budget_count(limit, 'limit', nullable=True)
        self.limit = limit
        self.spent = 0
        self.reserved = 0
        self._changed = asyncio.Event()

    def snapshot(self) -> dict[str, int | None]:
        """从唯一账本派生即时余量，预留不当成已经结算的用量。"""
        return {'limit': self.limit, 'spent': self.spent, 'reserved': self.reserved,
                'available': None if self.limit is None else max(0, self.limit - self.spent - self.reserved)}

    async def acquire(self, input_tokens: int, max_tokens: int | None) -> BudgetReservation | None:
        """等待未完成的预算预留，不产生额外调用。"""
        _check_budget_count(input_tokens, 'input_tokens')
        _check_budget_count(max_tokens, 'max_tokens', nullable=True, minimum=1)
        while True:
            available = None if self.limit is None else self.limit - self.spent - self.reserved
            desired = input_tokens + max_tokens if max_tokens else None
            if available is not None and self.reserved and (desired is None or available < desired):
                self._changed.clear()
                await self._changed.wait()
                continue
            if available is not None and available <= input_tokens:
                return None
            output = max_tokens if available is None else min(max_tokens or available, available - input_tokens)
            allocated = input_tokens + (output or 0)
            self.reserved += allocated
            return BudgetReservation(self, allocated, output)

    @property
    def exhausted(self) -> bool:
        return self.limit is not None and self.spent >= self.limit

    def consume(self, tokens: int) -> None:
        _check_budget_count(tokens, 'tokens')
        self.spent += tokens
        self._changed.set()


def operation_fingerprint(tool: str, args: Any, cwd: str | None) -> str:
    return hashlib.sha256(json.dumps([tool, args, cwd], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
