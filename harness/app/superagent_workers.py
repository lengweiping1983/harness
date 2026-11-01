"""从宿主授权模板建立独立工作树和对话；不重放结果未知的资源创建。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from copy import deepcopy
from pathlib import Path

from ..core.cleanup import join_cleanup, raise_cleanup_errors
from ..core.errors import HarnessError
from ..core.language import current_language, resolve_language
from ..core.sqlite import prepare_database
from ..i18n import tr
from .superagent_bindings import SuperagentBindings
from .superagent_inbox import SuperagentInbox, _error, _names, _text


SCHEMA = '''
CREATE TABLE configurations (instance TEXT PRIMARY KEY, identity TEXT NOT NULL);
CREATE TABLE workers (
    task_id TEXT PRIMARY KEY, instance TEXT NOT NULL, batch_id TEXT NOT NULL,
    trigger_id TEXT NOT NULL, source_id TEXT NOT NULL, event_id TEXT NOT NULL,
    revision INTEGER NOT NULL, profile_id TEXT NOT NULL, commit_id TEXT NOT NULL,
    title TEXT NOT NULL, state TEXT NOT NULL
    CHECK(state IN ('planned','workspace_creating','workspace_ready','session_creating','session_ready','ready','unknown','skipped')),
    stage TEXT NOT NULL CHECK(stage IN ('workspace','session','registration')),
    workspace_id TEXT, session_id TEXT, UNIQUE(instance,batch_id,trigger_id));
CREATE INDEX workers_instance_state ON workers(instance,state);
PRAGMA user_version=1;
'''


@dataclass(frozen=True)
class SuperagentWorkerProfile:
    """可信宿主授予的仓库、来源和工具范围；模型不能提供目录或扩大这些范围。"""

    repo: Path
    description: str
    sources: tuple[str, ...]
    tools: tuple[str, ...]
    model: str | None = None


class SuperagentWorkers:
    """独立创建账本与原工作树／对话事实关联，不复制执行状态。"""

    def __init__(self, harness, inbox: SuperagentInbox, *, owner_session_id: str,
                 profiles: dict[str, SuperagentWorkerProfile], path: Path | None = None,
                 max_workers: int = 128) -> None:
        self.harness, self.inbox = harness, inbox
        self.owner = _text(owner_session_id, 'owner_session_id', limit=128)
        if type(max_workers) is not int or not 1 <= max_workers <= 128:
            raise _error('invalid', field='max_workers')
        self.max_workers = max_workers
        self.closed = False
        self._operations = set()
        self._ensures = {}
        self._close_task = None
        self._native()
        if harness.runtime.closed or harness._close_task is not None:
            raise _error('closed')
        if not isinstance(profiles, dict) or not 1 <= len(profiles) <= 32:
            raise _error('invalid', field='profiles')
        self._profiles = {}
        for name, profile in profiles.items():
            name = _text(name, 'profile_id', limit=64)
            if not isinstance(profile, SuperagentWorkerProfile):
                raise _error('invalid', field='profile')
            repo = Path(profile.repo).resolve(strict=True)
            if not repo.is_dir():
                raise _error('invalid', field='repo')
            tools = _names(profile.tools, 'tools', maximum=256)
            if any(name not in harness.tools.names() for name in tools):
                raise _error('invalid', field='tools')
            self._profiles[name] = {'repo': str(repo), 'description': _text(profile.description, 'description', limit=4096),
                'sources': _names(profile.sources, 'sources', nonempty=True), 'tools': tools,
                'model': _text(profile.model, 'model') if profile.model is not None else None}
            for source in self._profiles[name]['sources']:
                inbox.source(source)
        self.path = Path(path) if path is not None else inbox.path.with_name(inbox.path.stem + '.workers.sqlite3')
        if self.path.resolve() == inbox.path.resolve():
            raise _error('invalid', field='path')
        self.identity = json.dumps({'home': str(harness.config.home.resolve()), 'inbox': str(inbox.path.resolve()),
            'owner': self.owner, 'profiles': self._profiles, 'max_workers': max_workers}, ensure_ascii=False, sort_keys=True)
        self._fingerprint = hashlib.sha256(self.identity.encode('utf-8')).hexdigest()
        self.team = 'superagent-' + hashlib.sha256(inbox.instance_id.encode('utf-8')).hexdigest()[:32]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db(initialize=True, write=True) as db:
            row = db.execute('SELECT identity FROM configurations WHERE instance=?', (inbox.instance_id,)).fetchone()
            if row is None:
                db.execute('INSERT INTO configurations VALUES(?,?)', (inbox.instance_id, self.identity))
            elif row[0] != self.identity:
                raise _error('conflict')
        for service in harness._superagent_workers:
            if service.inbox.instance_id == inbox.instance_id and not service.closed:
                raise _error('conflict')
        self.bindings = SuperagentBindings(self)
        harness._superagent_workers.append(self)

    def _native(self):
        if (self.harness.worktrees is None or self.harness.sessions.root is None
                or any(getattr(self.harness.config, name) for name in
                       ('enable_agents', 'enable_skills', 'enable_goal', 'enable_plan_mode',
                        'enable_subagent', 'enable_ralph'))
                or getattr(self.harness, 'agent_mount', None) is not None):
            raise _error('invalid', field='worker_harness')

    def _open(self):
        if self.closed or self.harness.runtime.closed or self.harness._close_task is not None:
            raise _error('closed')
        self._native()

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

    def catalog(self):
        return {'new:' + key: value['description'] for key, value in self._profiles.items()}

    def routing_entry(self, task_id):
        """返回稳定的任务来源卡片；当前可见性另在收集与派送时核对。"""
        row = self.worker(task_id)
        if row['state'] != 'ready':
            raise _error('worker-unavailable')
        event = self.inbox.event(source_id=row['source_id'], event_id=row['event_id'])
        return json.dumps({'profile_id': row['profile_id'],
            'description': self._profiles[row['profile_id']]['description'],
            'sources': self._profiles[row['profile_id']]['sources'],
            'source_id': row['source_id'], 'event_id': row['event_id'], 'trigger_id': row['trigger_id'],
            'thread_ids': event['thread_ids'], 'request_preview': event['text'][:512],
            'request_chars': len(event['text']), 'request_truncated': len(event['text']) > 512}, ensure_ascii=False)

    def ready_catalog(self, source_ids):
        """只列出当前授权可见、模板允许接收本批次来源的活动任务。"""
        self._open()
        with self._db() as db:
            ids = [row[0] for row in db.execute("SELECT task_id FROM workers WHERE instance=? AND state='ready' ORDER BY rowid LIMIT 129",
                                               (self.inbox.instance_id,))]
        if len(ids) > self.max_workers:
            raise _error('corrupt')
        result = {}
        for task_id in ids:
            row = self.worker(task_id)
            task = self.inbox.task(task_id)
            if not task['active'] or not set(source_ids).intersection(self._profiles[row['profile_id']]['sources']):
                continue
            if task['session_id'] != row['session_id']:
                raise _error('conflict')
            event = self.inbox.event(source_id=row['source_id'], event_id=row['event_id'])
            source = self.inbox.source(row['source_id'])
            if (source['mode'] == 'ignore' or event['actor_id'] not in source['actors']
                    or any(thread not in source['threads'] for thread in event['thread_ids'])):
                continue
            result[task_id] = self.routing_entry(task_id)
        return result

    def attach(self, task_id, *, source_id, event_id, revision):
        """复用原角色和资源，在模板来源范围内关联新 Thread；不恢复已停止任务。"""
        self._open()
        if type(revision) is not int or not 1 <= revision <= 9223372036854775807:
            raise _error('invalid', field='revision')
        row = self.worker(task_id)
        event = self.inbox.event(source_id=source_id, event_id=event_id)
        task = self.inbox.task(task_id)
        if (row['state'] != 'ready' or not task['active']
                or not self._eligible(event, row['profile_id'], revision)):
            return 'skipped'
        if task['session_id'] != row['session_id'] or self._session(row) is None:
            raise _error('worker-unavailable')
        return self.bindings.attach(task_id, event, revision)

    @property
    def profiles(self):
        """返回模板独立快照，外部编辑不改变已绑定的授权配置。"""
        return deepcopy(self._profiles)

    def task_id(self, batch_id, trigger_id):
        raw = json.dumps([self.inbox.instance_id, batch_id, trigger_id], ensure_ascii=False)
        return 'sa_' + hashlib.sha256(raw.encode('utf-8')).hexdigest()[:40]

    def _get(self, task_id):
        with self._db() as db:
            row = db.execute('SELECT * FROM workers WHERE task_id=? AND instance=?', (task_id, self.inbox.instance_id)).fetchone()
        if row is not None:
            try:
                if (row['task_id'] != self.task_id(row['batch_id'], row['trigger_id'])
                        or row['profile_id'] not in self._profiles or type(row['revision']) is not int
                        or not 1 <= row['revision'] <= 9223372036854775807
                        or len(row['commit_id']) not in (40, 64) or any(ch not in '0123456789abcdef' for ch in row['commit_id'])):
                    raise ValueError
                if row['state'] not in {'planned','workspace_creating','workspace_ready','session_creating','session_ready','ready','unknown','skipped'}:
                    raise ValueError
                if (row['state'] in {'planned','workspace_creating','workspace_ready','skipped'} and row['stage'] != 'workspace'
                        or row['state'] == 'session_creating' and row['stage'] != 'session'
                        or row['state'] in {'session_ready','ready'} and row['stage'] != 'registration'
                        or row['state'] == 'unknown' and row['stage'] not in {'workspace','session'}):
                    raise ValueError
                _text(row['title'], 'title', limit=1024)
                _text(row['batch_id'], 'batch_id')
                _text(row['trigger_id'], 'trigger_id')
                _text(row['source_id'], 'source_id')
                _text(row['event_id'], 'event_id')
                if self.inbox.event(source_id=row['source_id'], event_id=row['event_id'])['id'] != row['trigger_id']:
                    raise ValueError
                for field in ('workspace_id', 'session_id'):
                    if row[field] is not None:
                        _text(row[field], field)
                if row['stage'] in ('session', 'registration') and row['workspace_id'] is None:
                    raise ValueError
                if row['stage'] == 'workspace' and row['session_id'] is not None:
                    raise ValueError
                if row['state'] in ('workspace_ready', 'session_creating', 'session_ready', 'ready'):
                    _text(row['workspace_id'], 'workspace_id')
                if row['state'] in ('session_ready', 'ready'):
                    _text(row['session_id'], 'session_id')
            except (ValueError, TypeError, HarnessError) as exc:
                raise _error('corrupt') from exc
        return dict(row) if row is not None else None

    def _eligible(self, event, profile, revision):
        source = self.inbox.source(event['source_id'])
        return (event['disposition'] == 'accepted' and event['human'] and event['explicit']
                and any(route['kind'] == 'router' for route in event['routes'])
                and event['source_id'] in self._profiles[profile]['sources']
                and source['revision'] == revision and source['mode'] != 'ignore'
                and event['actor_id'] in source['actors']
                and all(thread in source['threads'] for thread in event['thread_ids']))

    def _plan(self, batch_id, item, profile):
        task_id = self.task_id(batch_id, item['id'])
        old = self._get(task_id)
        if old is not None:
            if (old['profile_id'] != profile or old['source_id'] != item['source_id']
                    or old['event_id'] != item['event_id'] or old['revision'] != item['revision']):
                raise _error('conflict')
            return old
        spec = self._profiles[profile]
        commit = self.harness.worktrees.git(Path(spec['repo']), 'rev-parse', '--verify', 'HEAD^{commit}').strip()
        with current_language.set(resolve_language(self.harness.config.language).effective_language):
            title = tr('superagent.worker_title', task=task_id)
        with self._db(write=True) as db:
            if db.execute('SELECT 1 FROM workers WHERE task_id=?', (task_id,)).fetchone() is None:
                if db.execute('SELECT count(*) FROM workers WHERE instance=?', (self.inbox.instance_id,)).fetchone()[0] >= self.max_workers:
                    raise _error('worker-capacity')
                db.execute("""INSERT INTO workers VALUES(?,?,?,?,?,?,?,?,?,?,'planned','workspace',NULL,NULL)""",
                    (task_id, self.inbox.instance_id, batch_id, item['id'], item['source_id'], item['event_id'], item['revision'], profile, commit, title))
        row = self._get(task_id)
        if (row['profile_id'] != profile or row['source_id'] != item['source_id']
                or row['event_id'] != item['event_id'] or row['revision'] != item['revision']):
            raise _error('conflict')
        return row

    def _claim(self, row, before, after, stage):
        with self._db(write=True) as db:
            return bool(db.execute('UPDATE workers SET state=?,stage=? WHERE task_id=? AND instance=? AND state=?',
                (after, stage, row['task_id'], self.inbox.instance_id, before)).rowcount)

    def _mark(self, row, state, *, workspace=None, session=None):
        allowed = {'workspace_ready': ('workspace_creating', 'unknown'),
                   'session_ready': ('session_creating', 'unknown'),
                   'ready': ('session_ready',), 'skipped': ('planned', 'workspace_creating', 'workspace_ready'),
                   'unknown': ('workspace_creating', 'session_creating')}
        if state not in allowed:
            raise _error('invalid', field='state')
        previous = allowed[state]
        with self._db(write=True) as db:
            db.execute("""UPDATE workers SET state=?,stage=CASE WHEN ? IN ('session_ready','ready')
                THEN 'registration' ELSE stage END,workspace_id=COALESCE(?,workspace_id),
                session_id=COALESCE(?,session_id) WHERE task_id=? AND instance=? AND state IN ("""
                + ','.join('?' for _ in previous) + ') AND stage=?',
                       (state, state, workspace, session, row['task_id'], self.inbox.instance_id, *previous, row['stage']))

    def _workspace(self, row):
        matches = [item for item in self.harness.worktrees.list() if item.get('team') == self.team
                   and item.get('team_run_id') == row['task_id'] and item.get('team_member') == row['task_id']
                   and item.get('owner_session_id') == self.owner]
        if len(matches) > 1:
            raise _error('conflict')
        if not matches:
            return None
        result = matches[0]
        spec = self._profiles[row['profile_id']]
        root = self.harness.worktrees.git(Path(spec['repo']), 'rev-parse', '--show-toplevel').strip()
        if (Path(result['repo']).resolve() != Path(root).resolve() or result['base_commit'] != row['commit_id'] or result['status'] != 'active'
                or row['workspace_id'] is not None and result['id'] != row['workspace_id']
                or not Path(result['path']).is_dir()):
            raise _error('conflict')
        target = self.harness.worktrees._target(result)
        common = lambda path: Path(self.harness.worktrees.git(path, 'rev-parse', '--path-format=absolute', '--git-common-dir').strip()).resolve()
        actual_root = Path(self.harness.worktrees.git(target, 'rev-parse', '--show-toplevel').strip()).resolve()
        if actual_root != target or common(target) != common(Path(root)):
            raise _error('conflict')
        return result

    def _metadata(self, row):
        return {'instance_id': self.inbox.instance_id, 'task_id': row['task_id'],
                'profile_id': row['profile_id'], 'fingerprint': self._fingerprint,
                'workspace_id': row['workspace_id']}

    def _session(self, row):
        expected = self._metadata(row)
        headers = [header for header in self.harness.sessions.list_sessions()
                   if isinstance(header.metadata.get('superagent_worker'), dict)
                   and header.metadata['superagent_worker'].get('task_id') == row['task_id']]
        if len(headers) > 1:
            raise _error('conflict')
        if not headers:
            return None
        header = headers[0]
        workspace = self._workspace(row)
        model = self._profiles[row['profile_id']]['model']
        if (header.metadata.get('superagent_worker') != expected or workspace is None or header.cwd != workspace['path']
                or row['session_id'] is not None and header.id != row['session_id']
                or model is not None and header.metadata.get('model') != model):
            raise _error('conflict')
        return header

    def bind_session(self, session, agent):
        """每次取执行器时验证持久角色，重启或直接发送也不能退回全局工具范围。"""
        metadata = session.header.metadata.get('superagent_worker')
        if self.closed or not isinstance(metadata, dict) or metadata.get('instance_id') != self.inbox.instance_id:
            return False
        self._native()
        row = self._get(metadata.get('task_id'))
        if (row is None or row['state'] != 'ready' or row['session_id'] != session.id
                or metadata != self._metadata(row)):
            raise _error('worker-unavailable')
        if self._session(row) is None:
            raise _error('worker-unavailable')
        agent.tools = self.harness.tools.restricted(self._profiles[row['profile_id']]['tools'])
        return True

    async def ensure(self, *, batch_id: str, item: dict, profile_id: str) -> dict:
        """认领后每个资源只创建一次；已有创建阶段只读回查，不以缺失为重试依据。"""
        self._open()
        owner = self.harness.runtime.claim_loop(asyncio.get_running_loop())
        if owner is not asyncio.get_running_loop():
            future = asyncio.run_coroutine_threadsafe(self.ensure(batch_id=batch_id, item=item, profile_id=profile_id), owner)
            return await join_cleanup(asyncio.wrap_future(future))
        batch_id = _text(batch_id, 'batch_id')
        if not isinstance(profile_id, str) or profile_id not in self._profiles:
            raise _error('invalid', field='profile_id')
        if (not isinstance(item, dict) or set(item) != {'id', 'source_id', 'event_id', 'revision'}
                or type(item['revision']) is not int or not 1 <= item['revision'] <= 9223372036854775807):
            raise _error('invalid', field='event')
        task_id = self.task_id(batch_id, _text(item['id'], 'trigger_id'))
        existing = self._ensures.get(task_id)
        if existing is not None:
            if existing[0] != (item, profile_id):
                raise _error('conflict')
            return await join_cleanup(existing[1])
        task = asyncio.create_task(self._ensure(batch_id, dict(item), profile_id))
        self._ensures[task_id] = ((dict(item), profile_id), task)
        self._operations.add(task)
        try:
            return await join_cleanup(task)
        finally:
            self._ensures.pop(task_id, None)
            self._operations.discard(task)

    async def _ensure(self, batch_id, item, profile_id):
        event = self.inbox.event(source_id=item['source_id'], event_id=item['event_id'])
        if event['id'] != item['id'] or type(item['revision']) is not int:
            raise _error('invalid', field='event')
        row = self._get(self.task_id(batch_id, item['id']))
        if row is None or row['state'] != 'ready':
            if not self._eligible(event, profile_id, item['revision']):
                return {'task_id': self.task_id(batch_id, item['id']), 'state': 'skipped', 'session_id': None}
        row = self._plan(batch_id, item, profile_id)
        if row['state'] in ('skipped', 'ready'):
            return row
        if row['stage'] == 'workspace' and row['state'] != 'workspace_ready':
            if self._claim(row, 'planned', 'workspace_creating', 'workspace'):
                def create():
                    if self.closed or self.harness.runtime.closed:
                        return None
                    self._native()
                    if not self._eligible(event, profile_id, item['revision']):
                        return None
                    return self.harness.worktrees.create_team(self._profiles[profile_id]['repo'], [row['task_id']],
                        team=self.team, owner_session_id=self.owner, run_id=row['task_id'], ref=row['commit_id'])
                task = asyncio.create_task(asyncio.to_thread(create))
                try:
                    created = await join_cleanup(task)
                    if created is None:
                        self._mark(row, 'skipped')
                        return self._get(row['task_id'])
                except BaseException as original:
                    self._recover_error(row, original)
            workspace = self._workspace(row)
            if workspace is None:
                self._mark(row, 'unknown')
                return self._get(row['task_id'])
            self._mark(row, 'workspace_ready', workspace=workspace['id'])
            row = self._get(row['task_id'])
        if self.closed or self.harness.runtime.closed:
            return row
        self._open()
        if not self._eligible(event, profile_id, item['revision']):
            if row['state'] == 'workspace_ready':
                self._mark(row, 'skipped')
            return {**row, 'state': 'skipped'}
        if row['state'] == 'workspace_ready':
            workspace = self._workspace(row)
            if workspace is None:
                raise _error('worker-unavailable')
            if self._claim(row, 'workspace_ready', 'session_creating', 'session'):
                metadata = {'superagent_worker': self._metadata(row)}
                if self._profiles[profile_id]['model'] is not None:
                    metadata['model'] = self._profiles[profile_id]['model']
                try:
                    if self._session(row) is None:
                        self.harness.create_session(cwd=workspace['path'], title=row['title'], metadata=metadata)
                except BaseException as original:
                    self._recover_error(self._get(row['task_id']), original)
            row = self._get(row['task_id'])
        if row['stage'] == 'session':
            session = self._session(row)
            if session is None:
                self._mark(row, 'unknown')
                return self._get(row['task_id'])
            self._mark(row, 'session_ready', session=session.id)
            row = self._get(row['task_id'])
        if row['state'] == 'session_ready':
            self.inbox.register_task(row['task_id'], session_id=row['session_id'])
            for thread in event['thread_ids']:
                self.inbox.subscribe(row['task_id'], source_id=event['source_id'], thread_id=thread)
            self._mark(row, 'ready')
        return self._get(row['task_id'])

    def worker(self, task_id: str) -> dict:
        """按稳定身份读取创建事实，不将资源就绪投影为业务完成。"""
        row = self._get(_text(task_id, 'task_id'))
        if row is None:
            raise _error('worker-unavailable')
        return row

    def status(self) -> dict:
        """有界读取创建状态和资源引用，不扫描或推断运行结果。"""
        with self._db() as db:
            counts = dict(db.execute('SELECT state,count(*) FROM workers WHERE instance=? GROUP BY state',
                                     (self.inbox.instance_id,)))
            recent = [dict(row) for row in db.execute('''SELECT task_id,state,stage,workspace_id,session_id
                FROM workers WHERE instance=? ORDER BY rowid DESC LIMIT 16''', (self.inbox.instance_id,))]
        return {'instance_id': self.inbox.instance_id, 'states': counts, 'recent': recent}

    def _recover_error(self, row, original):
        errors = [original]
        try:
            found = self._workspace(row) if row['stage'] == 'workspace' else self._session(row)
            if found is None:
                self._mark(row, 'unknown')
            elif row['stage'] == 'workspace':
                self._mark(row, 'workspace_ready', workspace=found['id'])
            else:
                self._mark(row, 'session_ready', session=found.id)
        except BaseException as reconciliation:
            if reconciliation is not original:
                errors.append(reconciliation)
        raise_cleanup_errors(errors)

    def stop(self):
        """同步停止创建准入，所属操作仍由 aclose 等待。"""
        self.closed = True

    async def aclose(self):
        """停止准入并等待所属 Git 写入，不取消已接受的线程操作。"""
        owner = self.harness.runtime.loop
        if owner is not None and owner.is_running() and owner is not asyncio.get_running_loop():
            future = asyncio.run_coroutine_threadsafe(self.aclose(), owner)
            await join_cleanup(asyncio.wrap_future(future))
            return
        self.stop()
        if self._close_task is None:
            async def finish():
                results = await asyncio.gather(*tuple(self._operations), return_exceptions=True)
                raise_cleanup_errors([item for item in results if isinstance(item, BaseException)])
            self._close_task = asyncio.create_task(finish())
        await join_cleanup(self._close_task)
