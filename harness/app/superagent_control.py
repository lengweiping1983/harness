"""Superagent 目标级控制账本。

控制面与任务事实分库存放：暂停、继续和取消都是幂等的持久意图，
不会直接改写任务账本，也不会把一个未确认的模型副作用重新执行一遍。
"""
from __future__ import annotations

import json
import math
import inspect
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from ..core.errors import HarnessError
from ..core.sqlite import prepare_database
from .superagent_inbox import _error, _json, _text


SCHEMA = '''
CREATE TABLE control_meta (
    instance TEXT PRIMARY KEY, identity TEXT NOT NULL, revision INTEGER NOT NULL);
CREATE TABLE commands (
    id TEXT PRIMARY KEY, instance TEXT NOT NULL, mission_id TEXT NOT NULL,
    action TEXT NOT NULL CHECK(action IN ('pause','resume','cancel')),
    payload TEXT NOT NULL, created_at REAL NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('queued','applied','rejected')),
    result TEXT, applied_at REAL,
    UNIQUE(instance,id));
CREATE TABLE mission_control (
    instance TEXT NOT NULL, mission_id TEXT NOT NULL,
    mode TEXT NOT NULL CHECK(mode IN ('running','paused','cancelled')),
    reason TEXT NOT NULL, revision INTEGER NOT NULL, updated_at REAL NOT NULL,
    PRIMARY KEY(instance,mission_id));
CREATE INDEX commands_pending ON commands(instance,status,created_at);
PRAGMA user_version=1;
'''

MODES = {'running', 'paused', 'cancelled'}
ACTIONS = {'pause', 'resume', 'cancel'}


def _payload(action: str, mission_id: str) -> str:
    return json.dumps({'action': action, 'mission_id': mission_id}, sort_keys=True)


def validate_request(command_id, mission_id, action):
    """所有调用入口在建立目录或账本之前验证相同的输入边界。"""
    _text(command_id, 'request_id', limit=200)
    _text(mission_id, 'mission_id', limit=256)
    if not isinstance(action, str) or action not in ACTIONS:
        raise _error('invalid', field='control_action')


def _time(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


class MissionControl:
    """控制模式与命令回执同事务提交；停止执行仍由原运行和作业证明。"""

    def __init__(self, path: Path, *, instance_id: str, identity: str, read_only=False):
        if type(read_only) is not bool:
            raise _error('invalid', field='read_only')
        self.path = Path(path)
        self.instance_id = _text(instance_id, 'instance_id', limit=256)
        self.identity = _text(identity, 'identity', limit=1048576)
        self.read_only, self._cursor = read_only, 0
        if not read_only:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        elif not self.path.is_file():
            raise _error('status-unavailable')
        with self.db(initialize=not read_only, write=not read_only) as db:
            if read_only:
                db.execute('BEGIN')
            rows = db.execute('SELECT * FROM control_meta').fetchall()
            if not rows and not read_only:
                db.execute('INSERT INTO control_meta VALUES(?,?,0)', (self.instance_id, self.identity))
            elif len(rows) != 1 or rows[0]['instance'] != self.instance_id or rows[0]['identity'] != self.identity:
                raise _error('conflict')
            self._revision(db)

    @contextmanager
    def db(self, *, initialize=False, write=False):
        if self.read_only and (initialize or write):
            raise _error('invalid', field='read_only')
        db = (sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True, timeout=10)
              if self.read_only else sqlite3.connect(self.path, timeout=10))
        db.row_factory = sqlite3.Row
        try:
            with db:
                if write:
                    db.execute('BEGIN IMMEDIATE')
                prepare_database(db, SCHEMA, initialize=initialize)
                yield db
        finally:
            db.close()

    def _revision(self, db):
        row = db.execute('SELECT * FROM control_meta WHERE instance=?', (self.instance_id,)).fetchone()
        if (row is None or row['identity'] != self.identity or type(row['revision']) is not int
                or not 0 <= row['revision'] <= 2**53 - 1
                or row['revision'] != db.execute("SELECT count(*) FROM commands WHERE instance=? AND status IN ('applied','rejected')",
                                                (self.instance_id,)).fetchone()[0]):
            raise _error('corrupt')
        return row['revision']

    def _command(self, row):
        if row is None:
            raise _error('status-unavailable')
        try:
            validate_request(row['id'], row['mission_id'], row['action'])
            if (row['instance'] != self.instance_id or row['status'] not in ('queued', 'applied', 'rejected')
                    or _json(_text(row['payload'], 'control.payload', limit=2048)) !=
                        {'action': row['action'], 'mission_id': row['mission_id']} or not _time(row['created_at'])):
                raise ValueError
            result = None if row['result'] is None else _json(_text(row['result'], 'control.result', limit=4096))
            if row['status'] == 'queued':
                if result is not None or row['applied_at'] is not None:
                    raise ValueError
            else:
                if (not isinstance(result, dict) or set(result) != ({'mission_id', 'mode', 'revision', 'error'}
                        if row['status'] == 'rejected' else {'mission_id', 'mode', 'revision'})
                        or result['mission_id'] != row['mission_id'] or result['mode'] not in MODES
                        or type(result['revision']) is not int or not 1 <= result['revision'] <= 2**53 - 1
                        or not _time(row['applied_at'])):
                    raise ValueError
                if row['status'] == 'applied' and result['mode'] != {'pause': 'paused', 'resume': 'running', 'cancel': 'cancelled'}[row['action']]:
                    raise ValueError
                if row['status'] == 'rejected' and (result['error'] != 'control/terminal' or result['mode'] != 'cancelled'
                                                  or row['action'] not in ('pause', 'resume')):
                    raise ValueError
        except (ValueError, TypeError, KeyError, RecursionError, HarnessError) as exc:
            raise _error('corrupt') from exc
        return {key: row[key] for key in ('id', 'mission_id', 'action', 'status', 'created_at', 'applied_at')} | {'result': result}

    def _mode(self, db, row, mission_id):
        original = db.execute("SELECT * FROM commands WHERE instance=? AND mission_id=? AND status='applied' ORDER BY rowid DESC LIMIT 1",
                              (self.instance_id, mission_id)).fetchone()
        latest = self._command(original) if original is not None else None
        if row is None:
            if latest is not None:
                raise _error('corrupt')
            return {'mission_id': mission_id, 'mode': 'running', 'reason': 'default', 'revision': 0, 'updated_at': None}
        if (row['instance'] != self.instance_id or row['mission_id'] != mission_id or row['mode'] not in MODES
                or row['reason'] != {'running': 'operator/resume', 'paused': 'operator/pause', 'cancelled': 'operator/cancel'}[row['mode']]
                or type(row['revision']) is not int or not 1 <= row['revision'] <= 2**53 - 1 or not _time(row['updated_at'])
                or latest is None or latest['result'] != {'mission_id': mission_id, 'mode': row['mode'], 'revision': row['revision']}):
            raise _error('corrupt')
        return {key: row[key] for key in ('mission_id', 'mode', 'reason', 'revision', 'updated_at')}

    def enqueue(self, *, command_id: str, mission_id: str, action: str) -> dict:
        """保持原稳定身份，损坏旧回执不能变成一次新控制。"""
        validate_request(command_id, mission_id, action)
        payload = _payload(action, mission_id)
        with self.db(write=True) as db:
            self._revision(db)
            self._mode(db, db.execute('SELECT * FROM mission_control WHERE instance=? AND mission_id=?',
                                 (self.instance_id, mission_id)).fetchone(), mission_id)
            row = db.execute('SELECT * FROM commands WHERE id=?', (command_id,)).fetchone()
            if row is None:
                if db.execute('SELECT count(*) FROM commands').fetchone()[0] >= 1024:
                    raise _error('control-capacity')
                db.execute('INSERT INTO commands VALUES(?,?,?,?,?,?,?,NULL,NULL)',
                           (command_id, self.instance_id, mission_id, action, payload, time.time(), 'queued'))
                row = db.execute('SELECT * FROM commands WHERE id=?', (command_id,)).fetchone()
            else:
                self._command(row)
                if row['payload'] != payload:
                    raise _error('event-conflict')
            return self._command(row)

    def command(self, command_id: str) -> dict:
        _text(command_id, 'request_id', limit=200)
        with self.db() as db:
            db.execute('BEGIN')
            self._revision(db)
            return self._command(db.execute('SELECT * FROM commands WHERE id=? AND instance=?',
                                             (command_id, self.instance_id)).fetchone())

    def mode(self, mission_id: str) -> dict:
        return self.modes([mission_id])[mission_id]

    def cancelled_missions(self) -> list[str]:
        """恢复使用完整的持久取消集合，不能从活动目标或截断摘要推断。"""
        with self.db() as db:
            db.execute('BEGIN')
            self._revision(db)
            rows = db.execute("SELECT * FROM mission_control WHERE instance=? AND mode='cancelled' ORDER BY mission_id",
                              (self.instance_id,)).fetchall()
            return [self._mode(db, row, row['mission_id'])['mission_id'] for row in rows]

    def modes(self, mission_ids):
        ids = list(dict.fromkeys(_text(value, 'mission_id', limit=256) for value in mission_ids))
        result = {}
        with self.db() as db:
            db.execute('BEGIN')
            self._revision(db)
            for mission_id in ids:
                row = db.execute('SELECT * FROM mission_control WHERE instance=? AND mission_id=?',
                                 (self.instance_id, mission_id)).fetchone()
                result[mission_id] = self._mode(db, row, mission_id)
        return result

    def reconcile(self, *, mission_exists, mission_id=None) -> list[dict]:
        """按持久插入顺序应用，未知目标不饿死后续请求；安全边界可优先核对原目标。"""
        if not callable(mission_exists) or inspect.iscoroutinefunction(mission_exists):
            raise _error('invalid', field='mission_exists')
        if mission_id is not None:
            _text(mission_id, 'mission_id', limit=256)
        applied = []
        with self.db(write=True) as db:
            revision = self._revision(db)
            rows = db.execute("SELECT rowid AS sequence,* FROM commands WHERE instance=? AND status='queued'"
                + (' AND mission_id=? ORDER BY rowid' if mission_id is not None else ' ORDER BY (rowid<=?),rowid') + ' LIMIT 64',
                (self.instance_id, mission_id if mission_id is not None else self._cursor)).fetchall()
            # 每个目标的命令必须按序处理，即使一次轮转从队列中间开始。
            blocked = set()
            for original in rows:
                command = self._command(original)
                target = command['mission_id']
                if target in blocked or db.execute("SELECT 1 FROM commands WHERE instance=? AND mission_id=? AND status='queued' AND rowid<?",
                        (self.instance_id, target, original['sequence'])).fetchone():
                    continue
                exists = mission_exists(target)
                if type(exists) is not bool:
                    if inspect.iscoroutine(exists):
                        exists.close()
                    raise _error('invalid', field='mission_exists')
                if not exists:
                    blocked.add(target)
                    continue
                current = self._mode(db, db.execute('SELECT * FROM mission_control WHERE instance=? AND mission_id=?',
                                               (self.instance_id, target)).fetchone(), target)
                error = current['mode'] == 'cancelled' and command['action'] != 'cancel'
                mode_revision = current['revision'] + (0 if error else 1)
                if mode_revision > 2**53 - 1 or revision + len(applied) + 1 > 2**53 - 1:
                    raise _error('corrupt')
                result = {'mission_id': target, 'mode': current['mode'] if error else
                    {'pause': 'paused', 'resume': 'running', 'cancel': 'cancelled'}[command['action']], 'revision': mode_revision}
                now = time.time()
                if error:
                    result['error'] = 'control/terminal'
                else:
                    db.execute('''INSERT INTO mission_control VALUES(?,?,?,?,?,?)
                        ON CONFLICT(instance,mission_id) DO UPDATE SET mode=excluded.mode,
                        reason=excluded.reason,revision=excluded.revision,updated_at=excluded.updated_at''',
                        (self.instance_id, target, result['mode'], 'operator/' + command['action'], mode_revision, now))
                status = 'rejected' if error else 'applied'
                db.execute("UPDATE commands SET status=?,result=?,applied_at=? WHERE id=? AND instance=? AND status='queued'",
                           (status, json.dumps(result, sort_keys=True), now, command['id'], self.instance_id))
                applied.append({'command_id': command['id'], 'status': status, 'result': result, **result})
            if applied:
                db.execute('UPDATE control_meta SET revision=revision+? WHERE instance=?', (len(applied), self.instance_id))
        if rows and mission_id is None:
            self._cursor = rows[-1]['sequence']
        return applied

    def status(self, *, limit=100):
        """回执、模式、全局统计与修订来自同一读事务，不用摘要代替总量。"""
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise _error('invalid', field='limit')
        with self.db() as db:
            db.execute('BEGIN')
            revision = self._revision(db)
            rows = db.execute('SELECT * FROM mission_control WHERE instance=? ORDER BY updated_at DESC,mission_id LIMIT ?',
                              (self.instance_id, limit)).fetchall()
            commands = db.execute('SELECT * FROM commands WHERE instance=? ORDER BY rowid DESC LIMIT ?',
                                  (self.instance_id, limit)).fetchall()
            total = db.execute('SELECT count(*) FROM mission_control WHERE instance=?', (self.instance_id,)).fetchone()[0]
            states = dict(db.execute('SELECT status,count(*) FROM commands WHERE instance=? GROUP BY status', (self.instance_id,)))
            if set(states) - {'queued', 'applied', 'rejected'}:
                raise _error('corrupt')
            return {'revision': revision, 'missions': [self._mode(db, row, row['mission_id']) for row in rows],
                'commands': [self._command(row) for row in commands], 'states': states,
                'missions_total': total, 'missions_has_more': total > len(rows),
                'commands_total': sum(states.values()), 'commands_has_more': sum(states.values()) > len(commands)}
