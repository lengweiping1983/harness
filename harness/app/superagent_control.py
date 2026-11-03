"""Superagent 目标级控制账本。

控制面与任务事实分库存放：暂停、继续和取消都是幂等的持久意图，
不会直接改写任务账本，也不会把一个未确认的模型副作用重新执行一遍。
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from ..core.errors import HarnessError
from ..core.sqlite import prepare_database
from .superagent_inbox import _error, _text


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


class MissionControl:
    """在目标账本之外记录人工控制意图，并提供一次性状态转移。"""

    def __init__(self, path: Path, *, instance_id: str, identity: str):
        self.path = Path(path)
        self.instance_id = _text(instance_id, 'instance_id', limit=256)
        self.identity = _text(identity, 'identity', limit=4096)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db(initialize=True, write=True) as db:
            row = db.execute('SELECT identity FROM control_meta WHERE instance=?', (self.instance_id,)).fetchone()
            if row is None:
                db.execute('INSERT INTO control_meta VALUES(?,?,0)', (self.instance_id, self.identity))
            elif row[0] != self.identity:
                raise _error('conflict')

    @contextmanager
    def db(self, *, initialize=False, write=False):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            if write:
                db.execute('BEGIN IMMEDIATE')
            with db:
                prepare_database(db, SCHEMA, initialize=initialize)
                yield db
        finally:
            db.close()

    def enqueue(self, *, command_id: str, mission_id: str, action: str) -> dict:
        """原子公开一个控制请求；同一身份只能对应相同请求正文。"""
        command_id = _text(command_id, 'request_id', limit=200)
        mission_id = _text(mission_id, 'mission_id', limit=256)
        if action not in ACTIONS:
            raise _error('invalid', field='control_action')
        payload = _payload(action, mission_id)
        now = time.time()
        with self.db(write=True) as db:
            row = db.execute('SELECT * FROM commands WHERE id=? AND instance=?',
                             (command_id, self.instance_id)).fetchone()
            if row is None:
                db.execute('INSERT INTO commands VALUES(?,?,?,?,?,?,?,NULL,NULL)',
                           (command_id, self.instance_id, mission_id, action, payload, now, 'queued'))
                row = db.execute('SELECT * FROM commands WHERE id=?', (command_id,)).fetchone()
            elif row['payload'] != payload:
                raise _error('event-conflict')
        return self._command(row)

    def _command(self, row):
        if row is None:
            raise _error('status-unavailable')
        result = None if row['result'] is None else json.loads(row['result'])
        return {key: row[key] for key in ('id', 'mission_id', 'action', 'status', 'created_at', 'applied_at')} | {'result': result}

    def command(self, command_id: str) -> dict:
        command_id = _text(command_id, 'request_id', limit=200)
        with self.db() as db:
            return self._command(db.execute('SELECT * FROM commands WHERE id=? AND instance=?',
                                             (command_id, self.instance_id)).fetchone())

    def mode(self, mission_id: str) -> dict:
        mission_id = _text(mission_id, 'mission_id', limit=256)
        with self.db() as db:
            row = db.execute('SELECT * FROM mission_control WHERE instance=? AND mission_id=?',
                             (self.instance_id, mission_id)).fetchone()
        if row is None:
            return {'mission_id': mission_id, 'mode': 'running', 'reason': 'default', 'revision': 0, 'updated_at': None}
        return {key: row[key] for key in ('mission_id', 'mode', 'reason', 'revision', 'updated_at')}

    def modes(self, mission_ids):
        ids = [_text(value, 'mission_id', limit=256) for value in mission_ids]
        if not ids:
            return {}
        with self.db() as db:
            rows = db.execute('SELECT * FROM mission_control WHERE instance=? AND mission_id IN (%s)' % ','.join('?' * len(ids)),
                              (self.instance_id, *ids)).fetchall()
        result = {mission_id: {'mission_id': mission_id, 'mode': 'running', 'reason': 'default', 'revision': 0, 'updated_at': None}
                  for mission_id in ids}
        result.update({row['mission_id']: {key: row[key] for key in ('mission_id', 'mode', 'reason', 'revision', 'updated_at')}
                       for row in rows})
        return result

    def reconcile(self, *, mission_exists) -> list[dict]:
        """按创建顺序应用已公开请求；目标尚未被收件箱采纳时保持排队。"""
        applied = []
        with self.db(write=True) as db:
            rows = db.execute("SELECT * FROM commands WHERE instance=? AND status='queued' ORDER BY created_at,id LIMIT 64",
                              (self.instance_id,)).fetchall()
            for command in rows:
                mission_id = command['mission_id']
                if not mission_exists(mission_id):
                    continue
                current = db.execute('SELECT * FROM mission_control WHERE instance=? AND mission_id=?',
                                     (self.instance_id, mission_id)).fetchone()
                mode = current['mode'] if current else 'running'
                action = command['action']
                next_mode, reason, error = mode, 'operator/' + action, None
                if action == 'pause':
                    if mode == 'cancelled':
                        error = 'control/terminal'
                    else:
                        next_mode = 'paused'
                elif action == 'resume':
                    if mode == 'cancelled':
                        error = 'control/terminal'
                    else:
                        next_mode = 'running'
                elif action == 'cancel':
                    if mode == 'cancelled':
                        next_mode = mode
                    else:
                        next_mode = 'cancelled'
                revision = (current['revision'] if current else 0) + 1
                result = {'mission_id': mission_id, 'mode': next_mode, 'revision': revision}
                if error is None:
                    db.execute('''INSERT INTO mission_control VALUES(?,?,?,?,?,?)
                        ON CONFLICT(instance,mission_id) DO UPDATE SET mode=excluded.mode,
                        reason=excluded.reason,revision=excluded.revision,updated_at=excluded.updated_at''',
                               (self.instance_id, mission_id, next_mode, reason, revision, time.time()))
                    status = 'applied'
                else:
                    result['error'] = error
                    status = 'rejected'
                db.execute('UPDATE commands SET status=?,result=?,applied_at=? WHERE id=? AND status=?',
                           (status, json.dumps(result, sort_keys=True), time.time(), command['id'], 'queued'))
                applied.append({'command_id': command['id'], 'status': status, 'result': result, **result})
            if applied:
                db.execute('UPDATE control_meta SET revision=revision+? WHERE instance=?', (len(applied), self.instance_id))
        return applied

    def status(self, *, limit=100):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise _error('invalid', field='limit')
        with self.db() as db:
            rows = db.execute('SELECT * FROM mission_control WHERE instance=? ORDER BY updated_at DESC LIMIT ?',
                              (self.instance_id, limit)).fetchall()
            commands = db.execute('SELECT * FROM commands WHERE instance=? ORDER BY created_at DESC LIMIT ?',
                                  (self.instance_id, limit)).fetchall()
        return {'revision': self._revision(), 'missions': [self.mode(row['mission_id']) for row in rows],
                'commands': [self._command(row) for row in commands]}

    def _revision(self):
        with self.db() as db:
            return db.execute('SELECT revision FROM control_meta WHERE instance=?', (self.instance_id,)).fetchone()[0]
