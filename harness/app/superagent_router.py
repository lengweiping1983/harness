"""可信宿主显式运行的持久研判与消费循环，复用原运行和投递事实。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from ..core.cleanup import join_cleanup, raise_cleanup_errors
from ..core.errors import HarnessError
from ..core.language import current_language, resolve_language
from ..core.sqlite import prepare_database
from ..i18n import tr
from .superagent_dispatch import SuperagentDispatcher
from .superagent_inbox import _error, _json, _text


SCHEMA = '''
CREATE TABLE coordinators (
    instance TEXT PRIMARY KEY, identity TEXT NOT NULL, cursor INTEGER NOT NULL);
CREATE TABLE batches (
    id TEXT PRIMARY KEY, instance TEXT NOT NULL, items TEXT NOT NULL,
    payload TEXT NOT NULL, state TEXT NOT NULL
    CHECK(state IN ('ready','submitting','accepted','unknown','decided','rejected')),
    run_id TEXT, decision TEXT, reason TEXT);
CREATE INDEX batches_instance_state ON batches(instance,state);
CREATE TABLE inputs (
    seq INTEGER PRIMARY KEY, instance TEXT NOT NULL, trigger_id TEXT NOT NULL,
    source_id TEXT NOT NULL, event_id TEXT NOT NULL, batch_id TEXT,
    target TEXT, revision INTEGER, state TEXT NOT NULL
    CHECK(state IN ('waiting','pending','settled','unknown','skipped','rejected','ignored')),
    UNIQUE(instance,trigger_id), FOREIGN KEY(batch_id) REFERENCES batches(id));
CREATE INDEX inputs_instance_state_seq ON inputs(instance,state,seq);
PRAGMA user_version=1;
'''


class SuperagentRouter:
    """模型仅研判归属；执行由宿主白名单、当前来源策略及原投递器约束。"""

    def __init__(self, dispatcher: SuperagentDispatcher, router_harness, *, session_id: str,
                 workers: dict[str, str], path: Path | None = None, batch_size: int = 8,
                 worker_factory=None) -> None:
        self.dispatcher, self.inbox = dispatcher, dispatcher.inbox
        self.harness = router_harness
        self.session_id = _text(session_id, 'router_session_id')
        self.closed = False
        self.results = None
        self.worker_factory = worker_factory
        if worker_factory is not None and (worker_factory.harness is not dispatcher.harness
                or worker_factory.inbox.instance_id != self.inbox.instance_id
                or worker_factory.inbox.path.resolve() != self.inbox.path.resolve()
                or worker_factory.owner != self.session_id):
            raise _error('invalid', field='worker_factory')
        if type(batch_size) is not int or not 1 <= batch_size <= 16:
            raise _error('invalid', field='batch_size')
        self.batch_size = batch_size
        if not isinstance(workers, dict) or not (0 if worker_factory else 1) <= len(workers) <= 128:
            raise _error('invalid', field='workers')
        self.workers = {_text(key, 'task_id'): _text(value, 'worker_description', limit=4096)
                        for key, value in workers.items()}
        if worker_factory is not None and set(self.workers) & set(worker_factory.catalog()):
            raise _error('invalid', field='workers')
        self._role()
        self.harness.get_session(self.session_id)
        worker_bindings = {key: self.inbox.task(key)['session_id'] for key in self.workers}
        for worker_session in worker_bindings.values():
            dispatcher.harness.get_session(worker_session)
        self.path = Path(path) if path is not None else self.inbox.path.with_name(
            self.inbox.path.stem + '.router.sqlite3')
        if self.path.resolve() in {self.inbox.path.resolve(), dispatcher.path.resolve()}:
            raise _error('invalid', field='path')
        binding = {'router_home': str(self.harness.config.home.resolve()),
            'worker_home': str(dispatcher.harness.config.home.resolve()), 'session_id': self.session_id,
            'inbox': str(self.inbox.path.resolve()), 'deliveries': str(dispatcher.path.resolve()),
            'workers': self.workers, 'bindings': worker_bindings}
        if worker_factory is not None:
            binding['worker_factory'] = {'identity': worker_factory.identity, 'path': str(worker_factory.path.resolve())}
        identity = json.dumps(binding, ensure_ascii=False, sort_keys=True)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db(initialize=True, write=True) as db:
            old = db.execute('SELECT * FROM coordinators WHERE instance=?', (self.inbox.instance_id,)).fetchone()
            if old is None:
                db.execute('INSERT INTO coordinators VALUES(?,?,0)', (self.inbox.instance_id, identity))
            elif old['identity'] != identity:
                raise _error('conflict')
        self.harness._agent_for(self.harness.get_session(self.session_id)).tools = self.harness.tools.restricted([])

    def _role(self):
        # 独立宿主关闭会解释来源正文的技能、团队和外部运行入口；工具范围不是系统沙箱。
        if (self.harness is self.dispatcher.harness
                or self.harness.config.home.resolve() == self.dispatcher.harness.config.home.resolve()
                or any(getattr(self.harness.config, name) for name in
                       ('enable_agents', 'enable_skills', 'enable_goal', 'enable_plan_mode'))
                or getattr(self.harness, 'agent_mount', None) is not None):
            raise _error('invalid', field='router_harness')

    def _open(self):
        self.dispatcher._open()
        if self.closed or self.harness.runtime.closed or self.harness._close_task is not None:
            raise _error('closed')
        self._role()

    def close(self) -> None:
        """停止消费准入，不取消两个宿主已接受的运行。"""
        self.closed = True

    @contextmanager
    def _db(self, *, initialize=False, write=False):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA foreign_keys=ON')
            with db:
                if write:
                    db.execute('BEGIN IMMEDIATE')
                prepare_database(db, SCHEMA, initialize=initialize)
                yield db
        finally:
            db.close()

    def _key(self, payload):
        identity = json.dumps([self.inbox.instance_id, self.session_id, payload], ensure_ascii=False)
        return 'superagent-router:' + hashlib.sha256(identity.encode('utf-8')).hexdigest()

    def _catalog(self, source_ids=()):
        base = {**self.workers, **(self.worker_factory.catalog() if self.worker_factory else {})}
        existing = self.worker_factory.ready_catalog(source_ids) if self.worker_factory and source_ids else {}
        if set(base) & set(existing):
            raise _error('conflict')
        return {**base, **existing}

    def _frame(self, batch):
        """验证冻结名单本身；后来新增或停止 Worker 不改写已经提交的模型输入。"""
        try:
            frame = _json(batch['payload'].rsplit('\n\n', 1)[1])
            if (not isinstance(frame, dict) or set(frame) != {'events', 'workers'}
                    or frame['events'] != _json(batch['items']) or not isinstance(frame['workers'], dict)
                    or len(frame['workers']) > len(self.workers) + 160
                    or batch['id'] != self._key(batch['payload'])
                    or not batch['payload'].endswith('\n\n' + json.dumps(frame, ensure_ascii=False))):
                raise ValueError
            base = self._catalog()
            if any(frame['workers'].get(key) != value for key, value in base.items()):
                raise ValueError
            for task_id, description in frame['workers'].items():
                if task_id not in base and (self.worker_factory is None
                        or self.worker_factory.routing_entry(task_id) != description):
                    raise ValueError
            return frame
        except (ValueError, TypeError, IndexError, KeyError, RecursionError, HarnessError) as exc:
            raise _error('corrupt') from exc

    def _collect(self):
        # 固定批次、消费游标和输入意图一起提交；不在事务内调用模型或运行执行器。
        with self._db(write=True) as db:
            if db.execute("SELECT 1 FROM batches WHERE instance=? AND state IN ('ready','submitting','accepted','unknown') LIMIT 1",
                          (self.inbox.instance_id,)).fetchone():
                return
            if db.execute("SELECT count(*) FROM inputs WHERE instance=? AND state='pending'",
                          (self.inbox.instance_id,)).fetchone()[0] >= 128:
                return
            cursor = db.execute('SELECT cursor FROM coordinators WHERE instance=?',
                                (self.inbox.instance_id,)).fetchone()[0]
            if type(cursor) is not int or not 0 <= cursor <= 9223372036854775807:
                raise _error('corrupt')
            events = self.inbox.events(after=cursor, limit=self.batch_size)['events']
            if not events:
                return
            candidates = []
            for event in events:
                if event['disposition'] != 'accepted':
                    continue
                if any(route['kind'] == 'worker' and not route['context_only'] for route in event['routes']):
                    db.execute("INSERT INTO inputs(instance,trigger_id,source_id,event_id,state) VALUES(?,?,?,?,'pending')",
                               (self.inbox.instance_id, event['id'], event['source_id'], event['event_id']))
                elif event['human'] and event['explicit']:
                    source = self.inbox.source(event['source_id'])
                    if (source['mode'] == 'ignore' or event['actor_id'] not in source['actors']
                            or any(thread not in source['threads'] for thread in event['thread_ids'])):
                        continue
                    candidates.append({**{key: event[key] for key in
                        ('id', 'source_id', 'event_id', 'actor_id', 'text', 'thread_ids')},
                        'revision': source['revision']})
            if candidates:
                frame = {'events': candidates, 'workers': self._catalog({item['source_id'] for item in candidates})}
                with current_language.set(resolve_language(self.harness.config.language).effective_language):
                    payload = tr('superagent.router_create_input' if self.worker_factory else 'superagent.router_input',
                                 batch=json.dumps(frame, ensure_ascii=False))
                _text(payload, 'router_payload', limit=8388608)
                key = self._key(payload)
                db.execute("INSERT INTO batches VALUES(?,?,?,?,'ready',NULL,NULL,NULL)",
                           (key, self.inbox.instance_id, json.dumps(candidates, ensure_ascii=False), payload))
                db.executemany("""INSERT INTO inputs(instance,trigger_id,source_id,event_id,batch_id,revision,state)
                    VALUES(?,?,?,?,?,?,'waiting')""", [(self.inbox.instance_id, event['id'], event['source_id'],
                    event['event_id'], key, event['revision']) for event in candidates])
            db.execute('UPDATE coordinators SET cursor=? WHERE instance=?', (events[-1]['seq'], self.inbox.instance_id))

    def _batch(self):
        with self._db() as db:
            row = db.execute("SELECT * FROM batches WHERE instance=? AND state IN ('ready','submitting','accepted','unknown') LIMIT 1",
                             (self.inbox.instance_id,)).fetchone()
        if row is None:
            return None
        _text(row['payload'], 'payload', limit=8388608)
        if row['id'] != self._key(row['payload']):
            raise _error('corrupt')
        try:
            if row['state'] == 'accepted':
                _text(row['run_id'], 'run_id')
            elif row['run_id'] is not None:
                raise ValueError
            items = json.loads(row['items'])
            if not isinstance(items, list) or not 1 <= len(items) <= 16:
                raise ValueError
            seen = set()
            for item in items:
                if (not isinstance(item, dict) or set(item) !=
                        {'id', 'source_id', 'event_id', 'actor_id', 'text', 'thread_ids', 'revision'}
                        or type(item['revision']) is not int or not 1 <= item['revision'] <= 9223372036854775807):
                    raise ValueError
                event = self.inbox.event(source_id=item['source_id'], event_id=item['event_id'])
                if item['id'] in seen or any(item[key] != event[key] for key in item if key != 'revision'):
                    raise ValueError
                seen.add(item['id'])
            self._frame(row)
            with self._db() as db:
                inputs = db.execute('SELECT trigger_id,source_id,event_id,revision FROM inputs WHERE batch_id=?', (row['id'],)).fetchall()
            expected = {(item['id'], item['source_id'], item['event_id'], item['revision']) for item in items}
            if {tuple(item) for item in inputs} != expected:
                raise ValueError
        except (ValueError, TypeError, KeyError, RecursionError, HarnessError) as exc:
            raise _error('corrupt') from exc
        return dict(row)

    def _reconcile(self, batch):
        record = self.harness.runtime.find_submission(self.session_id, batch['id'])
        if record is not None and (record.text != batch['payload'] or record.request_id != batch['id']
                or record.prompt_input is not None or batch['run_id'] is not None and batch['run_id'] != record.id):
            raise _error('conflict')
        if record is None and batch['state'] == 'accepted':
            raise _error('corrupt')
        with self._db(write=True) as db:
            db.execute("""UPDATE batches SET state=?,run_id=? WHERE id=?
                AND state IN ('submitting','accepted','unknown')""",
                       ('accepted' if record else 'unknown', record.id if record else None, batch['id']))
        return record

    def _decision(self, batch, record):
        if not record.settled:
            return
        reason, assignments = None, None
        if record.status != 'completed':
            reason = 'run/' + record.status
        else:
            try:
                _text(record.output, 'router_output', limit=65536)
                value = _json(record.output)
                events = json.loads(batch['items'])
                roster = self._frame(batch)['workers']
                expected = {event['id'] for event in events}
                if (not isinstance(value, dict) or set(value) != {'assignments'}
                        or not isinstance(value['assignments'], list) or len(value['assignments']) != len(expected)):
                    raise ValueError
                assignments = {}
                for item in value['assignments']:
                    if (not isinstance(item, dict) or set(item) != {'trigger_id', 'task_id'}
                            or not isinstance(item['trigger_id'], str) or item['trigger_id'] not in expected
                            or item['trigger_id'] in assignments or item['task_id'] is not None
                            and (not isinstance(item['task_id'], str) or item['task_id'] not in roster)):
                        raise ValueError
                    target = item['task_id']
                    if self.worker_factory and target in self.worker_factory.catalog():
                        target = {'task_id': self.worker_factory.task_id(batch['id'], item['trigger_id']),
                                  'profile_id': target[4:]}
                    assignments[item['trigger_id']] = target
            except (ValueError, TypeError, RecursionError, HarnessError):
                reason = 'superagent/router-output'
        with self._db(write=True) as db:
            changed = db.execute("UPDATE batches SET state=?,decision=?,reason=? WHERE id=? AND state='accepted' AND run_id=?",
                ('rejected' if reason else 'decided', json.dumps(assignments), reason, batch['id'], record.id)).rowcount
            if changed:
                if reason:
                    db.execute("UPDATE inputs SET state='rejected' WHERE batch_id=? AND state='waiting'", (batch['id'],))
                else:
                    for trigger_id, target in assignments.items():
                        task_id = target['task_id'] if isinstance(target, dict) else target
                        db.execute("UPDATE inputs SET target=?,state=? WHERE instance=? AND trigger_id=? AND batch_id=? AND state='waiting'",
                                   (task_id, 'ignored' if target is None else 'pending', self.inbox.instance_id, trigger_id, batch['id']))

    async def _poll(self):
        batch = self._batch()
        if batch is None:
            return
        record = None
        if batch['state'] == 'ready':
            active = self.harness.runtime.active.get(self.session_id)
            if active is not None and not active.done():
                return
            await self.harness.ensure_started()
            self._open()
            self.harness._agent_for(self.harness.get_session(self.session_id)).tools = self.harness.tools.restricted([])
            with self._db(write=True) as db:
                claimed = db.execute("UPDATE batches SET state='submitting' WHERE id=? AND state='ready'", (batch['id'],)).rowcount
            if claimed:
                try:
                    record = self.harness.runtime.submit(self.session_id, batch['payload'], key=batch['id'], request_id=batch['id'])
                    batch['state'] = 'submitting'
                    self._reconcile(batch)
                except BaseException as original:
                    errors = [original]
                    try:
                        batch['state'] = 'submitting'
                        self._reconcile(batch)
                    except BaseException as reconciliation:
                        if reconciliation is not original:
                            errors.append(reconciliation)
                    raise_cleanup_errors(errors)
        else:
            record = self._reconcile(batch)
        if record is not None:
            self._decision(batch, record)

    async def _drain(self):
        with self._db() as db:
            rows = db.execute("SELECT * FROM inputs WHERE instance=? AND state='pending' ORDER BY seq LIMIT 16",
                              (self.inbox.instance_id,)).fetchall()
        for row in rows:
            self._open()
            event = self.inbox.event(source_id=row['source_id'], event_id=row['event_id'])
            if row['trigger_id'] != event['id']:
                raise _error('corrupt')
            if row['batch_id'] is not None:
                with self._db() as db:
                    batch = db.execute("SELECT * FROM batches WHERE id=? AND instance=? AND state='decided'",
                                       (row['batch_id'], self.inbox.instance_id)).fetchone()
                try:
                    choice = json.loads(batch['decision']).get(row['trigger_id']) if batch is not None else None
                    roster = self._frame(batch)['workers'] if batch is not None else {}
                    creating = isinstance(choice, dict)
                    if (batch is None or type(row['revision']) is not int
                            or not 1 <= row['revision'] <= 9223372036854775807
                            or not creating and (row['target'] not in roster or choice != row['target']
                                or row['target'] not in self.workers and self.worker_factory is None)
                            or creating and (self.worker_factory is None or set(choice) != {'task_id', 'profile_id'}
                                or choice['task_id'] != row['target'] or choice['profile_id'] not in self.worker_factory.profiles
                                or choice['task_id'] != self.worker_factory.task_id(row['batch_id'], row['trigger_id']))):
                        raise ValueError
                except (ValueError, TypeError, AttributeError, RecursionError) as exc:
                    raise _error('corrupt') from exc
                if creating:
                    if len(self.dispatcher.harness.runtime.records.active) >= self.dispatcher.max_active:
                        continue
                    created = await self.worker_factory.ensure(batch_id=row['batch_id'],
                        item={'id': row['trigger_id'], 'source_id': row['source_id'], 'event_id': row['event_id'],
                              'revision': row['revision']}, profile_id=choice['profile_id'])
                    if created['state'] != 'ready':
                        if created['state'] in ('skipped', 'unknown'):
                            with self._db(write=True) as db:
                                db.execute("UPDATE inputs SET state=? WHERE seq=? AND state='pending'", (created['state'], row['seq']))
                        continue
                elif row['target'] not in self.workers:
                    linked = self.worker_factory.attach(row['target'], source_id=row['source_id'],
                        event_id=row['event_id'], revision=row['revision'])
                    if linked != 'applied':
                        with self._db(write=True) as db:
                            db.execute("UPDATE inputs SET state=? WHERE seq=? AND state='pending'",
                                       ('unknown' if linked == 'unknown' else 'skipped', row['seq']))
                        continue
                result = await self.dispatcher.dispatch_assigned(source_id=row['source_id'],
                    event_id=row['event_id'], task_id=row['target'], expected_revision=row['revision'])
            else:
                if row['target'] is not None or row['revision'] is not None:
                    raise _error('corrupt')
                result = await self.dispatcher.dispatch(source_id=row['source_id'], event_id=row['event_id'])
            if result['deferred']:
                continue
            state = 'unknown' if any(item['state'] == 'unknown' for item in result['deliveries']) else 'settled'
            if state != 'unknown' and result['skipped']:
                state = 'skipped'
            with self._db(write=True) as db:
                db.execute("UPDATE inputs SET state=? WHERE seq=? AND state='pending'", (state, row['seq']))

    async def tick(self) -> dict:
        """有界推进一次；仅认领者提交，重复调用和重启只回查原提交键。"""
        self._open()
        loop = asyncio.get_running_loop()
        owner = self.dispatcher.harness.runtime.claim_loop(loop)
        if owner is not loop:
            future = asyncio.run_coroutine_threadsafe(self.tick(), owner)
            return await join_cleanup(asyncio.wrap_future(future))
        if self.harness.runtime.claim_loop(loop) is not loop:
            raise _error('invalid', field='router_loop')
        if self.results is not None:
            await self.results.tick()
        await self._drain()
        await self._poll()
        self._collect()
        await self._poll()
        await self._drain()
        return self.status()

    def status(self) -> dict:
        """读取协调事实，不把运行受理或模型选择投影为业务完成。"""
        with self._db() as db:
            cursor = db.execute('SELECT cursor FROM coordinators WHERE instance=?', (self.inbox.instance_id,)).fetchone()[0]
            inputs = dict(db.execute('SELECT state,count(*) FROM inputs WHERE instance=? GROUP BY state', (self.inbox.instance_id,)))
            batches = [dict(row) for row in db.execute('SELECT id,state,run_id,reason FROM batches WHERE instance=? ORDER BY rowid DESC LIMIT 8',
                                                      (self.inbox.instance_id,))]
        return {'instance_id': self.inbox.instance_id, 'cursor': cursor, 'inputs': inputs, 'batches': batches,
                **({'workers': self.worker_factory.status()} if self.worker_factory else {}),
                **({'results': self.results.status()} if self.results is not None else {})}

    async def serve(self, stop: asyncio.Event, *, interval: float = 0.5) -> None:
        """显式等待的消费循环；取消等待不撤销已经受理的模型或工作运行。"""
        if isinstance(interval, bool) or not isinstance(interval, (int, float)) or not math.isfinite(interval) or not 0.01 <= interval <= 60:
            raise _error('invalid', field='interval')
        while not self.closed and not stop.is_set():
            await self.tick()
            try:
                await asyncio.wait_for(stop.wait(), interval)
            except TimeoutError:
                pass
