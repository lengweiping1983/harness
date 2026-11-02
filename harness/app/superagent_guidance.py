"""节点引导的独立意图账本；受理和应用事实仍归原会话，不重放未知写入。"""
from __future__ import annotations

import asyncio
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from ..core.cleanup import join_cleanup, raise_cleanup_errors
from ..core.errors import HarnessError
from ..core.sqlite import prepare_database
from ..session.turn_scope import turn_events
from .superagent_inbox import _error, _json, _text
from .superagent_mission_store import TERMINAL, digest, packed


SCHEMA = '''
CREATE TABLE settings (instance TEXT PRIMARY KEY, identity TEXT NOT NULL);
CREATE TABLE commands (seq INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL,
    mission TEXT NOT NULL, spec TEXT NOT NULL, spec_hash TEXT NOT NULL, state TEXT NOT NULL, reason TEXT);
CREATE INDEX commands_mission ON commands(mission,seq);
PRAGMA user_version=1;
'''
STATES = {'pending', 'submitting', 'accepted', 'unknown', 'rejected'}


def validate_command(value):
    """协议只接受确切目标与来源身份，不允许从正文推断命令。"""
    keys = {'version', 'instance_id', 'request_id', 'mission_id', 'job_id', 'run_id', 'turn',
            'source_id', 'revision', 'actor_id', 'thread_id', 'text'}
    if not isinstance(value, dict) or set(value) != keys or type(value['version']) is not int or value['version'] != 1:
        raise _error('invalid', field='guidance')
    for key in keys - {'version', 'turn', 'revision'}:
        _text(value[key], 'guidance.' + key, limit=65536 if key == 'text' else 256)
    for key in ('turn', 'revision'):
        if type(value[key]) is not int or not 1 <= value[key] <= 2**53 - 1:
            raise _error('invalid', field='guidance.' + key)
    return value


def command_id(instance_id, request_id):
    return digest(packed([instance_id, request_id]))


def validate_receipt(value):
    """持久投影按当前格式读取；损坏原件不能靠刷新覆盖为新回执。"""
    keys = {'version', 'instance_id', 'id', 'request_id', 'mission_id', 'job_id', 'run_id', 'turn',
            'state', 'reason', 'application', 'accepted_seq', 'applied_seq'}
    try:
        if not isinstance(value, dict) or set(value) != keys or type(value['version']) is not int or value['version'] != 1:
            raise ValueError
        for name in ('instance_id', 'id', 'request_id', 'mission_id', 'job_id', 'run_id'):
            _text(value[name], name)
        if (value['id'] != command_id(value['instance_id'], value['request_id']) or value['state'] not in STATES
                or value['application'] not in (None, 'pending', 'applied', 'unapplied')
                or value['state'] == 'accepted' and value['application'] is None
                or value['state'] in ('pending', 'rejected') and value['application'] is not None
                or type(value['turn']) is not int or not 1 <= value['turn'] <= 2**53 - 1):
            raise ValueError
        if value['reason'] is not None:
            _text(value['reason'], 'reason')
        for name in ('accepted_seq', 'applied_seq'):
            if value[name] is not None and (type(value[name]) is not int or not 0 <= value[name] <= 2**53 - 1):
                raise ValueError
        if ((value['application'] is not None) != (value['accepted_seq'] is not None)
                or (value['application'] == 'applied') != (value['applied_seq'] is not None)
                or value['applied_seq'] is not None and value['applied_seq'] <= value['accepted_seq']):
            raise ValueError
    except (ValueError, TypeError, HarnessError) as exc:
        raise _error('corrupt') from exc
    return value


class SuperagentGuidance:
    """独立持久意图只负责一次准入；原稳定回执确定已受理及已应用。"""

    def __init__(self, coordinator, *, sources, path=None):
        self.coordinator = coordinator
        if not isinstance(sources, (list, tuple, set, frozenset)):
            raise _error('invalid', field='guidance.sources')
        self.sources = frozenset(_text(source, 'guidance.sources') for source in sources)
        for source in self.sources:
            coordinator.inbox.source(source)
        self.instance_id = coordinator.inbox.instance_id
        self.path = Path(path) if path is not None else coordinator.store.path.with_suffix('.guidance.sqlite3')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._tick_task = None
        self._cursor = 0
        self.closed = False
        self.changed = set()
        identity = packed({'missions': str(coordinator.store.path.resolve()), 'sources': sorted(self.sources)})
        with self.db(initialize=True, write=True) as db:
            rows = db.execute('SELECT * FROM settings').fetchall()
            if not rows:
                db.execute('INSERT INTO settings VALUES(?,?)', (self.instance_id, identity))
            elif len(rows) != 1 or rows[0]['instance'] != self.instance_id or rows[0]['identity'] != identity:
                raise _error('conflict')

    @contextmanager
    def db(self, *, initialize=False, write=False):
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

    def _decode(self, row):
        if row is None:
            raise _error('event-unavailable')
        try:
            spec = validate_command(_json(row['spec']))
            if (digest(row['spec']) != row['spec_hash'] or spec['instance_id'] != self.instance_id
                    or row['mission'] != spec['mission_id']
                    or row['id'] != command_id(self.instance_id, spec['request_id']) or row['state'] not in STATES
                    or row['reason'] is not None and not isinstance(row['reason'], str)):
                raise ValueError
        except (ValueError, TypeError, KeyError, RecursionError, HarnessError) as exc:
            raise _error('corrupt') from exc
        return {**dict(row), 'spec': spec}

    def get(self, identity):
        with self.db() as db:
            return self._decode(db.execute('SELECT * FROM commands WHERE id=?', (identity,)).fetchone())

    def enqueue(self, value):
        """只保存本机输入，不把排队回执当成模型已收到。"""
        self._open()
        spec = validate_command(value)
        if spec['instance_id'] != self.instance_id:
            raise _error('conflict')
        identity, raw = command_id(self.instance_id, spec['request_id']), packed(spec)
        with self.db(write=True) as db:
            row = db.execute('SELECT * FROM commands WHERE id=?', (identity,)).fetchone()
            if row is not None:
                original = self._decode(row)
                if packed(original['spec']) != raw:
                    raise _error('event-conflict')
            else:
                if db.execute('SELECT count(*) FROM commands').fetchone()[0] >= 1024:
                    raise _error('file-capacity')
                db.execute("INSERT INTO commands(id,mission,spec,spec_hash,state) VALUES(?,?,?,?,'pending')",
                           (identity, spec['mission_id'], raw, digest(raw)))
        return self.receipt(identity)

    def _open(self):
        self.coordinator._open()
        if self.closed:
            raise _error('closed')

    def close(self):
        self.closed = True

    async def aclose(self):
        self.close()
        if self._tick_task is not None:
            await join_cleanup(self._tick_task)

    def _change(self, row, state, reason=None):
        with self.db(write=True) as db:
            changed = bool(db.execute('UPDATE commands SET state=?,reason=? WHERE id=? AND state=?',
                                     (state, reason, row['id'], row['state'])).rowcount)
        if changed:
            self.changed.add(row['id'])
        return changed

    def identities(self):
        with self.db() as db:
            ids = [row[0] for row in db.execute('SELECT id FROM commands ORDER BY seq LIMIT 1025')]
        if len(ids) > 1024:
            raise _error('corrupt')
        return ids

    def _target(self, row, *, admission=False):
        spec, coordinator = row['spec'], self.coordinator
        job = coordinator.store.job(spec['job_id'])
        mission = coordinator.store.mission(job['mission'])
        if (mission['id'] != spec['mission_id'] or mission['source_id'] != spec['source_id']
                or mission['revision'] != spec['revision'] or job['role'] not in ('work', 'integrate')
                or job['data'].get('run_id') != spec['run_id']):
            raise _error('guidance-unavailable')
        record = coordinator._record(job) if admission else coordinator._submission(job)
        if record is None or record.id != spec['run_id']:
            raise _error('guidance-unavailable')
        host = coordinator.dispatcher.harness
        session = host.get_session(record.session_id)
        try:
            events = turn_events(session.events, run_id=record.id)
        except ValueError as exc:
            raise _error('corrupt') from exc
        if not events or events[0].data['turn'] != spec['turn']:
            raise _error('guidance-unavailable')
        if admission:
            self._open()
            event = coordinator.inbox.event(source_id=mission['source_id'], event_id=mission['event_id'])
            source = coordinator.inbox.source(mission['source_id'])
            if (spec['source_id'] not in self.sources or not coordinator._allowed(mission)
                    or spec['actor_id'] not in source['actors'] or spec['thread_id'] not in source['threads']
                    or spec['thread_id'] not in event['thread_ids']):
                raise _error('guidance-unauthorized')
            if mission['state'] in TERMINAL or job['state'] != 'accepted' or record.status != 'running':
                raise _error('guidance-unavailable')
        return job, session, events

    def _original(self, row, *, target=None):
        """逐次验证原受理与应用事件，不信账本中的接受标记替代运行证据。"""
        _, session, events = target if target is not None else self._target(row)
        request_id = 'superagent-guidance:' + row['id']
        receipt = self.coordinator.dispatcher.harness.steering_receipt(session.id, request_id)
        if receipt is None:
            return None
        spec = row['spec']
        accepted = [event for event in events if event.seq == receipt['accepted_seq'] and event.type == 'note'
                    and event.data.get('kind') == 'steering/accepted' and event.data.get('request_id') == request_id
                    and event.data.get('run_id') == spec['run_id'] and event.data.get('text') == spec['text']
                    and 'original_text' not in event.data and 'attachments' not in event.data]
        if (len(accepted) != 1 or receipt['turn'] != spec['turn'] or receipt['text'] != spec['text']
                or receipt['status'] not in ('pending', 'applied', 'unapplied')):
            raise _error('corrupt')
        if receipt['status'] == 'applied':
            applied = [event for event in events if event.seq == receipt['applied_seq'] and event.type == 'user/message'
                       and event.seq > receipt['accepted_seq'] and event.data.get('text') == spec['text']
                       and event.data.get('source') == 'steer' and event.data.get('request_id') == request_id
                       and 'original_text' not in event.data and 'attachments' not in event.data]
            if len(applied) != 1:
                raise _error('corrupt')
        return receipt

    def receipt(self, identity):
        row = self.get(identity)
        original = self._original(row) if row['state'] in ('submitting', 'accepted', 'unknown') else None
        if row['state'] == 'accepted' and original is None:
            raise _error('corrupt')
        return {'version': 1, 'instance_id': self.instance_id, 'id': row['id'],
                **{key: row['spec'][key] for key in ('request_id', 'mission_id', 'job_id', 'run_id', 'turn')},
                'state': row['state'], 'reason': row['reason'],
                'application': original['status'] if original is not None else None,
                'accepted_seq': original['accepted_seq'] if original is not None else None,
                'applied_seq': original['applied_seq'] if original is not None else None}

    async def tick(self):
        coordinator = self.coordinator
        self._open()
        loop = asyncio.get_running_loop()
        owner = coordinator.dispatcher.harness.runtime.claim_loop(loop)
        if owner is not loop:
            return await join_cleanup(asyncio.wrap_future(asyncio.run_coroutine_threadsafe(self.tick(), owner)))
        if self._tick_task is None or self._tick_task.done():
            self._tick_task = asyncio.create_task(self._tick())
        await join_cleanup(self._tick_task)

    async def _tick(self):
        with self.db() as db:
            rows = [self._decode(row) for row in db.execute("SELECT * FROM commands WHERE state IN ('pending','submitting','unknown') ORDER BY (seq<=?),seq LIMIT 16", (self._cursor,))]
        for row in rows:
            if row['state'] != 'pending':
                original = self._original(row)
                if original is not None:
                    self._change(row, 'accepted')
                elif row['state'] != 'unknown':
                    self._change(row, 'unknown', 'run/submission-unconfirmed')
                continue
            try:
                self._target(row, admission=True)
            except HarnessError as exc:
                if exc.code == 'superagent/corrupt':
                    raise
                self._change(row, 'rejected', exc.code)
                continue
            if not self._change(row, 'submitting'):
                continue
            claimed = self.get(row['id'])
            try:
                job, _, _ = self._target(claimed, admission=True)
                def authorize():
                    self._target(claimed, admission=True)
                await self.coordinator.dispatcher.harness.steer(job['data']['session_id'], row['spec']['text'],
                    expected_turn=row['spec']['turn'], request_id='superagent-guidance:' + row['id'],
                    before_accept=authorize)
            except BaseException as original:
                errors = [original]
                try:
                    found = self._original(claimed)
                    definite = isinstance(original, HarnessError) and original.code in (
                        'run/stale-turn', 'run/steering-capacity', 'superagent/guidance-unavailable', 'superagent/guidance-unauthorized', 'superagent/closed')
                    self._change(claimed, 'accepted' if found is not None else 'rejected' if definite else 'unknown',
                                 None if found is not None else original.code if definite else 'run/submission-unconfirmed')
                    if definite and found is None:
                        continue
                except BaseException as recovery:
                    if recovery is not original:
                        errors.append(recovery)
                raise_cleanup_errors(errors)
            if self._original(claimed) is None:
                raise _error('corrupt')
            self._change(claimed, 'accepted')
        if rows:
            self._cursor = rows[-1]['seq']

    def status(self):
        with self.db() as db:
            db.execute('BEGIN')
            states = dict(db.execute('SELECT state,count(*) FROM commands GROUP BY state'))
            ids = [row[0] for row in db.execute('SELECT id FROM commands ORDER BY seq DESC LIMIT 16')]
        if set(states) - STATES:
            raise _error('corrupt')
        return {'states': states, 'recent': [self.receipt(identity) for identity in ids],
                'total': sum(states.values()), 'shown': len(ids), 'has_more': sum(states.values()) > len(ids)}

    def applied_context(self, mission_id):
        """新节点只接收有确切应用证据的有界上下文，不改原任务正文或验收。"""
        budget, result, total, targets = 8192, [], 0, {}
        with self.db() as db:
            for original in db.execute("SELECT * FROM commands WHERE mission=? AND state='accepted' ORDER BY seq DESC", (mission_id,)):
                row = self._decode(original)
                key = tuple(row['spec'][name] for name in ('mission_id', 'job_id', 'run_id', 'turn', 'source_id', 'revision'))
                if key not in targets:
                    targets[key] = self._target(row)
                receipt = self._original(row, target=targets[key])
                if receipt is None:
                    raise _error('corrupt')
                if receipt['status'] == 'applied':
                    total += 1
                    if len(result) < 16 and budget > 0:
                        text = row['spec']['text'][:min(2048, budget)]
                        budget -= len(text)
                        result.append({**{name: row['spec'][name] for name in ('request_id', 'job_id', 'run_id', 'turn')},
                                       **{name: receipt[name] for name in ('accepted_seq', 'applied_seq')},
                                       'text': text, 'text_chars': len(row['spec']['text']), 'text_truncated': len(text) < len(row['spec']['text'])})
        return {'items': list(reversed(result)), 'total': total, 'shown': len(result), 'has_more': total > len(result)}
