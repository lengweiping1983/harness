"""从原工作运行收集内部报告，由 Router 生成持久回复候选，不发送外部消息。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from ..core.cleanup import join_cleanup, raise_cleanup_errors
from ..core.errors import HarnessError
from ..core.language import current_language, resolve_language
from ..core.sqlite import prepare_database
from ..i18n import tr
from .superagent_inbox import _error, _json, _page, _text


SCHEMA = '''
CREATE TABLE consumers (
    instance TEXT PRIMARY KEY, identity TEXT NOT NULL, cursor INTEGER NOT NULL, waiting_cursor INTEGER NOT NULL);
CREATE TABLE reports (
    seq INTEGER PRIMARY KEY, id TEXT NOT NULL UNIQUE, instance TEXT NOT NULL,
    trigger_id TEXT NOT NULL, task_id TEXT NOT NULL, source_id TEXT NOT NULL, event_id TEXT NOT NULL,
    worker_run TEXT, revision INTEGER, payload TEXT, payload_hash TEXT, router_run TEXT, reply TEXT,
    state TEXT NOT NULL CHECK(state IN ('waiting','ready','submitting','accepted','unknown','draft','silent','rejected','suppressed')),
    UNIQUE(instance,trigger_id,task_id));
CREATE INDEX reports_instance_state_seq ON reports(instance,state,seq);
PRAGMA user_version=1;
'''


class SuperagentResults:
    """保留原运行引用与候选正文；候选不是已发送回执或业务验收。"""

    def __init__(self, router, *, path: Path | None = None, reply_limits: dict[str, int] | None = None):
        self.router, self.dispatcher, self.inbox = router, router.dispatcher, router.inbox
        if reply_limits is not None and not isinstance(reply_limits, dict):
            raise _error('invalid', field='reply_limits')
        self._reply_limits = dict(reply_limits or {})
        for source_id, limit in self._reply_limits.items():
            self.inbox.source(source_id)
            if type(limit) is not int or not 1 <= limit <= 100:
                raise _error('invalid', field='reply_limits')
        self.path = Path(path) if path is not None else self.inbox.path.with_name(self.inbox.path.stem + '.results.sqlite3')
        occupied = {router.path.resolve(), self.inbox.path.resolve(), self.dispatcher.path.resolve()}
        if router.worker_factory:
            occupied.add(router.worker_factory.path.resolve())
        if self.path.resolve() in occupied or getattr(router, 'results', None) is not None:
            raise _error('invalid', field='results')
        binding = [str(router.path.resolve()), router.session_id,
            str(self.dispatcher.path.resolve()), str(router.harness.config.home.resolve()),
            str(self.dispatcher.harness.config.home.resolve())]
        if self._reply_limits:
            binding.append(dict(sorted(self._reply_limits.items())))
        identity = json.dumps(binding, ensure_ascii=False)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db(initialize=True, write=True) as db:
            old = db.execute('SELECT identity FROM consumers WHERE instance=?', (self.inbox.instance_id,)).fetchone()
            if old is None:
                db.execute('INSERT INTO consumers VALUES(?,?,0,0)', (self.inbox.instance_id, identity))
            elif old[0] != identity:
                raise _error('conflict')
        router.results = self

    @property
    def reply_limits(self):
        """返回独立快照，外部编辑不能改变已绑定的回复预算。"""
        return dict(self._reply_limits)

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

    def _id(self, trigger_id, task_id):
        raw = json.dumps([self.inbox.instance_id, trigger_id, task_id], ensure_ascii=False)
        return 'report_' + hashlib.sha256(raw.encode('utf-8')).hexdigest()

    def _key(self, report):
        return 'superagent-result:' + report['id']

    def _get(self, report_id):
        with self._db() as db:
            row = db.execute('SELECT * FROM reports WHERE id=? AND instance=?', (report_id, self.inbox.instance_id)).fetchone()
        if row is None:
            raise _error('event-unavailable')
        try:
            for key in ('trigger_id','task_id','source_id','event_id'):
                _text(row[key], key)
            if row['id'] != self._id(row['trigger_id'], row['task_id']):
                raise ValueError
            if row['payload'] is not None and (not isinstance(row['payload'],str)
                    or row['payload_hash'] != hashlib.sha256(row['payload'].encode('utf-8')).hexdigest()):
                raise ValueError
            if row['state'] not in ('waiting','unknown','suppressed'):
                _text(row['worker_run'], 'worker_run')
                _text(row['payload'], 'result_payload', limit=524288)
                if type(row['revision']) is not int or row['revision'] < 1:
                    raise ValueError
            if row['state'] in ('accepted','draft','silent','rejected'):
                _text(row['router_run'], 'router_run')
            if row['state'] == 'draft':
                self._reply(row['reply'], row['source_id'])
            elif row['reply'] is not None:
                raise ValueError
        except (ValueError, TypeError, HarnessError) as exc:
            raise _error('corrupt') from exc
        return dict(row)

    def _collect(self):
        with self._db(write=True) as db:
            cursor = db.execute('SELECT cursor FROM consumers WHERE instance=?', (self.inbox.instance_id,)).fetchone()[0]
            deliveries = self.dispatcher.deliveries(after=cursor, limit=16)['deliveries']
            for delivery in deliveries:
                db.execute("""INSERT INTO reports(id,instance,trigger_id,task_id,source_id,event_id,state)
                    VALUES(?,?,?,?,?,?,'waiting')""", (self._id(delivery['trigger_id'], delivery['task_id']),
                    self.inbox.instance_id, delivery['trigger_id'], delivery['task_id'], delivery['source_id'], delivery['event_id']))
            if deliveries:
                db.execute('UPDATE consumers SET cursor=? WHERE instance=?', (deliveries[-1]['seq'], self.inbox.instance_id))

    def _allowed(self, event, revision=None):
        source = self.inbox.source(event['source_id'])
        return (event['disposition'] == 'accepted' and event['human'] and source['mode'] != 'ignore'
                and event['actor_id'] in source['actors'] and all(thread in source['threads'] for thread in event['thread_ids'])
                and (revision is None or source['revision'] == revision))

    def _prepare(self):
        with self._db(write=True) as db:
            cursor = db.execute('SELECT waiting_cursor FROM consumers WHERE instance=?', (self.inbox.instance_id,)).fetchone()[0]
            rows = db.execute("SELECT id,seq FROM reports WHERE instance=? AND state='waiting' AND seq>? ORDER BY seq LIMIT 16",
                              (self.inbox.instance_id, cursor)).fetchall()
            if not rows:
                rows = db.execute("SELECT id,seq FROM reports WHERE instance=? AND state='waiting' ORDER BY seq LIMIT 16",
                                  (self.inbox.instance_id,)).fetchall()
            db.execute('UPDATE consumers SET waiting_cursor=? WHERE instance=?', (rows[-1]['seq'] if rows else 0, self.inbox.instance_id))
        for item in rows:
            report = self._get(item['id'])
            delivery = self.dispatcher.delivery(report['trigger_id'], report['task_id'])
            if delivery['source_id'] != report['source_id'] or delivery['event_id'] != report['event_id']:
                raise _error('corrupt')
            if delivery['state'] == 'unknown':
                self._state(report, 'unknown', before='waiting')
                continue
            if delivery['state'] != 'accepted':
                continue
            record = self.dispatcher.harness.runtime.find_submission(delivery['session_id'], delivery['submission_key'])
            if (record is None or record.id != delivery['run_id'] or record.text != delivery['payload']
                    or record.request_id != delivery['submission_key']):
                raise _error('conflict')
            if not record.settled:
                continue
            event = self.inbox.event(source_id=report['source_id'], event_id=report['event_id'])
            if event['id'] != report['trigger_id']:
                raise _error('corrupt')
            revision = self.inbox.source(event['source_id'])['revision']
            if not self._allowed(event, revision):
                self._state(report, 'suppressed', before='waiting')
                continue
            # 原始长报告仍留在工作运行中；截取仅限 Router 上下文并显式标明。
            output = record.output
            frame = {'report_id': report['id'], 'task_id': report['task_id'],
                'source_id': event['source_id'], 'event_id': event['event_id'], 'thread_ids': event['thread_ids'],
                'request': event['text'], 'worker_run_id': record.id, 'worker_status': record.status,
                'worker_error_code': (record.error or {}).get('code'), 'worker_output': output[:8192],
                'output_chars': len(output), 'output_truncated': len(output) > 8192}
            with current_language.set(resolve_language(self.router.harness.config.language).effective_language):
                payload = tr('superagent.result_input', report=json.dumps(frame, ensure_ascii=False),
                             word_limit=self._reply_limits.get(event['source_id'], 100))
            _text(payload, 'result_payload', limit=524288)
            with self._db(write=True) as db:
                db.execute("""UPDATE reports SET state='ready',worker_run=?,revision=?,payload=?,payload_hash=?
                    WHERE id=? AND state='waiting'""", (record.id, revision, payload,
                    hashlib.sha256(payload.encode('utf-8')).hexdigest(), report['id']))

    def _state(self, report, state, *, before, router_run=None, reply=None):
        with self._db(write=True) as db:
            db.execute('UPDATE reports SET state=?,router_run=COALESCE(?,router_run),reply=? WHERE id=? AND state=?',
                       (state, router_run, reply, report['id'], before))

    def _reconcile(self, report):
        record = self.router.harness.runtime.find_submission(self.router.session_id, self._key(report))
        if record is None:
            if report['state'] == 'accepted':
                raise _error('corrupt')
            self._state(report, 'unknown', before=report['state'])
            return None
        if (record.text != report['payload'] or record.request_id != self._key(report)
                or record.prompt_input is not None or report['router_run'] is not None and record.id != report['router_run']):
            raise _error('conflict')
        self._state(report, 'accepted', before=report['state'], router_run=record.id)
        return record

    def _reply(self, value, source_id=None):
        _text(value, 'reply', limit=4096)
        # 汉字按字计，其余按单词计；机器链接不据此授予发布权限。
        if len(re.findall(r'[\u3400-\u9fff]|[^\s\u3400-\u9fff]+', value)) > self._reply_limits.get(source_id, 100):
            raise ValueError
        return value

    async def _poll(self):
        with self._db() as db:
            row = db.execute("""SELECT id FROM reports WHERE instance=? AND state IN ('submitting','accepted')
                ORDER BY seq LIMIT 1""", (self.inbox.instance_id,)).fetchone()
            if row is None:
                row = db.execute("SELECT id FROM reports WHERE instance=? AND state='ready' ORDER BY seq LIMIT 1",
                                 (self.inbox.instance_id,)).fetchone()
        if row is None:
            return
        report = self._get(row['id'])
        event = self.inbox.event(source_id=report['source_id'], event_id=report['event_id'])
        record = None
        if report['state'] == 'ready':
            if not self._allowed(event, report['revision']):
                self._state(report, 'suppressed', before='ready')
                return
            # 根对话当前忙碌时保留报告，不建立无界的排队整理任务。
            active = self.router.harness.runtime.active.get(self.router.session_id)
            if active is not None and not active.done():
                return
            await self.router.harness.ensure_started()
            self.router._open()
            if not self._allowed(event, report['revision']):
                self._state(report, 'suppressed', before='ready')
                return
            agent = self.router.harness._agent_for(self.router.harness.get_session(self.router.session_id))
            agent.tools = self.router.harness.tools.restricted([])
            with self._db(write=True) as db:
                claimed = db.execute("UPDATE reports SET state='submitting' WHERE id=? AND state='ready'", (report['id'],)).rowcount
            if not claimed:
                return
            report['state'] = 'submitting'
            try:
                self.router.harness.runtime.submit(self.router.session_id, report['payload'],
                    key=self._key(report), request_id=self._key(report))
                record = self._reconcile(report)
            except BaseException as original:
                errors = [original]
                try:
                    self._reconcile(report)
                except BaseException as reconciliation:
                    if reconciliation is not original:
                        errors.append(reconciliation)
                raise_cleanup_errors(errors)
        else:
            record = self._reconcile(report)
        if record is None or not record.settled:
            return
        report = self._get(report['id'])
        if not self._allowed(event, report['revision']):
            self._state(report, 'suppressed', before='accepted')
            return
        try:
            if record.status != 'completed':
                raise ValueError
            _text(record.output, 'result_output', limit=65536)
            value = _json(record.output)
            if not isinstance(value, dict) or set(value) != {'reply'}:
                raise ValueError
            reply = self._reply(value['reply'], report['source_id']) if value['reply'] is not None else None
        except (ValueError, TypeError, RecursionError, HarnessError):
            self._state(report, 'rejected', before='accepted')
            return
        self._state(report, 'draft' if reply else 'silent', before='accepted', reply=reply)

    async def tick(self):
        owner = self.dispatcher.harness.runtime.claim_loop(asyncio.get_running_loop())
        if owner is not asyncio.get_running_loop():
            future = asyncio.run_coroutine_threadsafe(self.tick(), owner)
            return await join_cleanup(asyncio.wrap_future(future))
        if self.router.harness.runtime.claim_loop(owner) is not owner:
            raise _error('invalid', field='router_loop')
        self.router._open()
        self._collect()
        self._prepare()
        await self._poll()

    def status(self):
        with self._db() as db:
            return dict(db.execute('SELECT state,count(*) FROM reports WHERE instance=? GROUP BY state', (self.inbox.instance_id,)))

    def uncollected(self):
        with self._db() as db:
            cursor = db.execute('SELECT cursor FROM consumers WHERE instance=?', (self.inbox.instance_id,)).fetchone()[0]
        return bool(self.dispatcher.deliveries(after=cursor, limit=1)['deliveries'])

    def drafts(self, *, after: int = 0, limit: int = 100):
        """读取本地候选，不产生发送回执，正文不随界面语言重新生成。"""
        _page(after, limit)
        with self._db() as db:
            rows = db.execute("SELECT id FROM reports WHERE instance=? AND state='draft' AND seq>? ORDER BY seq LIMIT ?",
                              (self.inbox.instance_id, after, limit)).fetchall()
        return [{key: report[key] for key in ('seq','id','task_id','source_id','event_id','worker_run','router_run','reply','state')}
                for report in (self._get(row['id']) for row in rows)]
