"""将已订阅的真人事件投递给真实执行器；未知提交只回查，不自动重放。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from ..core.cleanup import join_cleanup, raise_cleanup_errors
from ..core.errors import HarnessError
from ..core.language import current_language, resolve_language
from ..core.sqlite import prepare_database
from ..i18n import tr
from .superagent_inbox import SuperagentInbox, _error, _page, _text


SCHEMA = '''
CREATE TABLE deliveries (
    seq INTEGER PRIMARY KEY, instance TEXT NOT NULL, trigger_id TEXT NOT NULL,
    source_id TEXT NOT NULL, event_id TEXT NOT NULL, task_id TEXT NOT NULL,
    session_id TEXT NOT NULL, submission_key TEXT NOT NULL, payload TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('submitting','accepted','unknown')),
    run_id TEXT, UNIQUE(instance,trigger_id,task_id), UNIQUE(instance,submission_key));
PRAGMA user_version=1;
'''


class SuperagentDispatcher:
    """可信宿主显式创建的工作层适配器，不承担 Router 决策或外部发送。"""

    def __init__(self, harness, inbox: SuperagentInbox, *, path: Path | None = None, max_active: int = 3) -> None:
        if type(max_active) is not int or not 1 <= max_active <= 128:
            raise _error('invalid', field='max_active')
        self.harness, self.inbox = harness, inbox
        self.max_active = max_active
        self.path = Path(path) if path is not None else inbox.path.with_name(inbox.path.stem + '.deliveries.sqlite3')
        if self.path.resolve() == inbox.path.resolve():
            raise _error('invalid', field='path')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.closed = False
        with self._db(initialize=True):
            pass

    @contextmanager
    def _db(self, *, initialize=False, write=False):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                if write:
                    db.execute('BEGIN IMMEDIATE')
                prepare_database(db, SCHEMA, initialize=initialize)
                yield db
        finally:
            db.close()

    def _open(self):
        if self.closed or self.harness.runtime.closed or self.harness._close_task is not None:
            raise _error('closed')

    def close(self) -> None:
        """停止后续准入；执行器已接受的运行继续归原宿主管理。"""
        self.closed = True

    def delivery(self, trigger_id: str, task_id: str) -> dict:
        """只读回查投递事实；本方法不提交运行或恢复未知结果。"""
        with self._db() as db:
            row = self._get(db, _text(trigger_id, 'trigger_id'), _text(task_id, 'task_id'))
        if row is None:
            raise _error('event-unavailable')
        return dict(row)

    def deliveries(self, *, after: int = 0, limit: int = 100) -> dict:
        """有界枚举本实例的投递引用，供结果消费器独立跟踪未结束运行。"""
        _page(after, limit)
        with self._db() as db:
            rows = db.execute('SELECT trigger_id,task_id FROM deliveries WHERE instance=? AND seq>? ORDER BY seq LIMIT ?',
                              (self.inbox.instance_id, after, limit)).fetchall()
            items = [dict(self._get(db, row['trigger_id'], row['task_id'])) for row in rows]
        return {'deliveries': items}

    def _get(self, db, trigger_id, task_id):
        row = db.execute('SELECT * FROM deliveries WHERE instance=? AND trigger_id=? AND task_id=?',
                         (self.inbox.instance_id, trigger_id, task_id)).fetchone()
        if row is not None:
            try:
                for field in ('source_id', 'event_id', 'task_id', 'session_id'):
                    _text(row[field], field)
                _text(row['payload'], 'payload', limit=524288)
                if (row['state'] not in {'submitting', 'accepted', 'unknown'}
                        or row['submission_key'] != self._key(row['trigger_id'], row['task_id'])
                        or row['state'] == 'accepted' and not isinstance(row['run_id'], str)
                        or row['state'] != 'accepted' and row['run_id'] is not None):
                    raise ValueError
            except (ValueError, TypeError, HarnessError) as exc:
                raise _error('corrupt') from exc
        return row

    def _key(self, trigger_id, task_id):
        identity = json.dumps([self.inbox.instance_id, trigger_id, task_id], ensure_ascii=False)
        return 'superagent:' + hashlib.sha256(identity.encode('utf-8')).hexdigest()

    def _claim(self, event, route):
        key = self._key(event['id'], route['task_id'])
        with self._db(write=True) as db:
            existing = self._get(db, event['id'], route['task_id'])
            if existing is not None:
                return dict(existing), False
            # 以结构化事件封装来源正文，不能让外部 /goal 等文本成为宿主命令。
            content = {field: event[field] for field in
                       ('id', 'source_id', 'event_id', 'actor_id', 'text', 'thread_ids', 'human', 'explicit')}
            with current_language.set(resolve_language(self.harness.config.language).effective_language):
                payload = tr('superagent.worker_input', event=json.dumps(content, ensure_ascii=False))
            # JSON 转义可能把合法的 64 KiB 来源正文放大六倍，仍保留完整原文。
            _text(payload, 'payload', limit=524288)
            db.execute('''INSERT INTO deliveries(instance,trigger_id,source_id,event_id,task_id,
                session_id,submission_key,payload,state) VALUES(?,?,?,?,?,?,?,?,'submitting')''',
                       (self.inbox.instance_id, event['id'], event['source_id'], event['event_id'], route['task_id'],
                        route['session_id'], key, payload))
            return dict(self._get(db, event['id'], route['task_id'])), True

    def _settle(self, delivery, state, run_id=None):
        with self._db(write=True) as db:
            old = self._get(db, delivery['trigger_id'], delivery['task_id'])
            if old is None or old['submission_key'] != delivery['submission_key']:
                raise _error('conflict')
            if old['state'] == 'accepted':
                if state != 'accepted' or old['run_id'] != run_id:
                    raise _error('conflict')
                return
            db.execute('UPDATE deliveries SET state=?,run_id=? WHERE seq=?', (state, run_id, old['seq']))

    def _reconcile(self, delivery):
        record = self.harness.runtime.find_submission(delivery['session_id'], delivery['submission_key'])
        if record is None:
            if delivery['state'] == 'accepted':
                raise _error('corrupt')
            self._settle(delivery, 'unknown')
            return {'task_id': delivery['task_id'], 'state': 'unknown', 'run_id': None}
        if (record.text != delivery['payload'] or record.session_id != delivery['session_id']
                or record.prompt_input is not None or record.request_id != delivery['submission_key']
                or delivery['state'] == 'accepted' and record.id != delivery['run_id']):
            raise _error('conflict')
        self._settle(delivery, 'accepted', record.id)
        return {'task_id': delivery['task_id'], 'state': 'accepted', 'run_id': record.id}

    def _admitted(self, event, route, expected_revision=None):
        source = self.inbox.source(event['source_id'])
        task = self.inbox.task(route['task_id'])
        # 候选事件不等于授权；机器人、上下文和未决语义判断不能启动执行。
        return (event['disposition'] == 'accepted' and event['human'] and not route['context_only']
                and task['active'] and task['session_id'] == route['session_id']
                and (expected_revision is None or source['revision'] == expected_revision)
                and source['mode'] != 'ignore' and event['actor_id'] in source['actors']
                and all(thread in source['threads'] for thread in event['thread_ids']))

    async def dispatch(self, *, source_id: str, event_id: str) -> dict:
        """事件可并发重复请求；只有首次持久认领者可以调用执行器。"""
        return await self._dispatch(source_id=source_id, event_id=event_id)

    async def dispatch_assigned(self, *, source_id: str, event_id: str, task_id: str,
                                expected_revision: int | None = None) -> dict:
        """可信协调宿主派送未归属的明确真人请求；本入口不授予新权限。"""
        if expected_revision is not None and (type(expected_revision) is not int or not 1 <= expected_revision <= 9223372036854775807):
            raise _error('invalid', field='expected_revision')
        return await self._dispatch(source_id=source_id, event_id=event_id, assigned=_text(task_id, 'task_id'),
                                    expected_revision=expected_revision)

    async def _dispatch(self, *, source_id: str, event_id: str, assigned: str | None = None,
                        expected_revision: int | None = None) -> dict:
        self._open()
        owner = self.harness.runtime.claim_loop(asyncio.get_running_loop())
        if owner is not asyncio.get_running_loop():
            future = asyncio.run_coroutine_threadsafe(
                self._dispatch(source_id=source_id, event_id=event_id, assigned=assigned,
                               expected_revision=expected_revision), owner)
            return await join_cleanup(asyncio.wrap_future(future))
        event = self.inbox.event(source_id=source_id, event_id=event_id)
        routes = [route for route in event['routes'] if route['kind'] == 'worker']
        if assigned is not None:
            if (not event['human'] or not event['explicit']
                    or not any(route['kind'] == 'router' for route in event['routes'])):
                raise _error('invalid', field='assigned_event')
            task = self.inbox.task(assigned)
            routes = [{'task_id': task['id'], 'session_id': task['session_id'], 'context_only': False}]
        result = {'trigger_id': event['id'], 'deliveries': [], 'skipped': [], 'deferred': []}
        if not routes:
            return result
        started = False
        for route in routes:
            with self._db() as db:
                existing = self._get(db, event['id'], route['task_id'])
            # 已认领的投递先回查；后续策略变更不能使已接受的运行失去归属。
            if existing is not None:
                result['deliveries'].append(self._reconcile(dict(existing)))
                continue
            if not self._admitted(event, route, expected_revision):
                result['skipped'].append(route['task_id'])
                continue
            if not started:
                await self.harness.ensure_started()
                self._open()
                started = True
                with self._db() as db:
                    existing = self._get(db, event['id'], route['task_id'])
                if existing is not None:
                    result['deliveries'].append(self._reconcile(dict(existing)))
                    continue
            # 启动等待期间可能撤销策略或关闭任务，认领前重新核对。
            if not self._admitted(event, route, expected_revision):
                result['skipped'].append(route['task_id'])
                continue
            if len(self.harness.runtime.records.active) >= self.max_active:
                result['deferred'].append(route['task_id'])
                continue
            self.harness.get_session(route['session_id'])
            delivery, claimed = self._claim(event, route)
            if not claimed:
                result['deliveries'].append(self._reconcile(delivery))
                continue
            try:
                record = self.harness.runtime.submit(delivery['session_id'], delivery['payload'],
                                                     key=delivery['submission_key'], request_id=delivery['submission_key'])
                self._settle(delivery, 'accepted', record.id)
            except BaseException as original:
                errors = [original]
                try:
                    # 提交或本地确认失败都可能已有持久写入，只回查原键。
                    self._reconcile(delivery)
                except BaseException as reconciliation:
                    if reconciliation is not original:
                        errors.append(reconciliation)
                raise_cleanup_errors(errors)
            result['deliveries'].append({'task_id': route['task_id'], 'state': 'accepted', 'run_id': record.id})
        return result
