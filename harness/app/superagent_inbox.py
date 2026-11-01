"""可信宿主接入外部事件的持久入口；此层只路由，不执行工具或启动对话。"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ..core.errors import HarnessError
from ..core.sqlite import prepare_database
from ..i18n import tr


SCHEMA = '''
CREATE TABLE sources (
    instance TEXT NOT NULL, id TEXT NOT NULL, mode TEXT NOT NULL,
    actors TEXT NOT NULL, threads TEXT NOT NULL, revision INTEGER NOT NULL,
    PRIMARY KEY(instance,id));
CREATE TABLE tasks (
    instance TEXT NOT NULL, id TEXT NOT NULL, session_id TEXT NOT NULL,
    active INTEGER NOT NULL CHECK(active IN (0,1)), PRIMARY KEY(instance,id),
    UNIQUE(instance,session_id));
CREATE TABLE subscriptions (
    instance TEXT NOT NULL, source TEXT NOT NULL, thread TEXT NOT NULL,
    task TEXT NOT NULL, context_only INTEGER NOT NULL CHECK(context_only IN (0,1)),
    PRIMARY KEY(instance,source,thread,task),
    FOREIGN KEY(instance,source) REFERENCES sources(instance,id),
    FOREIGN KEY(instance,task) REFERENCES tasks(instance,id));
CREATE TABLE triggers (
    seq INTEGER PRIMARY KEY, id TEXT NOT NULL UNIQUE, instance TEXT NOT NULL,
    source TEXT NOT NULL, remote_id TEXT NOT NULL, actor TEXT NOT NULL,
    text TEXT NOT NULL, threads TEXT NOT NULL,
    human INTEGER NOT NULL CHECK(human IN (0,1)),
    explicit INTEGER NOT NULL CHECK(explicit IN (0,1)),
    disposition TEXT NOT NULL, policy_revision INTEGER NOT NULL,
    UNIQUE(instance,source,remote_id),
    FOREIGN KEY(instance,source) REFERENCES sources(instance,id));
CREATE TABLE routes (
    trigger_id TEXT NOT NULL, target TEXT NOT NULL,
    context_only INTEGER NOT NULL CHECK(context_only IN (0,1)),
    PRIMARY KEY(trigger_id,target), FOREIGN KEY(trigger_id) REFERENCES triggers(id));
CREATE INDEX triggers_instance_seq ON triggers(instance,seq);
CREATE INDEX routes_target ON routes(target,trigger_id);
PRAGMA user_version=1;
'''


def _error(code: str, **values: Any) -> HarnessError:
    return HarnessError(tr('superagent.' + code, **values), code='superagent/' + code)


def _json(raw):
    """拒绝重复对象键和非标准数值，不从歧义输入制造决策。"""
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError
            result[key] = value
        return result
    def invalid(_):
        raise ValueError
    return json.loads(raw, object_pairs_hook=unique, parse_constant=invalid)


def _text(value: Any, field: str, *, limit: int = 256, empty: bool = False) -> str:
    try:
        valid = (isinstance(value, str) and '\0' not in value
                 and (empty or bool(value.strip())) and len(value.encode('utf-8')) <= limit)
    except UnicodeError:
        valid = False
    if not valid:
        raise _error('invalid', field=field)
    return value


def _names(value: Any, field: str, *, maximum: int = 256, nonempty: bool = False) -> list[str]:
    if not isinstance(value, (list, tuple)) or not int(nonempty) <= len(value) <= maximum:
        raise _error('invalid', field=field)
    result = [_text(item, field) for item in value]
    if len(set(result)) != len(result):
        raise _error('invalid', field=field)
    return sorted(result)


def _page(after: Any, limit: Any) -> None:
    if type(after) is not int or not 0 <= after < 9223372036854775807:
        raise _error('invalid', field='after')
    if type(limit) is not int or not 1 <= limit <= 100:
        raise _error('invalid', field='limit')


def _array(raw: Any, field: str, *, maximum: int = 256, nonempty: bool = False) -> list[str]:
    if not isinstance(raw, str) or len(raw) > maximum * 1540 + 2:
        raise _error('corrupt')
    return _names(json.loads(raw), field, maximum=maximum, nonempty=nonempty)


class SuperagentInbox:
    """按实例隔离事件和订阅；接收、去重与分发意图在同一事务提交。"""

    def __init__(self, path: Path, *, instance_id: str) -> None:
        self.instance_id = _text(instance_id, 'instance_id')
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db(initialize=True):
            pass

    @contextmanager
    def _db(self, *, initialize: bool = False, write: bool = False):
        # 每次持有独立连接，跨线程和多实例竞争由 SQLite 写事务裁决。
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

    def configure_source(self, source_id: str, *, mode: str, actors: list[str],
                         threads: list[str], expected_revision: int = 0) -> dict[str, Any]:
        """显式白名单，不接受通配范围；版本比较避免覆盖其他维护者的策略。"""
        source_id = _text(source_id, 'source_id')
        if not isinstance(mode, str) or mode not in {'ignore', 'requests', 'candidates'}:
            raise _error('invalid', field='mode')
        actors, threads = _names(actors, 'actors'), _names(threads, 'threads')
        if type(expected_revision) is not int or not 0 <= expected_revision < 9223372036854775807:
            raise _error('invalid', field='expected_revision')
        with self._db(write=True) as db:
            old = db.execute('SELECT revision FROM sources WHERE instance=? AND id=?',
                             (self.instance_id, source_id)).fetchone()
            if old is not None:
                self._source(db, source_id)
            if (old['revision'] if old else 0) != expected_revision:
                raise _error('conflict')
            revision = expected_revision + 1
            db.execute('''INSERT INTO sources VALUES(?,?,?,?,?,?)
                ON CONFLICT(instance,id) DO UPDATE SET mode=excluded.mode,
                actors=excluded.actors,threads=excluded.threads,revision=excluded.revision''',
                       (self.instance_id, source_id, mode, json.dumps(actors), json.dumps(threads), revision))
        return {'id': source_id, 'mode': mode, 'actors': actors, 'threads': threads, 'revision': revision}

    def register_task(self, task_id: str, *, session_id: str) -> dict[str, Any]:
        """只登记宿主已建立的对话引用，不创建对话或授予执行权限。"""
        task_id, session_id = _text(task_id, 'task_id'), _text(session_id, 'session_id')
        with self._db(write=True) as db:
            previous = db.execute('SELECT * FROM tasks WHERE instance=? AND (id=? OR session_id=?)',
                                  (self.instance_id, task_id, session_id)).fetchall()
            if previous:
                if (len(previous) != 1 or previous[0]['id'] != task_id
                        or previous[0]['session_id'] != session_id or previous[0]['active'] != 1):
                    raise _error('conflict')
            else:
                db.execute('INSERT INTO tasks VALUES(?,?,?,1)', (self.instance_id, task_id, session_id))
        return {'id': task_id, 'session_id': session_id, 'active': True}

    def _task(self, db, task_id: str, *, active: bool = True):
        row = db.execute('SELECT * FROM tasks WHERE instance=? AND id=?',
                         (self.instance_id, task_id)).fetchone()
        if row is None or active and row['active'] != 1:
            raise _error('task-unavailable')
        try:
            _text(row['session_id'], 'session_id')
            if type(row['active']) is not int or row['active'] not in (0, 1):
                raise ValueError
        except (ValueError, HarnessError) as exc:
            raise _error('corrupt') from exc
        return row

    def task(self, task_id: str) -> dict[str, Any]:
        task_id = _text(task_id, 'task_id')
        with self._db() as db:
            row = self._task(db, task_id, active=False)
            return {'id': row['id'], 'session_id': row['session_id'], 'active': bool(row['active'])}

    def source(self, source_id: str) -> dict[str, Any]:
        source_id = _text(source_id, 'source_id')
        with self._db() as db:
            row = self._source(db, source_id)
            return {key: row[key] for key in ('id', 'mode', 'actors', 'threads', 'revision')}

    def subscribe(self, task_id: str, *, source_id: str, thread_id: str,
                  context_only: bool = False) -> None:
        """订阅不扩大来源白名单；上下文订阅不取得事件的处理归属。"""
        task_id, source_id, thread_id = (_text(value, field) for value, field in
                                        ((task_id, 'task_id'), (source_id, 'source_id'), (thread_id, 'thread_id')))
        if type(context_only) is not bool:
            raise _error('invalid', field='context_only')
        with self._db(write=True) as db:
            self._task(db, task_id)
            source = self._source(db, source_id)
            if thread_id not in source['threads']:
                raise _error('thread-denied')
            db.execute('''INSERT INTO subscriptions VALUES(?,?,?,?,?)
                ON CONFLICT(instance,source,thread,task) DO UPDATE SET context_only=excluded.context_only''',
                       (self.instance_id, source_id, thread_id, task_id, int(context_only)))

    def unsubscribe(self, task_id: str, *, source_id: str, thread_id: str) -> None:
        task_id, source_id, thread_id = (_text(value, field) for value, field in
                                        ((task_id, 'task_id'), (source_id, 'source_id'), (thread_id, 'thread_id')))
        with self._db(write=True) as db:
            self._task(db, task_id, active=False)
            db.execute('DELETE FROM subscriptions WHERE instance=? AND source=? AND thread=? AND task=?',
                       (self.instance_id, source_id, thread_id, task_id))

    def subscribe_assigned(self, task_id: str, *, source_id: str, event_id: str,
                           expected_revision: int) -> bool:
        """可信协调者把明确请求的全部 Thread 原子关联到已有任务，事务内复核权限。"""
        task_id, source_id, event_id = (_text(value, field) for value, field in
            ((task_id, 'task_id'), (source_id, 'source_id'), (event_id, 'event_id')))
        if type(expected_revision) is not int or not 1 <= expected_revision <= 9223372036854775807:
            raise _error('invalid', field='expected_revision')
        with self._db(write=True) as db:
            row = db.execute('SELECT * FROM triggers WHERE instance=? AND source=? AND remote_id=?',
                             (self.instance_id, source_id, event_id)).fetchone()
            if row is None:
                raise _error('event-unavailable')
            event = self._view(db, row)
            source = self._source(db, source_id)
            task = self._task(db, task_id, active=False)
            if (not task['active'] or event['disposition'] != 'accepted' or not event['human'] or not event['explicit']
                    or not any(route['kind'] == 'router' for route in event['routes'])
                    or source['revision'] != expected_revision or source['mode'] == 'ignore'
                    or event['actor_id'] not in source['actors']
                    or any(thread not in source['threads'] for thread in event['thread_ids'])):
                return False
            db.executemany('''INSERT INTO subscriptions VALUES(?,?,?,?,0)
                ON CONFLICT(instance,source,thread,task) DO UPDATE SET context_only=0''',
                [(self.instance_id, source_id, thread, task_id) for thread in event['thread_ids']])
        return True

    def subscribed(self, task_id: str, *, source_id: str, thread_ids: list[str]) -> bool:
        """只读核对全部工作订阅；不创建订阅、不推断原操作是否从未执行。"""
        task_id, source_id = _text(task_id, 'task_id'), _text(source_id, 'source_id')
        threads = _names(thread_ids, 'thread_ids', maximum=16, nonempty=True)
        with self._db() as db:
            self._task(db, task_id, active=False)
            return all(db.execute('''SELECT 1 FROM subscriptions
                WHERE instance=? AND source=? AND thread=? AND task=? AND context_only=0''',
                (self.instance_id, source_id, thread, task_id)).fetchone() is not None for thread in threads)

    def stop_task(self, task_id: str) -> None:
        """撤销后续路由，不清除历史接收事实，不停止执行器中可能已接受的运行。"""
        task_id = _text(task_id, 'task_id')
        with self._db(write=True) as db:
            self._task(db, task_id, active=False)
            db.execute('UPDATE tasks SET active=0 WHERE instance=? AND id=?', (self.instance_id, task_id))
            db.execute('DELETE FROM subscriptions WHERE instance=? AND task=?', (self.instance_id, task_id))

    def _source(self, db, source_id: str) -> dict[str, Any]:
        row = db.execute('SELECT * FROM sources WHERE instance=? AND id=?',
                         (self.instance_id, source_id)).fetchone()
        if row is None:
            raise _error('source-unavailable')
        try:
            actors = _array(row['actors'], 'actors')
            threads = _array(row['threads'], 'threads')
            if (row['mode'] not in {'ignore', 'requests', 'candidates'} or type(row['revision']) is not int
                    or not 1 <= row['revision'] <= 9223372036854775807):
                raise ValueError
        except (ValueError, TypeError, RecursionError, HarnessError) as exc:
            raise _error('corrupt') from exc
        return {**dict(row), 'actors': actors, 'threads': threads}

    def _follows_work(self, db, source_id: str, threads: list[str]) -> bool:
        placeholders = ','.join('?' for _ in threads)
        return db.execute(f'''SELECT 1 FROM subscriptions s JOIN tasks t
            ON t.instance=s.instance AND t.id=s.task WHERE s.instance=? AND s.source=?
            AND s.thread IN ({placeholders}) AND s.context_only=0 AND t.active=1 LIMIT 1''',
                          (self.instance_id, source_id, *threads)).fetchone() is not None

    def _targets(self, db, source_id: str, threads: list[str]) -> dict[str, int]:
        targets: dict[str, int] = {}
        for thread in threads:
            for row in db.execute('''SELECT s.task,s.context_only FROM subscriptions s JOIN tasks t
                    ON t.instance=s.instance AND t.id=s.task
                    WHERE s.instance=? AND s.source=? AND s.thread=? AND t.active=1 LIMIT 129''',
                                  (self.instance_id, source_id, thread)):
                if row['context_only'] not in (0, 1):
                    raise _error('corrupt')
                targets[row['task']] = min(targets.get(row['task'], 1), row['context_only'])
                if len(targets) > 128:
                    raise _error('capacity')
        return targets

    def receive(self, *, source_id: str, event_id: str, actor_id: str, text: str,
                thread_ids: list[str], human: bool, explicit: bool) -> dict[str, Any]:
        """来源身份和指向性须由可信适配器确认；此方法不验证外部签名。"""
        source_id, event_id, actor_id = (_text(value, field) for value, field in
                                        ((source_id, 'source_id'), (event_id, 'event_id'), (actor_id, 'actor_id')))
        text = _text(text, 'text', limit=65536, empty=True)
        threads = _names(thread_ids, 'thread_ids', maximum=16, nonempty=True)
        if type(human) is not bool or type(explicit) is not bool:
            raise _error('invalid', field='human/explicit')
        raw = (actor_id, text, json.dumps(threads), int(human), int(explicit))
        identity = json.dumps([self.instance_id, source_id, event_id], ensure_ascii=False)
        trigger_id = 'trigger_' + hashlib.sha256(identity.encode('utf-8')).hexdigest()
        with self._db(write=True) as db:
            old = db.execute('SELECT * FROM triggers WHERE instance=? AND source=? AND remote_id=?',
                             (self.instance_id, source_id, event_id)).fetchone()
            if old is not None:
                self._validate_trigger(old)
                if tuple(old[key] for key in ('actor', 'text', 'threads', 'human', 'explicit')) != raw:
                    raise _error('event-conflict')
                return self._receipt(db, old, duplicate=True)
            source = self._source(db, source_id)
            targets: dict[str, int] = {}
            if source['mode'] == 'ignore':
                disposition = 'source_ignored'
            elif actor_id not in source['actors']:
                disposition = 'actor_denied'
            elif any(thread not in source['threads'] for thread in threads):
                disposition = 'thread_denied'
            elif source['mode'] == 'requests' and (not human or not explicit and not self._follows_work(db, source_id, threads)):
                disposition = 'request_required'
            else:
                targets = self._targets(db, source_id, threads)
                disposition = 'accepted'
            db.execute('''INSERT INTO triggers(id,instance,source,remote_id,actor,text,threads,human,explicit,
                disposition,policy_revision) VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                       (trigger_id, self.instance_id, source_id, event_id, *raw, disposition, source['revision']))
            if disposition == 'accepted':
                # 同一任务订阅多个关联流时只投递一次；任一工作订阅优先于上下文订阅。
                if not any(context == 0 for context in targets.values()):
                    targets[''] = 0
                db.executemany('INSERT INTO routes VALUES(?,?,?)',
                               [(trigger_id, target, context) for target, context in targets.items()])
            row = db.execute('SELECT * FROM triggers WHERE id=?', (trigger_id,)).fetchone()
            return self._receipt(db, row, duplicate=False)

    def _validate_trigger(self, row) -> list[str]:
        try:
            for key in ('source', 'remote_id', 'actor'):
                _text(row[key], key)
            _text(row['text'], 'text', limit=65536, empty=True)
            threads = _array(row['threads'], 'threads', maximum=16, nonempty=True)
            identity = json.dumps([self.instance_id, row['source'], row['remote_id']], ensure_ascii=False)
            if (row['instance'] != self.instance_id
                    or row['id'] != 'trigger_' + hashlib.sha256(identity.encode('utf-8')).hexdigest()
                    or type(row['seq']) is not int or not 1 <= row['seq'] <= 9223372036854775807
                    or type(row['policy_revision']) is not int or not 1 <= row['policy_revision'] <= 9223372036854775807
                    or type(row['human']) is not int or row['human'] not in (0, 1)
                    or type(row['explicit']) is not int or row['explicit'] not in (0, 1)
                    or row['disposition'] not in {'accepted', 'source_ignored', 'actor_denied', 'thread_denied', 'request_required'}):
                raise ValueError
            return threads
        except (ValueError, TypeError, RecursionError, HarnessError) as exc:
            raise _error('corrupt') from exc

    def _receipt(self, db, row, *, duplicate: bool) -> dict[str, Any]:
        self._validate_trigger(row)
        stored_routes = db.execute('SELECT target,context_only FROM routes WHERE trigger_id=? ORDER BY target LIMIT 130',
                                   (row['id'],)).fetchall()
        if (len(stored_routes) > 129 or row['disposition'] != 'accepted' and stored_routes
                or row['disposition'] == 'accepted' and not stored_routes
                or any(type(item['context_only']) is not int or item['context_only'] not in (0, 1)
                       or not isinstance(item['target'], str) or item['target'] == '' and item['context_only'] != 0
                       for item in stored_routes)):
            raise _error('corrupt')
        routes = [{'kind': 'worker' if item['target'] else 'router', 'task_id': item['target'] or None,
                   'session_id': self._task(db, item['target'], active=False)['session_id'] if item['target'] else None,
                   'context_only': bool(item['context_only'])} for item in stored_routes]
        return {'id': row['id'], 'seq': row['seq'], 'instance_id': self.instance_id,
                'source_id': row['source'], 'event_id': row['remote_id'], 'duplicate': duplicate,
                'disposition': row['disposition'], 'policy_revision': row['policy_revision'], 'routes': routes}

    def _view(self, db, row) -> dict[str, Any]:
        return {**self._receipt(db, row, duplicate=False), 'actor_id': row['actor'], 'text': row['text'],
                'thread_ids': self._validate_trigger(row), 'human': bool(row['human']), 'explicit': bool(row['explicit'])}

    def event(self, *, source_id: str, event_id: str) -> dict[str, Any]:
        """按原远端身份回查；不存在明确报错，不按正文猜测匹配。"""
        source_id, event_id = _text(source_id, 'source_id'), _text(event_id, 'event_id')
        with self._db() as db:
            row = db.execute('SELECT * FROM triggers WHERE instance=? AND source=? AND remote_id=?',
                             (self.instance_id, source_id, event_id)).fetchone()
            if row is None:
                raise _error('event-unavailable')
            return self._view(db, row)

    def events(self, *, after: int = 0, limit: int = 100) -> dict[str, Any]:
        """有界读取原文和历史路由；读取不消费事件或触发运行。"""
        _page(after, limit)
        with self._db() as db:
            rows = db.execute('SELECT * FROM triggers WHERE instance=? AND seq>? ORDER BY seq LIMIT ?',
                              (self.instance_id, after, limit + 1)).fetchall()
            events = [self._view(db, row) for row in rows[:limit]]
            return {'instance_id': self.instance_id, 'events': events,
                    'next_after': rows[limit - 1]['seq'] if len(rows) > limit else None}
