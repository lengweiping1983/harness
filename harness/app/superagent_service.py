"""独立目录中的 Superagent 本地文件信道与完整宿主生命周期。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import time
from contextlib import ExitStack, contextmanager
from dataclasses import fields
from pathlib import Path
from itertools import islice

from ..core.cleanup import join_cleanup, raise_cleanup_errors
from ..core.sqlite import prepare_database
from ..core.errors import HarnessError
from ..i18n import tr
from ..session.store import _atomic_write
from ..session.write_lock import event_write_lock
from .config import HarnessConfig, _default_owned_processes
from .harness import Harness
from .superagent_inbox import SuperagentInbox, _error, _json as strict_json, _names, _text
from .superagent_dispatch import SuperagentDispatcher
from .superagent_router import SuperagentRouter
from .superagent_workers import SuperagentWorkerProfile, SuperagentWorkers
from .superagent_results import SuperagentResults
from .superagent_mission_store import MissionStore, validate_autonomy
from .superagent_missions import SuperagentMissions
from .superagent_guidance import SuperagentGuidance
from .superagent_guidance_files import GuidanceFiles
from .superagent_github import SuperagentGitHub, github_credentials, validate_github
from .superagent_feishu import SuperagentFeishu, feishu_credentials, validate_feishu


SCHEMA = '''
CREATE TABLE instances (
    instance TEXT PRIMARY KEY, config TEXT NOT NULL, state TEXT NOT NULL
    CHECK(state IN ('planned','creating','ready','unknown')), session_id TEXT);
PRAGMA user_version=1;
'''


def read_config(path: Path):
    """在创建宿主、读取凭据或修改目录之前核对全部显式配置。"""
    path = Path(path).expanduser().resolve(strict=True)
    with path.open('rb') as stream:
        raw = stream.read(131073)
    if len(raw) > 131072:
        raise _error('invalid', field='config')
    try:
        value = strict_json(raw.decode('utf-8'))
        required = {'version','instance_id','state_dir','language','router_model','worker_model',
                    'base_url','permission_policy','sources','profiles'}
        if (not isinstance(value, dict) or set(value) - required - {'max_active','max_workers','interval','once_timeout','github','feishu','autonomy'}
                or required - set(value) or type(value['version']) is not int or value['version'] != 1):
            raise ValueError
        _text(value['instance_id'], 'instance_id')
        for key in ('router_model','worker_model','base_url'):
            _text(value[key], key)
        if value['language'] not in ('en','zh-CN') or value['permission_policy'] not in ('read_only','full_access'):
            raise ValueError
        def resolved(raw):
            candidate = Path(_text(raw, 'path', limit=4096)).expanduser()
            return (path.parent / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
        value['state_dir'] = str(resolved(value['state_dir']))
        if not isinstance(value['sources'], dict) or not 1 <= len(value['sources']) <= 32:
            raise ValueError
        for name, source in value['sources'].items():
            _text(name, 'source_id')
            if (not isinstance(source, dict) or set(source) != {'mode','actors','threads'}
                    or source['mode'] not in ('ignore','requests','candidates')):
                raise ValueError
            source['actors'] = _names(source['actors'], 'actors')
            source['threads'] = _names(source['threads'], 'threads')
        if not isinstance(value['profiles'], dict) or not 1 <= len(value['profiles']) <= 32:
            raise ValueError
        for name, profile in value['profiles'].items():
            _text(name, 'profile_id', limit=64)
            if not isinstance(profile, dict) or set(profile) != {'repo','description','sources','tools'}:
                raise ValueError
            repo = resolved(profile['repo'])
            if not repo.is_dir():
                raise ValueError
            profile['repo'] = str(repo)
            _text(profile['description'], 'description', limit=4096)
            profile['sources'] = _names(profile['sources'], 'sources', nonempty=True)
            profile['tools'] = _names(profile['tools'], 'tools')
            if any(source not in value['sources'] for source in profile['sources']):
                raise ValueError
        for name, default, maximum in (('max_active',3,128),('max_workers',128,128),('interval',0.5,60),('once_timeout',600,86400)):
            item = value.setdefault(name, default)
            if (isinstance(item, bool) or not isinstance(item, (int,float)) or not 0 < item <= maximum
                    or name in ('max_active','max_workers') and type(item) is not int):
                raise ValueError
        if value['interval'] < 0.01:
            raise ValueError
        if 'autonomy' in value:
            value['autonomy'] = validate_autonomy(value['autonomy'], value['profiles'])
        if 'github' in value:
            value['github'] = validate_github(value['github'],value['sources'])
        if 'feishu' in value:
            value['feishu'] = validate_feishu(value['feishu'],value['sources'])
        if 'github' in value and 'feishu' in value and (value['github']['source_id'] == value['feishu']['source_id']
                or value['github']['webhook_port'] and value['github']['webhook_port'] == value['feishu']['webhook_port']):
            raise ValueError
        return value
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, HarnessError) as exc:
        raise _error('invalid', field='config') from exc


class SuperagentService:
    """本机可信文件信道；同一状态目录只允许一个所属运行进程。"""

    @classmethod
    async def open(cls, config_path: Path, *, worker_transport=None, router_transport=None):
        spec = read_config(config_path)
        api_key = os.environ.get('HARNESS_API_KEY') or os.environ.get('OPENROUTER_API_KEY','')
        if not api_key:
            raise _error('credentials-required')
        channel_credentials = github_credentials(spec['github']) if 'github' in spec else None
        feishu_keys = feishu_credentials(spec['feishu']) if 'feishu' in spec else None
        service = cls()
        service.spec = spec
        service.root = Path(spec['state_dir'])
        service.root.mkdir(parents=True, exist_ok=True)
        service.stack = ExitStack()
        service.hosts = []
        service.closed = False
        service._close_task = None
        service._tick_task = None
        service._serve_error = None
        service._file_cursor = 0
        service._draft_cursor = 0
        service._file_errors = {}
        service.file_errors = []
        service.router = service.dispatcher = None
        service.github = None
        service.feishu = None
        service.channels = []
        service.guidance_files = None
        service.owner = asyncio.get_running_loop()
        try:
            try:
                service.stack.enter_context(event_write_lock(service.root / 'superagent-runtime'))
            except OSError as exc:
                raise _error('runtime-busy') from exc
            service.path = service.root / 'service.sqlite3'
            with service._db(initialize=True, write=True) as db:
                identity = json.dumps(spec, ensure_ascii=False, sort_keys=True)
                row = db.execute('SELECT config FROM instances WHERE instance=?', (spec['instance_id'],)).fetchone()
                # 一个运行目录只归一个实例，不能重用其中的两个宿主目录。
                if row is None:
                    if db.execute('SELECT 1 FROM instances LIMIT 1').fetchone():
                        raise _error('conflict')
                    db.execute("INSERT INTO instances VALUES(?,?,'planned',NULL)", (spec['instance_id'], identity))
                elif row[0] != identity:
                    raise _error('conflict')
            for kind, model, transport in (('router',spec['router_model'],router_transport),
                                            ('worker',spec['worker_model'],worker_transport)):
                flags = {item.name: False for item in fields(HarnessConfig) if item.name.startswith('enable_')}
                if kind == 'worker':
                    flags.update(enable_fs=True, enable_shell=True, enable_todo=True, enable_worktrees=True,
                                 enable_owned_processes=_default_owned_processes())
                config = HarnessConfig(home=service.root / (kind + '-home'), cwd=service.root,
                    api_key=api_key,
                    model=model, base_url=spec['base_url'], language=spec['language'], max_steps=64,
                    max_retries=0, permission_policy=spec['permission_policy'], **flags)
                host = Harness(config, transport=transport, codex_gateway_home=service.root / (kind + '-gateway'))
                service.hosts.append(host)
                if kind == 'worker' and spec['permission_policy'] == 'read_only':
                    # 本地模式没有审批界面，先于普通宿主的审批钩子拒绝不受理的写入。
                    def read_only(tool, args, ctx):
                        if tool.effect != 'read' or tool.dangerous or tool.approval_required:
                            return tr('superagent.read-only')
                        return None
                    host.tools.add_restriction(read_only)
                    host.tools.add_execution_check(read_only)
            service.router_host, service.worker_host = service.hosts
            session_id = service._router_conversation()
            service.inbox = SuperagentInbox(service.root / 'inbox.sqlite3', instance_id=spec['instance_id'])
            for name, source in spec['sources'].items():
                try:
                    old = service.inbox.source(name)
                except HarnessError as exc:
                    if exc.code != 'superagent/source-unavailable':
                        raise
                    service.inbox.configure_source(name, **source)
                else:
                    if any(old[key] != source[key] for key in ('mode','actors','threads')):
                        raise _error('conflict')
            profiles = {name: SuperagentWorkerProfile(Path(value['repo']), value['description'],
                        tuple(value['sources']), tuple(value['tools']), model=spec['worker_model'])
                        for name, value in spec['profiles'].items()}
            mission_store = (MissionStore(service.root / 'missions.sqlite3', service.inbox,
                identity={'config': spec, 'session_id': session_id}, profiles=spec['profiles'], limits=spec['autonomy'])
                if 'autonomy' in spec else None)
            service.factory = SuperagentWorkers(service.worker_host, service.inbox,
                owner_session_id=session_id, profiles=profiles, max_workers=spec['max_workers'], mission_store=mission_store)
            service.dispatcher = SuperagentDispatcher(service.worker_host, service.inbox, max_active=spec['max_active'])
            reply_limits = {spec['feishu']['source_id']:99} if 'feishu' in spec else None
            if mission_store is not None:
                service.router = SuperagentMissions(service.dispatcher, service.router_host,
                    session_id=session_id, worker_factory=service.factory, store=mission_store, reply_limits=reply_limits)
                service.results = service.router.results
                remote_sources = {spec[name]['source_id'] for name in ('github', 'feishu') if name in spec}
                service.router.guidance = SuperagentGuidance(service.router, sources=set(spec['sources']) - remote_sources)
                service.guidance_files = GuidanceFiles(service.root / 'guidance', service.router.guidance)
            else:
                service.router = SuperagentRouter(service.dispatcher, service.router_host,
                    session_id=session_id, workers={}, worker_factory=service.factory)
                service.results = SuperagentResults(service.router, reply_limits=reply_limits)
            for name in ('incoming','receipts','drafts'):
                (service.root / name).mkdir(exist_ok=True)
            if channel_credentials is not None:
                service.github = SuperagentGitHub(service,spec['github'],*channel_credentials)
                service.channels.append(('github', service.github))
                await service.github.server.start()
            if feishu_keys is not None:
                service.feishu = SuperagentFeishu(service, spec['feishu'], feishu_keys)
                service.channels.append(('feishu', service.feishu))
                await service.feishu.server.start()
            return service
        except BaseException as original:
            errors = [original]
            for _, channel in reversed(service.channels):
                channel.stop()
                try:
                    await channel.server.aclose()
                except BaseException as cleanup:
                    errors.append(cleanup)
            for host in reversed(service.hosts):
                try:
                    await host.close()
                except BaseException as cleanup:
                    errors.append(cleanup)
            try:
                service.stack.close()
            except BaseException as cleanup:
                errors.append(cleanup)
            raise_cleanup_errors(errors)

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

    def _router_conversation(self):
        with self._db(write=True) as db:
            row = db.execute('SELECT * FROM instances WHERE instance=?', (self.spec['instance_id'],)).fetchone()
            creating = row['state'] == 'planned'
            if creating:
                db.execute("UPDATE instances SET state='creating' WHERE instance=?", (self.spec['instance_id'],))
        marker = hashlib.sha256(row['config'].encode('utf-8')).hexdigest()
        headers = [header for header in self.router_host.sessions.list_sessions()
                   if header.metadata.get('superagent_router') == marker]
        if not headers and creating:
            self.router_host.create_session(metadata={'superagent_router': marker})
            headers = [header for header in self.router_host.sessions.list_sessions()
                       if header.metadata.get('superagent_router') == marker]
        if len(headers) != 1 or row['session_id'] is not None and headers[0].id != row['session_id']:
            with self._db(write=True) as db:
                db.execute("UPDATE instances SET state='unknown' WHERE instance=?", (self.spec['instance_id'],))
            raise _error('worker-unavailable')
        with self._db(write=True) as db:
            db.execute("UPDATE instances SET state='ready',session_id=? WHERE instance=?", (headers[0].id, self.spec['instance_id']))
        return headers[0].id

    @staticmethod
    def _publish(path, value):
        raw = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')
        try:
            with path.open('xb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            if path.is_symlink():
                raise _error('conflict')
            with path.open('rb') as stream:
                if stream.read(len(raw) + 1) != raw:
                    raise _error('conflict')

    def _scan(self):
        files = list(islice((self.root / 'incoming').glob('*.json'),1025))
        if len(files) > 1024:
            raise _error('file-capacity')
        files.sort()
        self._input_count = len(files)
        names = {path.name for path in files}
        self._file_errors = {name: value for name, value in self._file_errors.items() if name in names}
        if files:
            offset = self._file_cursor % len(files)
            selected = (files[offset:] + files[:offset])[:16]
            self._file_cursor = offset + len(selected)
        else:
            selected = []
        for path in selected:
            try:
                if path.is_symlink() or not path.is_file() or path.stat().st_size > 524288:
                    raise _error('invalid', field='event_file')
                with path.open('rb') as stream:
                    raw = stream.read(524289)
                if len(raw) > 524288:
                    raise _error('invalid', field='event_file')
                value = strict_json(raw.decode('utf-8'))
                if not isinstance(value, dict) or set(value) != {'source_id','event_id','actor_id','text','thread_ids','human','explicit'}:
                    raise _error('invalid', field='event_file')
                receipt = self.inbox.receive(**value)
                identity = hashlib.sha256(path.name.encode('utf-8') + b'\0' + raw).hexdigest()
                self._publish(self.root / 'receipts' / (identity + '.json'),
                              {'file': path.name, 'receipt': {key: value for key, value in receipt.items() if key != 'duplicate'}})
                self._file_errors.pop(path.name, None)
            except (UnicodeError, ValueError, RecursionError):
                self._file_errors[path.name] = {'file': path.name, 'code': 'superagent/invalid'}
            except HarnessError as exc:
                if exc.code not in {'superagent/invalid','superagent/event-conflict','superagent/source-unavailable'}:
                    raise
                self._file_errors[path.name] = {'file': path.name, 'code': exc.code}
        self.file_errors = [self._file_errors[name] for name in sorted(self._file_errors)]

    async def tick(self):
        if self.closed:
            raise _error('closed')
        if self.owner is not asyncio.get_running_loop():
            future = asyncio.run_coroutine_threadsafe(self.tick(), self.owner)
            return await join_cleanup(asyncio.wrap_future(future))
        if self._tick_task is None or self._tick_task.done():
            self._tick_task = asyncio.create_task(self._tick())
        return await join_cleanup(self._tick_task)

    async def _tick(self):
        for _, channel in self.channels:
            if channel.server.errors:
                raise channel.server.errors[0]
            channel.drain()
        self._scan()
        try:
            if not self.closed and self.guidance_files is not None:
                self.guidance_files.scan()
            await self.router.tick(include_status=False)
        except HarnessError as exc:
            if not self.closed or exc.code != 'superagent/closed':
                raise
        for draft in self.results.drafts(after=self._draft_cursor, limit=16):
            self._publish(self.root / 'drafts' / (draft['id'] + '.json'), draft)
            self._draft_cursor = draft['seq']
        for _, channel in self.channels:
            await channel.tick()
        if self.guidance_files is not None:
            self.guidance_files.publish()
        status = {**self.router.status(), 'input_errors': self.file_errors,
                  'runtime_state': 'stopping' if self.closed else 'running',
                  **{name:channel.status() for name, channel in self.channels},
                  **({'guidance_files': self.guidance_files.status()} if self.guidance_files is not None else {})}
        _atomic_write(self.root / 'status.json', json.dumps(status, ensure_ascii=False, indent=2))
        return status

    async def serve(self, stop: asyncio.Event, *, once=False):
        started = time.monotonic()
        scans = 0
        if once and self.guidance_files is not None:
            self.guidance_files.seen.clear()
        while not stop.is_set() and not self.closed:
            status = await self.tick()
            scans += 1
            active = any(host.runtime.records.active for host in self.hosts)
            pending = any(status['inputs'].get(key,0) for key in ('waiting','pending'))
            pending |= any(status['results'].get(key,0) for key in ('waiting','ready','submitting','accepted'))
            pending |= self.results.uncollected()
            pending |= bool(self.results.drafts(after=self._draft_cursor, limit=1))
            pending |= bool(self.inbox.events(after=status['cursor'], limit=1)['events'])
            if self.guidance_files is not None:
                pending |= self.guidance_files.unscanned or any(status['guidance']['states'].get(key, 0) for key in ('pending', 'submitting'))
            outgoing = [status[name]['outbound'] for name, _ in self.channels]
            pending |= any(status[name]['queued'] for name, _ in self.channels)
            pending |= any(counts.get(key,0) for counts in outgoing for key in ('pending','sending'))
            # 至少轮询完输入目录；重启读回不重放旧运行或写入。
            covered = scans * 16 >= self._input_count
            if once and not active and any(batch['state'] == 'unknown' for batch in status['batches']):
                return 2
            if once and covered and not active and not pending:
                if self.guidance_files is not None:
                    self.guidance_files.publish(final=True)
                return 2 if (status['input_errors'] or status['inputs'].get('unknown') or status['inputs'].get('rejected')
                    or self.guidance_files is not None and (status['guidance_files']['input_errors'] or
                        any(status['guidance']['states'].get(key, 0) for key in ('unknown', 'rejected')))
                    or any(counts.get('unknown') for counts in outgoing)
                    or any(status['results'].get(key) for key in ('unknown','rejected'))
                    or any(batch['state'] in ('unknown','rejected') for batch in status['batches'])) else 0
            if once and time.monotonic() - started >= self.spec['once_timeout']:
                self._serve_error = _error('once-timeout')
                raise self._serve_error
            try:
                await asyncio.wait_for(stop.wait(), self.spec['interval'])
            except TimeoutError:
                pass
        return 0

    async def aclose(self):
        if self.owner is not asyncio.get_running_loop():
            future = asyncio.run_coroutine_threadsafe(self.aclose(), self.owner)
            await join_cleanup(asyncio.wrap_future(future))
            return
        self.closed = True
        self.router.close()
        if self.guidance_files is not None:
            self.router.guidance.close()
        self.dispatcher.close()
        self.factory.stop()
        for _, channel in self.channels:
            channel.stop()
        for host in self.hosts:
            host.runtime.begin_close()
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        await join_cleanup(self._close_task)

    async def _close(self):
        errors = []
        for _, channel in reversed(self.channels):
            try:
                await channel.server.aclose()
            except BaseException as exc:
                errors.append(exc)
        try:
            await self.factory.aclose()
        except BaseException as exc:
            errors.append(exc)
        if self._tick_task is not None:
            try:
                await self._tick_task
            except BaseException as exc:
                errors.append(exc)
        if self.guidance_files is not None:
            try:
                await self.router.guidance.aclose()
            except BaseException as exc:
                if not any(exc is error for error in errors):
                    errors.append(exc)
        for host in reversed(self.hosts):
            try:
                await host.close()
            except BaseException as exc:
                errors.append(exc)
        try:
            if self.guidance_files is not None:
                self.guidance_files.publish(final=True)
            failure = errors[0] if errors else self._serve_error
            status = {**self.router.status(), 'input_errors': self.file_errors,
                      'runtime_state': 'failed' if failure is not None else 'stopped',
                      **{name:channel.status() for name, channel in self.channels},
                      **({'guidance_files': self.guidance_files.status()} if self.guidance_files is not None else {})}
            if failure is not None:
                status['last_error_code'] = getattr(failure,'code','runtime/failure')
            _atomic_write(self.root / 'status.json', json.dumps(status, ensure_ascii=False, indent=2))
        except BaseException as exc:
            errors.append(exc)
        try:
            self.stack.close()
        except BaseException as exc:
            errors.append(exc)
        raise_cleanup_errors(errors)
