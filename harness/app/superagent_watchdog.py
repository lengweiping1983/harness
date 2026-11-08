"""Superagent 长任务的持久可观测与停滞升级。"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import time
from datetime import datetime
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ..core.sqlite import prepare_database
from .superagent_common import _error, _text
from .superagent_coordinator_store import (
    CoordinatorStore, _hash as _coordinator_hash, _json as _coordinator_json,
    _now as _coordinator_now, _time as _coordinator_time,
)


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



class CoordinatorWatchdog:
    """在协调账本中持久化 Mission 活性快照；不直接重放或终止外部操作。"""

    _ACTIVITY_LIMIT = 65_536

    def __init__(self, store: CoordinatorStore, *, limits: dict[str, Any] | None = None):
        self._store = store
        self.limits = watchdog_limits(limits)

    @staticmethod
    def _seconds(value: str) -> float:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
        return parsed.timestamp()

    @staticmethod
    def _decode(row, limits: dict[str, Any]) -> dict[str, Any]:
        try:
            state = row["state"]
            fingerprint = row["fingerprint"]
            revision = row["aggregate_revision"]
            baseline_trusted = row["activity_baseline_trusted"]
            ledger_fingerprint = row["ledger_fingerprint"]
            ledger_facts_json = row["ledger_facts_json"]
            observed = _coordinator_time(row["observed_at"], "observed_at")
            progress = _coordinator_time(row["last_progress_at"], "last_progress_at")
            if (
                state not in {"healthy", "stalled", "blocked"}
                or not isinstance(fingerprint, str) or len(fingerprint) != 64
                or any(char not in "0123456789abcdef" for char in fingerprint)
                or type(revision) is not int or revision < 1
                or type(baseline_trusted) is not int or baseline_trusted not in (0, 1)
                or not isinstance(ledger_facts_json, str)
                or _coordinator_json(json.loads(ledger_facts_json)) != ledger_facts_json
                or (ledger_fingerprint is not None and (
                    not isinstance(ledger_fingerprint, str) or len(ledger_fingerprint) != 64
                    or any(char not in "0123456789abcdef" for char in ledger_fingerprint)
                    or _coordinator_hash(ledger_facts_json) != ledger_fingerprint
                ))
                or observed != row["observed_at"] or progress != row["last_progress_at"]
                or progress > observed
            ):
                raise ValueError
            stagnant_seconds = max(
                0.0, CoordinatorWatchdog._seconds(observed) - CoordinatorWatchdog._seconds(progress),
            )
            return {
                "mission_id": row["mission_id"], "fingerprint": fingerprint,
                "state": state, "last_progress_at": progress, "observed_at": observed,
                "aggregate_revision": revision, "stagnant_seconds": stagnant_seconds,
                "stagnant_ticks": int(stagnant_seconds // limits["stale_after"]),
                "activity_baseline_trusted": bool(baseline_trusted),
                "ledger_fingerprint": ledger_fingerprint,
                "ledger_facts_json": ledger_facts_json,
            }
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise _error("corrupt") from exc

    @staticmethod
    def _activity(value: object) -> dict[str, Any]:
        if value is None:
            return {}
        if type(value) is not dict or any(not isinstance(key, str) for key in value):
            raise _error("invalid", field="watchdog.activity")
        try:
            encoded = _coordinator_json(value).encode("utf-8")
        except Exception as exc:
            raise _error("invalid", field="watchdog.activity") from exc
        if len(encoded) > CoordinatorWatchdog._ACTIVITY_LIMIT:
            raise _error("invalid", field="watchdog.activity")
        return json.loads(encoded)

    @staticmethod
    def _activity_observations(
        activity: dict[str, Any], active_assignments: dict[str, dict[str, str | None]],
    ) -> tuple[bool, dict[str, dict[str, Any]]]:
        """逐项核验活动身份；不完整或异常快照不能消费本轮任一 Run 的进度。"""
        if "assignments" not in activity:
            return not active_assignments, {}
        inspections = activity.get("assignments")
        if type(inspections) is not list:
            return False, {}
        seen: set[str] = set()
        observations: dict[str, dict[str, Any]] = {}
        for item in inspections:
            if type(item) is not dict:
                return False, {}
            assignment_id = item.get("assignment_id")
            if (
                not isinstance(assignment_id, str) or not assignment_id
                or assignment_id not in active_assignments or assignment_id in seen
            ):
                return False, {}
            seen.add(assignment_id)
            if item.get("inspection") != "confirmed":
                continue
            identity = active_assignments[assignment_id]
            run_id = identity.get("run_id")
            session_id = identity.get("session_id")
            if (
                not isinstance(run_id, str) or not run_id
                or not isinstance(session_id, str) or not session_id
                or item.get("run_id") != run_id
                or item.get("session_id") != session_id
                or not isinstance(item.get("status"), str) or not item["status"]
                or type(item.get("settled")) is not bool
            ):
                continue
            last = item.get("last_activity")
            if last is None:
                sequence, event_time, event_type = -1, None, None
            elif type(last) is dict:
                sequence = last.get("seq")
                event_time = last.get("time")
                event_type = last.get("type")
                if (
                    type(sequence) is not int or sequence < 0
                    or not isinstance(event_time, str) or not event_time
                    or not isinstance(event_type, str) or not event_type
                ):
                    continue
            else:
                continue
            observations[assignment_id] = {
                "run_id": run_id, "session_id": session_id,
                "last_event_seq": sequence, "last_event_time": event_time,
                "last_event_type": event_type,
            }
        complete = seen == set(active_assignments) and len(observations) == len(active_assignments)
        return complete, observations

    @staticmethod
    def _fingerprint(mission, jobs, assignments, activity: dict[str, Any]) -> str:
        payload = {
            "mission": {
                "mission_id": mission["mission_id"],
                "aggregate_revision": mission["aggregate_revision"],
                "state": mission["state"],
                "plan_revision": mission["plan_revision"],
                "round_no": mission["round_no"],
                "outcome": mission["outcome"],
            },
            "jobs": [
                {
                    "job_id": row["job_id"], "aggregate_revision": row["aggregate_revision"],
                    "state": row["state"], "role": row["role"], "result_hash": row["result_hash"],
                }
                for row in jobs
            ],
            "assignments": [
                {
                    "assignment_id": row["assignment_id"],
                    "aggregate_revision": row["aggregate_revision"],
                    "state": row["state"], "attempt_no": row["attempt_no"],
                    "run_id": row["run_id"], "started_at": row["started_at"],
                    "ended_at": row["ended_at"],
                }
                for row in assignments
            ],
            "activity": activity,
        }
        return _coordinator_hash(_coordinator_json(payload))

    @staticmethod
    def _ledger_facts_json(db, mission_id: str) -> str:
        """读取由 SQLite 按固定顺序投影的实际账本事实，供快照守卫逐字核验。"""
        row = db.execute(
            "SELECT facts_json FROM watchdog_ledger_facts WHERE mission_id=?",
            (mission_id,),
        ).fetchone()
        if row is None:
            raise LookupError("Mission ledger facts not found")
        return row["facts_json"]

    @staticmethod
    def _persist_activity_cursors(db, mission_id, observations, next_revision):
        """先核验全部原生 Run 游标，再在同一事务内统一保存；异常快照不部分消费进度。"""
        changes = []
        inserts = []
        for aid, item in observations.items():
            row = db.execute(
                "SELECT * FROM watchdog_assignment_cursors WHERE mission_id=? AND assignment_id=?",
                (mission_id, aid),
            ).fetchone()
            if row is None:
                inserts.append((aid, item))
                continue
            if (row["run_id"], row["session_id"]) != (item["run_id"], item["session_id"]):
                return False, False
            seq = item["last_event_seq"]
            old = row["last_event_seq"]
            if seq < old or (seq == old and (
                item["last_event_time"] != row["last_event_time"]
                or item["last_event_type"] != row["last_event_type"]
            )):
                return False, False
            if seq > old:
                changes.append((aid, item, row["aggregate_revision"]))

        # 全部校验通过后才允许推进任一原生游标，避免部分可信快照误解锁 Mission。
        for aid, item in inserts:
            db.execute(
                "INSERT INTO watchdog_assignment_cursors(mission_id,assignment_id,run_id,session_id,"
                "last_event_seq,last_event_time,last_event_type,aggregate_revision,last_progress_revision) "
                "VALUES(?,?,?,?,?,?,?,1,0)",
                (mission_id, aid, item["run_id"], item["session_id"], item["last_event_seq"],
                 item["last_event_time"], item["last_event_type"]),
            )
        for aid, item, revision in changes:
            result = db.execute(
                "UPDATE watchdog_assignment_cursors SET last_event_seq=?,last_event_time=?,last_event_type=?,"
                "aggregate_revision=aggregate_revision+1,last_progress_revision=? "
                "WHERE mission_id=? AND assignment_id=? AND aggregate_revision=?",
                (item["last_event_seq"], item["last_event_time"], item["last_event_type"],
                 next_revision, mission_id, aid, revision),
            )
            if result.rowcount != 1:
                raise RuntimeError("Watchdog cursor revision changed")
        return bool(changes), True

    def observe(
        self, mission_id: str, *, activity: dict[str, Any] | None = None,
        now: str | None = None,
    ) -> dict[str, Any]:
        """记录一个账本快照；时间、Revision 和状态升级均保持单调。"""
        mission_id = _text(mission_id, "mission_id")
        observed_at = _coordinator_time(
            _coordinator_now() if now is None else now, "watchdog.now",
        )
        activity_value = self._activity(activity)
        with self._store.transaction(write=True) as db:
            mission = db.execute(
                "SELECT mission_id,aggregate_revision,state,plan_revision,round_no,outcome "
                "FROM missions WHERE mission_id=?",
                (mission_id,),
            ).fetchone()
            if mission is None:
                raise LookupError("Mission not found")
            jobs = db.execute(
                "SELECT job_id,aggregate_revision,state,role,result_hash "
                "FROM jobs WHERE mission_id=? ORDER BY job_id",
                (mission_id,),
            ).fetchall()
            assignments = db.execute(
                "SELECT assignment.assignment_id,assignment.aggregate_revision,assignment.state,"
                "assignment.attempt_no,assignment.run_id,assignment.session_id,assignment.started_at,assignment.ended_at "
                "FROM assignments AS assignment JOIN jobs AS job ON job.job_id=assignment.job_id "
                "WHERE job.mission_id=? ORDER BY assignment.assignment_id",
                (mission_id,),
            ).fetchall()
            active_assignments = {
                row["assignment_id"]: {"run_id": row["run_id"], "session_id": row["session_id"]}
                for row in assignments
                if row["state"] in {"submitting", "accepted", "running", "capturing", "stopping", "unknown"}
            }
            activity_complete, activity_observations = self._activity_observations(
                activity_value, active_assignments,
            )
            fingerprint = self._fingerprint(mission, jobs, assignments, activity_value)
            ledger_facts_json = self._ledger_facts_json(db, mission_id)
            ledger_fingerprint = _coordinator_hash(ledger_facts_json)
            previous = db.execute(
                "SELECT * FROM watchdog_snapshots WHERE mission_id=?",
                (mission_id,),
            ).fetchone()
            if previous is None:
                db.execute(
                    "INSERT INTO watchdog_snapshots(mission_id,fingerprint,state,last_progress_at,observed_at,"
                    "aggregate_revision,activity_baseline_trusted,ledger_fingerprint,ledger_facts_json) "
                    "VALUES(?,?,'healthy',?,?,1,?,?,?)",
                    (
                        mission_id, fingerprint, observed_at, observed_at, int(activity_complete),
                        ledger_fingerprint, ledger_facts_json,
                    ),
                )
                if activity_complete:
                    self._persist_activity_cursors(
                        db, mission_id, activity_observations, next_revision=1,
                    )
            else:
                current = self._decode(previous, self.limits)
                observed_at = max(observed_at, current["observed_at"])
                if (
                    fingerprint == current["fingerprint"]
                    and ledger_fingerprint == current["ledger_fingerprint"]
                    and ledger_facts_json == previous["ledger_facts_json"]
                    and observed_at == current["observed_at"]
                    and (current["activity_baseline_trusted"] or not activity_complete)
                ):
                    return current
                baseline_trusted = current["activity_baseline_trusted"]
                cursor_progress = False
                cursor_complete = True
                next_revision = current["aggregate_revision"] + 1
                if baseline_trusted:
                    if activity_complete:
                        cursor_progress, cursor_complete = self._persist_activity_cursors(
                            db, mission_id, activity_observations, next_revision,
                        )
                    else:
                        cursor_complete = False
                elif activity_complete:
                    _, cursor_complete = self._persist_activity_cursors(
                        db, mission_id, activity_observations, next_revision,
                    )
                    baseline_trusted = cursor_complete
                complete_snapshot = activity_complete and cursor_complete
                old_ledger_facts_json = previous["ledger_facts_json"]
                if complete_snapshot:
                    next_ledger_fingerprint = ledger_fingerprint
                    next_ledger_facts_json = ledger_facts_json
                    ledger_progress = ledger_facts_json != old_ledger_facts_json
                else:
                    next_ledger_fingerprint = current["ledger_fingerprint"]
                    next_ledger_facts_json = old_ledger_facts_json
                    ledger_progress = False
                progressed = ledger_progress or cursor_progress
                if progressed:
                    state = "healthy"
                    last_progress_at = observed_at
                else:
                    fingerprint = current["fingerprint"]
                    last_progress_at = current["last_progress_at"]
                    stagnant_seconds = max(
                        0.0, self._seconds(observed_at) - self._seconds(last_progress_at),
                    )
                    stagnant_ticks = int(stagnant_seconds // self.limits["stale_after"])
                    state = current["state"]
                    if current["state"] != "blocked" and stagnant_ticks >= 1:
                        state = (
                            "blocked"
                            if self.limits["action"] == "block"
                            and stagnant_ticks >= self.limits["max_stale_ticks"]
                            else "stalled"
                        )
                changed = db.execute(
                    "UPDATE watchdog_snapshots SET fingerprint=?,state=?,last_progress_at=?,observed_at=?,"
                    "activity_baseline_trusted=?,ledger_fingerprint=?,ledger_facts_json=?,"
                    "aggregate_revision=aggregate_revision+1 "
                    "WHERE mission_id=? AND aggregate_revision=?",
                    (
                        fingerprint, state, last_progress_at, observed_at, int(baseline_trusted),
                        next_ledger_fingerprint, next_ledger_facts_json,
                        mission_id, current["aggregate_revision"],
                    ),
                )
                if changed.rowcount != 1:
                    raise RuntimeError("WatchdogSnapshot revision changed during a serialized update")
            row = db.execute(
                "SELECT * FROM watchdog_snapshots WHERE mission_id=?",
                (mission_id,),
            ).fetchone()
        return self._decode(row, self.limits)

    def get(self, mission_id: str) -> dict[str, Any] | None:
        mission_id = _text(mission_id, "mission_id")
        with self._store.transaction() as db:
            row = db.execute(
                "SELECT * FROM watchdog_snapshots WHERE mission_id=?",
                (mission_id,),
            ).fetchone()
        return None if row is None else self._decode(row, self.limits)

    def status(self, *, limit: int = 32, mission_ids: list[str] | None = None) -> dict[str, Any]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise _error("invalid", field="watchdog.limit")
        if mission_ids is not None and (
            not isinstance(mission_ids, list) or len(mission_ids) > 500
        ):
            raise _error("invalid", field="watchdog.mission_ids")
        ids = None if mission_ids is None else list(dict.fromkeys(
            _text(value, "mission_id") for value in mission_ids
        ))
        if ids == []:
            return {
                "enabled": True, "limits": dict(self.limits),
                "states": {"healthy": 0, "stalled": 0, "blocked": 0},
                "items": [], "total": 0, "shown": 0, "has_more": False,
            }
        where = "" if ids is None else " WHERE mission_id IN (" + ",".join("?" for _ in ids) + ")"
        arguments = () if ids is None else tuple(ids)
        with self._store.transaction() as db:
            rows = db.execute(
                "SELECT * FROM watchdog_snapshots" + where
                + " ORDER BY CASE state WHEN 'blocked' THEN 0 WHEN 'stalled' THEN 1 ELSE 2 END,"
                "observed_at DESC,mission_id LIMIT ?",
                (*arguments, limit),
            ).fetchall()
            counts = dict(db.execute(
                "SELECT state,count(*) FROM watchdog_snapshots" + where + " GROUP BY state",
                arguments,
            ).fetchall())
        items = [self._decode(row, self.limits) for row in rows]
        total = sum(counts.values())
        return {
            "enabled": True, "limits": dict(self.limits),
            "states": {state: counts.get(state, 0) for state in ("healthy", "stalled", "blocked")},
            "items": items, "total": total, "shown": len(items), "has_more": total > len(items),
        }

    def close(self) -> None:
        """连接按调用关闭；WatchdogSnapshot 历史保留在协调账本中。"""
        return None


__all__ = ['MissionWatchdog', 'CoordinatorWatchdog', 'validate_watchdog', 'watchdog_limits']
