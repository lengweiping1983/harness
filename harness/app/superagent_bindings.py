"""已有 Worker 的 Thread 关联意图；未知关联只回查，不能覆盖后来的取消订阅。"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager

from ..core.cleanup import raise_cleanup_errors
from ..core.errors import HarnessError
from ..core.sqlite import prepare_database
from .superagent_inbox import _error, _text


SCHEMA = '''
CREATE TABLE configurations (instance TEXT PRIMARY KEY, identity TEXT NOT NULL);
CREATE TABLE bindings (
    id TEXT PRIMARY KEY, instance TEXT NOT NULL, task_id TEXT NOT NULL,
    source_id TEXT NOT NULL, event_id TEXT NOT NULL, revision INTEGER NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('applying','applied','unknown','skipped')));
PRAGMA user_version=1;
'''


class SuperagentBindings:
    """保存关联事实，工作运行继续由原投递器的稳定键负责。"""

    def __init__(self, factory):
        self.inbox = factory.inbox
        self.path = factory.path.with_name(factory.path.stem + '.bindings.sqlite3')
        with self._db(initialize=True, write=True) as db:
            row = db.execute('SELECT identity FROM configurations WHERE instance=?', (self.inbox.instance_id,)).fetchone()
            if row is None:
                db.execute('INSERT INTO configurations VALUES(?,?)', (self.inbox.instance_id, factory.identity))
            elif row[0] != factory.identity:
                raise _error('conflict')

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

    def _settle(self, key, state):
        with self._db(write=True) as db:
            db.execute("UPDATE bindings SET state=? WHERE id=? AND state IN ('applying','unknown')", (state, key))

    def _reconcile(self, key, task_id, event):
        bound = self.inbox.subscribed(task_id, source_id=event['source_id'], thread_ids=event['thread_ids'])
        state = 'applied' if bound else 'unknown'
        self._settle(key, state)
        return state

    def attach(self, task_id, event, revision):
        """先认领再进行一次原子订阅；原回执确认失败保留原异常。"""
        raw = json.dumps([self.inbox.instance_id, task_id, event['id']], ensure_ascii=False)
        key = 'superagent-binding:' + hashlib.sha256(raw.encode('utf-8')).hexdigest()
        expected = (self.inbox.instance_id, task_id, event['source_id'], event['event_id'], revision)
        with self._db(write=True) as db:
            row = db.execute('SELECT * FROM bindings WHERE id=?', (key,)).fetchone()
            claimed = row is None
            if claimed:
                db.execute("INSERT INTO bindings VALUES(?,?,?,?,?,?,'applying')", (key, *expected))
            else:
                try:
                    for field in ('task_id','source_id','event_id'):
                        _text(row[field], field)
                    if (row['instance'] != self.inbox.instance_id or type(row['revision']) is not int
                            or not 1 <= row['revision'] <= 9223372036854775807
                            or row['state'] not in ('applying','applied','unknown','skipped')):
                        raise ValueError
                    original = self.inbox.event(source_id=row['source_id'], event_id=row['event_id'])
                    identity = json.dumps([row['instance'], row['task_id'], original['id']], ensure_ascii=False)
                    if key != 'superagent-binding:' + hashlib.sha256(identity.encode('utf-8')).hexdigest():
                        raise ValueError
                except (ValueError, TypeError, HarnessError) as exc:
                    raise _error('corrupt') from exc
                if tuple(row[field] for field in ('instance','task_id','source_id','event_id','revision')) != expected:
                    raise _error('conflict')
        if not claimed:
            return row['state'] if row['state'] in ('applied','skipped') else self._reconcile(key, task_id, event)
        try:
            accepted = self.inbox.subscribe_assigned(task_id, source_id=event['source_id'],
                event_id=event['event_id'], expected_revision=revision)
            state = 'applied' if accepted else 'skipped'
            self._settle(key, state)
            return state
        except BaseException as original:
            errors = [original]
            try:
                self._reconcile(key, task_id, event)
            except BaseException as recovery:
                if recovery is not original:
                    errors.append(recovery)
            raise_cleanup_errors(errors)
