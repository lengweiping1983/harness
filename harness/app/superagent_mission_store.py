"""自主目标的当前格式账本；只保存协调意图，执行事实仍引用原运行与工作树。"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from ..core.errors import HarnessError
from ..core.sqlite import prepare_database
from .superagent_inbox import _error, _json, _text


SCHEMA = '''
CREATE TABLE coordinator (instance TEXT PRIMARY KEY, identity TEXT NOT NULL,
    cursor INTEGER NOT NULL, scheduling_cursor INTEGER NOT NULL);
CREATE TABLE missions (seq INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL,
    instance TEXT NOT NULL, source_id TEXT NOT NULL, event_id TEXT NOT NULL,
    trigger_id TEXT NOT NULL, revision INTEGER NOT NULL, state TEXT NOT NULL,
    plan TEXT, plan_hash TEXT, base_commit TEXT, repo TEXT, reason TEXT,
    round INTEGER NOT NULL, reply TEXT, UNIQUE(instance,trigger_id));
CREATE TABLE jobs (id TEXT PRIMARY KEY, mission TEXT NOT NULL, spec TEXT NOT NULL,
    spec_hash TEXT NOT NULL, state TEXT NOT NULL, data TEXT NOT NULL, data_hash TEXT NOT NULL,
    FOREIGN KEY(mission) REFERENCES missions(id));
CREATE INDEX jobs_mission ON jobs(mission);
CREATE TABLE publications (seq INTEGER PRIMARY KEY, mission TEXT NOT NULL UNIQUE,
    FOREIGN KEY(mission) REFERENCES missions(id));
PRAGMA user_version=1;
'''
MISSION_STATES = {'planning', 'running', 'integrating', 'reviewing', 'completed',
                  'blocked', 'unknown', 'rejected', 'suppressed'}
JOB_STATES = {'pending', 'importing', 'ready', 'submitting', 'accepted', 'capturing',
              'done', 'blocked', 'unknown'}
TERMINAL = {'completed', 'blocked', 'unknown', 'rejected', 'suppressed'}


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def packed(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def validate_autonomy(value, profiles):
    """配置必须显式选择集成与复核模板，不由模型扩大工具权限。"""
    if (not isinstance(value, dict) or set(value) !=
            {'max_steps', 'max_attempts', 'max_missions', 'integrator_profile', 'reviewer_profile'}):
        raise _error('invalid', field='autonomy')
    for name, maximum in (('max_steps', 8), ('max_attempts', 5), ('max_missions', 128)):
        if type(value[name]) is not int or not 1 <= value[name] <= maximum:
            raise _error('invalid', field='autonomy.' + name)
    for name in ('integrator_profile', 'reviewer_profile'):
        if not isinstance(value[name], str) or value[name] not in profiles:
            raise _error('invalid', field='autonomy.' + name)
    if profiles[value['integrator_profile']]['repo'] != profiles[value['reviewer_profile']]['repo']:
        raise _error('invalid', field='autonomy.profiles')
    return dict(value)


def validate_plan(value, profiles, source_id, limits):
    """验证完整有向无环图和完成标准；拒绝部分采用、未知模板及跨仓库合并。"""
    if not isinstance(value, dict) or set(value) != {'steps', 'acceptance'}:
        raise ValueError
    steps, checks = value['steps'], value['acceptance']
    if not isinstance(steps, list) or not 1 <= len(steps) <= limits['max_steps']:
        raise ValueError
    if not isinstance(checks, list) or not 1 <= len(checks) <= 16:
        raise ValueError
    ids, repos = set(), set()
    for step in steps:
        if (not isinstance(step, dict) or set(step) != {'id', 'profile_id', 'instruction', 'depends_on'}
                or not isinstance(step['id'], str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,47}', step['id'])
                or step['id'] in ids or not isinstance(step['profile_id'], str)
                or step['profile_id'] not in profiles or not isinstance(step['depends_on'], list)
                or len(step['depends_on']) > 8 or any(not isinstance(item, str) for item in step['depends_on'])
                or len(set(step['depends_on'])) != len(step['depends_on'])):
            raise ValueError
        _text(step['instruction'], 'instruction', limit=8192)
        ids.add(step['id'])
        profile = profiles[step['profile_id']]
        if source_id not in profile['sources']:
            raise ValueError
        repos.add(profile['repo'])
    for name in ('integrator_profile', 'reviewer_profile'):
        profile = profiles[limits[name]]
        if source_id not in profile['sources']:
            raise ValueError
        repos.add(profile['repo'])
    if len(repos) != 1:
        raise ValueError
    resolved = set()
    while len(resolved) < len(steps):
        ready = {step['id'] for step in steps if step['id'] not in resolved and set(step['depends_on']) <= resolved}
        if not ready:
            raise ValueError
        resolved.update(ready)
    check_ids = set()
    for check in checks:
        if (not isinstance(check, dict) or set(check) != {'id', 'criterion', 'kind'}
                or not isinstance(check['id'], str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,47}', check['id'])
                or check['id'] in check_ids or check['kind'] not in ('command', 'inspection')):
            raise ValueError
        _text(check['criterion'], 'criterion', limit=4096)
        check_ids.add(check['id'])
    return value


class MissionStore:
    """新账本不迁移现有 Router；冻结基线和资源组供 Worker 严格回查。"""

    def __init__(self, path, inbox, *, identity, profiles, limits):
        self.path, self.inbox = Path(path), inbox
        self.profiles, self.limits = json.loads(packed(profiles)), dict(limits)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db(initialize=True, write=True) as db:
            row = db.execute('SELECT identity FROM coordinator WHERE instance=?', (inbox.instance_id,)).fetchone()
            if row is None:
                db.execute('INSERT INTO coordinator VALUES(?,?,0,0)', (inbox.instance_id, packed(identity)))
            elif row[0] != packed(identity):
                raise _error('conflict')

    @contextmanager
    def db(self, *, initialize=False, write=False):
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

    def mission_id(self, trigger_id):
        return 'mission_' + digest(packed([self.inbox.instance_id, trigger_id]))[:48]

    def mission(self, identity):
        with self.db() as db:
            row = db.execute('SELECT * FROM missions WHERE id=? AND instance=?', (identity, self.inbox.instance_id)).fetchone()
        if row is None:
            raise _error('event-unavailable')
        result = dict(row)
        try:
            event = self.inbox.event(source_id=row['source_id'], event_id=row['event_id'])
            if (row['trigger_id'] != event['id'] or row['id'] != self.mission_id(event['id'])
                    or row['state'] not in MISSION_STATES or type(row['revision']) is not int or row['revision'] < 1
                    or row['revision'] != event['policy_revision']
                    or type(row['round']) is not int or not 1 <= row['round'] <= self.limits['max_attempts']):
                raise ValueError
            if row['plan'] is not None:
                if digest(row['plan']) != row['plan_hash']:
                    raise ValueError
                result['plan'] = validate_plan(_json(row['plan']), self.profiles, row['source_id'], self.limits)
                if (row['repo'] != self.profiles[result['plan']['steps'][0]['profile_id']]['repo']
                        or not isinstance(row['base_commit'], str) or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', row['base_commit'])):
                    raise ValueError
            elif row['state'] not in {'planning', 'blocked', 'unknown', 'rejected', 'suppressed'}:
                raise ValueError
            if row['state'] == 'completed':
                _text(row['reply'], 'reply', limit=4096)
        except (ValueError, TypeError, KeyError, RecursionError, HarnessError) as exc:
            raise _error('corrupt') from exc
        return result

    def update(self, identity, *, before, **values):
        allowed = {'state', 'plan', 'plan_hash', 'base_commit', 'repo', 'reason', 'round', 'reply'}
        if not values or set(values) - allowed:
            raise _error('invalid', field='mission_update')
        with self.db(write=True) as db:
            return bool(db.execute('UPDATE missions SET ' + ','.join(name + '=?' for name in values)
                + ' WHERE id=? AND instance=? AND state=?', (*values.values(), identity, self.inbox.instance_id, before)).rowcount)

    def complete(self, mission, reply):
        """完成流按实际完成顺序编号，慢目标不能被较快目标的发布游标跳过。"""
        with self.db(write=True) as db:
            changed = db.execute("UPDATE missions SET state='completed',reply=? WHERE id=? AND instance=? AND state='reviewing'",
                                 (reply, mission['id'], self.inbox.instance_id)).rowcount
            if changed:
                db.execute('INSERT INTO publications(mission) VALUES(?)', (mission['id'],))

    def add_job(self, mission, *, role, node, attempt=1, profile=None, dependencies=(), resource=None):
        identity = mission['id'] + '/' + role + ':' + node + ':' + str(attempt)
        spec = packed({'role': role, 'node': node, 'attempt': attempt, 'profile': profile,
                       'dependencies': list(dependencies), 'resource': resource or identity})
        with self.db(write=True) as db:
            row = db.execute('SELECT spec FROM jobs WHERE id=?', (identity,)).fetchone()
            if row is None:
                db.execute("INSERT INTO jobs VALUES(?,?,?,?,'pending','{}',?)", (identity, mission['id'], spec, digest(spec), digest('{}')))
            elif row[0] != spec:
                raise _error('conflict')
        return self.job(identity)

    def job(self, identity):
        with self.db() as db:
            row = db.execute('SELECT * FROM jobs WHERE id=?', (identity,)).fetchone()
        if row is None:
            raise _error('event-unavailable')
        try:
            spec, data = _json(row['spec']), _json(row['data'])
            mission = self.mission(row['mission'])
            if (row['spec_hash'] != digest(row['spec']) or row['data_hash'] != digest(row['data']) or row['state'] not in JOB_STATES
                    or not isinstance(spec, dict) or set(spec) != {'role', 'node', 'attempt', 'profile', 'dependencies', 'resource'}
                    or spec['role'] not in ('plan', 'work', 'integrate', 'review')
                    or type(spec['attempt']) is not int or not 1 <= spec['attempt'] <= self.limits['max_attempts']
                    or not isinstance(spec['node'], str) or not isinstance(spec['dependencies'], list)
                    or any(not isinstance(item, str) for item in spec['dependencies'])
                    or len(set(spec['dependencies'])) != len(spec['dependencies'])
                    or row['id'] != mission['id'] + '/' + spec['role'] + ':' + spec['node'] + ':' + str(spec['attempt'])
                    or not isinstance(data, dict) or set(data) - {'payload', 'payload_hash', 'session_id', 'workspace_id', 'run_id',
                        'import_plan', 'delivery', 'capture_before', 'tree', 'verdict', 'feedback'}):
                raise ValueError
            if spec['role'] != 'plan':
                if mission['plan'] is None or not isinstance(spec['profile'], str) or spec['profile'] not in self.profiles:
                    raise ValueError
                if spec['role'] == 'work':
                    step = next(item for item in mission['plan']['steps'] if item['id'] == spec['node'])
                    if spec['profile'] != step['profile_id'] or spec['dependencies'] != step['depends_on'] or spec['attempt'] != 1:
                        raise ValueError
                elif spec['profile'] != self.limits['reviewer_profile' if spec['role'] == 'review' else 'integrator_profile']:
                    raise ValueError
            if 'payload' in data:
                _text(data['payload'], 'mission_payload', limit=524288)
                if data.get('payload_hash') != digest(data['payload']):
                    raise ValueError
            if row['state'] in ('ready', 'submitting', 'accepted', 'capturing', 'done'):
                _text(data.get('payload'), 'mission_payload', limit=524288)
                _text(data.get('session_id'), 'session_id')
            if row['state'] in ('accepted', 'capturing', 'done'):
                _text(data.get('run_id'), 'run_id')
            if row['state'] in ('importing', 'capturing') or row['state'] == 'done' and spec['role'] != 'plan':
                _text(data.get('workspace_id'), 'workspace_id')
            if row['state'] == 'importing':
                plan = data.get('import_plan')
                if (not isinstance(plan, dict) or plan.get('clean') is not True or not isinstance(plan.get('sources'), list)
                        or not plan['sources'] or any(not isinstance(item, dict) or set(item) != {'id', 'tree', 'ref', 'commit'} for item in plan['sources'])
                        or any(not isinstance(plan.get(key), str) or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', plan[key])
                               for key in ('tree', 'target_tree'))):
                    raise ValueError
            if row['state'] == 'done' and spec['role'] != 'plan':
                delivery = data.get('delivery')
                if (not isinstance(delivery, dict) or set(delivery) != {'commit', 'tree', 'ref', 'head', 'base_commit'}
                        or delivery['base_commit'] != mission['base_commit'] or delivery['tree'] != data.get('tree')):
                    raise ValueError
            if not isinstance(spec['resource'], str) or not spec['resource'].startswith(mission['id'] + '/'):
                raise ValueError
            expected_resource = mission['id'] + '/integrate:goal:1' if spec['role'] == 'integrate' else identity
            if spec['resource'] != expected_resource or spec['role'] in ('integrate', 'review') and (spec['node'] != 'goal' or spec['dependencies']):
                raise ValueError
        except (ValueError, TypeError, KeyError, StopIteration, RecursionError, HarnessError) as exc:
            raise _error('corrupt') from exc
        return {'id': row['id'], 'mission': row['mission'], 'state': row['state'], **spec, 'data': data}

    def change_job(self, job, state, **values):
        if state not in JOB_STATES:
            raise _error('invalid', field='job_state')
        data = {**job['data'], **values}
        if 'payload' in values:
            data['payload_hash'] = digest(values['payload'])
        with self.db(write=True) as db:
            changed = db.execute('UPDATE jobs SET state=?,data=?,data_hash=? WHERE id=? AND state=? AND data=? AND data_hash=?',
                                (state, packed(data), digest(packed(data)), job['id'], job['state'], packed(job['data']), digest(packed(job['data'])))).rowcount
        return self.job(job['id']) if changed else None

    def jobs(self, identity):
        with self.db() as db:
            ids = [row[0] for row in db.execute('SELECT id FROM jobs WHERE mission=? ORDER BY rowid', (identity,))]
        return [self.job(item) for item in ids]

    def scope(self, batch_id, profile):
        job = self.job(batch_id)
        mission = self.mission(job['mission'])
        if job['profile'] != profile or mission['plan'] is None:
            raise _error('conflict')
        return mission['id'], mission['base_commit']
