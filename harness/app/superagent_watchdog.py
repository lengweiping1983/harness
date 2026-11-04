"""Superagent 长任务的持久可观测与停滞升级。"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ..core.sqlite import prepare_database
from .superagent_inbox import _error, _text


SCHEMA = '''
CREATE TABLE metadata (
    instance TEXT PRIMARY KEY,
    config TEXT NOT NULL
);
CREATE TABLE heartbeats (
    mission TEXT PRIMARY KEY,
    state TEXT NOT NULL CHECK(state IN ('healthy','stalled','blocked')),
    fingerprint TEXT NOT NULL,
    revision INTEGER NOT NULL,
    first_seen REAL NOT NULL,
    last_progress REAL NOT NULL,
    last_seen REAL NOT NULL,
    stagnant_ticks INTEGER NOT NULL,
    reason TEXT
);
CREATE INDEX heartbeats_state ON heartbeats(state, last_seen);
PRAGMA user_version=1;
'''

DEFAULT_WATCHDOG = {
    'stale_after': 900.0,
    'max_stale_ticks': 3,
    'action': 'attention',
}


def validate_watchdog(value: Any) -> dict[str, Any]:
    """核对 Watchdog 配置，拒绝模型或配置文件扩大自动化副作用。"""
    if not isinstance(value, dict) or set(value) != {'stale_after', 'max_stale_ticks', 'action'}:
        raise _error('invalid', field='autonomy.watchdog')
    stale_after = value['stale_after']
    try:
        stale_seconds = float(stale_after)
    except (OverflowError, TypeError, ValueError):
        stale_seconds = math.inf
    if (isinstance(stale_after, bool) or not isinstance(stale_after, (int, float))
            or not math.isfinite(stale_seconds) or not 1.0 <= stale_seconds <= 604800.0):
        raise _error('invalid', field='autonomy.watchdog.stale_after')
    ticks = value['max_stale_ticks']
    if type(ticks) is not int or not 1 <= ticks <= 32:
        raise _error('invalid', field='autonomy.watchdog.max_stale_ticks')
    if value['action'] not in ('attention', 'block'):
        raise _error('invalid', field='autonomy.watchdog.action')
    return {'stale_after': stale_seconds, 'max_stale_ticks': ticks, 'action': value['action']}


def watchdog_limits(value: Any | None) -> dict[str, Any]:
    """返回隔离的运行时限制，不把隐式默认值写回已绑定配置身份。"""
    if value is None:
        return dict(DEFAULT_WATCHDOG)
    return validate_watchdog(value)


def _fingerprint(mission: dict[str, Any], jobs: list[dict[str, Any]], activity: Any = None) -> str:
    """只用已确认账本和运行活动生成进度指纹，不读取外部正文作为控制指令。"""
    items = []
    for job in jobs:
        data = job.get('data') or {}
        if not isinstance(data, dict):
            raise ValueError('invalid job data')
        items.append({
            'id': job.get('id'),
            'state': job.get('state'),
            'attempt': job.get('attempt'),
            'run_id': data.get('run_id'),
            'workspace_id': data.get('workspace_id'),
            'tree': data.get('tree'),
            'checkpoint': data.get('checkpoint'),
            'verdict': data.get('verdict'),
        })
    value = json.dumps({
        'state': mission.get('state'),
        'revision': mission.get('revision'),
        'round': mission.get('round'),
        'reason': mission.get('reason'),
        'jobs': items,
        'activity': activity,
    }, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


class MissionWatchdog:
    """以独立账本记录目标活性，并把停滞升级为可审计的人工关注项。"""

    def __init__(self, path: Path, *, instance_id: str, identity: str, limits: dict[str, Any] | None = None):
        self.path = Path(path)
        self.instance_id = _text(instance_id, 'instance_id')
        self.identity = _text(identity, 'watchdog_identity', limit=524288)
        self.limits = watchdog_limits(limits)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db(initialize=True, write=True) as db:
            row = db.execute('SELECT config FROM metadata WHERE instance=?', (self.instance_id,)).fetchone()
            if row is None:
                db.execute('INSERT INTO metadata(instance,config) VALUES(?,?)', (self.instance_id, self.identity))
            elif row[0] != self.identity:
                raise _error('conflict')

    @contextmanager
    def _db(self, *, initialize: bool = False, write: bool = False):
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

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        if row is None:
            raise _error('event-unavailable')
        try:
            if (row['state'] not in {'healthy', 'stalled', 'blocked'}
                    or not isinstance(row['mission'], str)
                    or not isinstance(row['fingerprint'], str)
                    or type(row['revision']) is not int or row['revision'] < 1
                    or type(row['stagnant_ticks']) is not int or row['stagnant_ticks'] < 0
                    or not isinstance(row['reason'], (str, type(None)))):
                raise ValueError
            for key in ('first_seen', 'last_progress', 'last_seen'):
                try:
                    finite = math.isfinite(float(row[key]))
                except (OverflowError, TypeError, ValueError):
                    finite = False
                if (isinstance(row[key], bool) or not isinstance(row[key], (int, float))
                        or not finite or row[key] < 0):
                    raise ValueError
            return dict(row)
        except (ValueError, TypeError, KeyError) as exc:
            raise _error('corrupt') from exc

    def observe(self, mission: dict[str, Any], jobs: list[dict[str, Any]], *, activity: Any = None,
                now: float | None = None) -> dict[str, Any]:
        """记录一次心跳；未知副作用只产生关注项，不自动重放原运行。"""
        if (not isinstance(mission, dict) or not isinstance(jobs, list)
                or any(not isinstance(job, dict) for job in jobs)):
            raise _error('invalid', field='watchdog.observation')
        mission_id = _text(mission.get('id'), 'mission_id')
        revision = mission.get('revision')
        if type(revision) is not int or revision < 1:
            raise _error('invalid', field='mission_revision')
        stamp = time.time() if now is None else now
        try:
            finite = math.isfinite(float(stamp))
        except (OverflowError, TypeError, ValueError):
            finite = False
        if (isinstance(stamp, bool) or not isinstance(stamp, (int, float))
                or not finite or stamp < 0):
            raise _error('invalid', field='watchdog.now')
        try:
            fingerprint = _fingerprint(mission, jobs, activity)
        except (TypeError, ValueError) as exc:
            raise _error('invalid', field='watchdog.observation') from exc
        with self._db(write=True) as db:
            row = db.execute('SELECT * FROM heartbeats WHERE mission=?', (mission_id,)).fetchone()
            if row is None:
                db.execute('''INSERT INTO heartbeats(mission,state,fingerprint,revision,first_seen,last_progress,
                    last_seen,stagnant_ticks,reason) VALUES(?,?,?,?,?,?,?,?,NULL)''',
                           (mission_id, 'healthy', fingerprint, revision, stamp, stamp, stamp, 0))
            else:
                previous = self._decode(row)
                # 系统时钟回拨时保留账本单调性，避免虚假的长时间窗口或倒退时间。
                stamp = max(stamp, previous['last_seen'])
                progressed = previous['fingerprint'] != fingerprint
                stagnant = 0 if progressed else previous['stagnant_ticks']
                last_progress = stamp if progressed else previous['last_progress']
                state = 'healthy' if progressed else previous['state']
                reason = None if progressed else previous['reason']
                if not progressed and stamp - last_progress >= self.limits['stale_after']:
                    # 每个 stale_after 窗口最多增加一次计数，避免高频 tick 放大停滞证据。
                    windows = max(0, int((stamp - last_progress) // self.limits['stale_after'])
                                  - previous['stagnant_ticks'])
                    if windows > 0:
                        stagnant += windows
                        if stagnant >= self.limits['max_stale_ticks']:
                            state = 'blocked' if self.limits['action'] == 'block' else 'stalled'
                            reason = 'watchdog/stalled'
                        elif stagnant > 0:
                            state = 'stalled'
                            reason = 'watchdog/stalled'
                db.execute('''UPDATE heartbeats SET state=?,fingerprint=?,revision=?,last_progress=?,last_seen=?,
                    stagnant_ticks=?,reason=? WHERE mission=?''',
                           (state, fingerprint, revision, last_progress, stamp, stagnant, reason, mission_id))
            current = db.execute('SELECT * FROM heartbeats WHERE mission=?', (mission_id,)).fetchone()
        return self._decode(current)

    def get(self, mission_id: str) -> dict[str, Any] | None:
        mission_id = _text(mission_id, 'mission_id')
        with self._db() as db:
            row = db.execute('SELECT * FROM heartbeats WHERE mission=?', (mission_id,)).fetchone()
        return None if row is None else self._decode(row)

    def status(self, *, limit: int = 32, mission_ids: list[str] | None = None) -> dict[str, Any]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise _error('invalid', field='watchdog.limit')
        if mission_ids is not None and not isinstance(mission_ids, list):
            raise _error('invalid', field='watchdog.mission_ids')
        ids = None if mission_ids is None else list(dict.fromkeys(_text(value, 'mission_id') for value in mission_ids))
        where = '' if ids is None else ' WHERE mission IN (' + ','.join('?' for _ in ids) + ')'
        arguments = () if ids is None else tuple(ids)
        with self._db() as db:
            db.execute('BEGIN')
            rows = db.execute('SELECT * FROM heartbeats' + where + ' ORDER BY CASE state WHEN \'blocked\' THEN 0 WHEN \'stalled\' THEN 1 ELSE 2 END,last_seen DESC LIMIT ?', (*arguments, limit)).fetchall()
            counts = dict(db.execute('SELECT state,count(*) FROM heartbeats' + where + ' GROUP BY state', arguments).fetchall())
        items = [self._decode(row) for row in rows]
        return {
            'enabled': True,
            'limits': dict(self.limits),
            'states': {state: counts.get(state, 0) for state in ('healthy', 'stalled', 'blocked')},
            'items': items,
            'total': sum(counts.values()),
            'shown': len(items),
            'has_more': sum(counts.values()) > len(items),
        }

    def close(self) -> None:
        """保留账本；连接按调用关闭，不删除历史证据。"""
        return None


__all__ = ['MissionWatchdog', 'validate_watchdog', 'watchdog_limits']
