"""普通聊天接续的本机只读桥；不代理模型，不直接执行项目命令。"""
from __future__ import annotations

from contextlib import closing
from contextlib import contextmanager
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
import re
import secrets
import sqlite3
import threading
import time
from urllib.parse import parse_qs, urlsplit

from ..core.errors import HarnessError
from ..core.language import current_language
from ..core.sqlite import prepare_database
from ..execution.pipeline import PipelineRuntime
from ..execution.store import ExecutionStore, SCHEMA as EXECUTOR_SCHEMA
from ..execution.workspaces import sync_projects
from ..execution.catalog import SID
from ..tools.schema import validate
from ..i18n import tr


SCHEMA = """
CREATE TABLE bindings(id TEXT PRIMARY KEY, principal TEXT NOT NULL, session_id TEXT NOT NULL,
    url TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE dispatches(id TEXT PRIMARY KEY, binding_id TEXT NOT NULL, milestone TEXT NOT NULL,
    phase TEXT NOT NULL, status TEXT NOT NULL, prompt TEXT NOT NULL, created REAL NOT NULL,
    UNIQUE(binding_id,milestone,phase));
"""
CHAT = re.compile(r'^/(?:g/[^/]+/)?c/[0-9a-f-]{36}$')
PROTOCOL_VERSION = 2


def read_pairing_token(home):
    """读取既有配对凭据并验证，读取接口不生成或覆盖凭据。"""
    value = json.loads((home / 'companion-key.json').read_text(encoding='utf-8'))
    if (not isinstance(value, dict) or set(value) != {'token'} or not isinstance(value['token'], str)
            or not re.fullmatch('[0-9a-f]{64}', value['token'])):
        raise ValueError(tr('execution.store.invalid_record'))
    return value['token']


def chat_url(value):
    """仅绑定已存在的普通聊天，不接受站外地址或新建聊天占位页。"""
    parsed = urlsplit(value)
    if parsed.scheme != 'https' or parsed.netloc != 'chatgpt.com' or not CHAT.fullmatch(parsed.path):
        raise ValueError(tr('execution.companion.invalid'))
    return 'https://chatgpt.com' + parsed.path


def image_resume_milestone(milestone, reply_key):
    """图片恢复消息按被打断的回复隔离，不重复登记生成或交付意图。"""
    return hashlib.sha256(f'{milestone}:resume:{reply_key}'.encode()).hexdigest()


class Companion:
    """浏览器负责页面确认，桥只读执行事实并持久保存单次发送意图。"""

    def __init__(self, config, principal='local'):
        self.config = config
        self.configured = dict(config.projects)
        self.principal = principal
        self.lock = threading.RLock()
        config.home.mkdir(parents=True, exist_ok=True)
        key_path = config.home / 'companion-key.json'
        if not key_path.exists():
            token = secrets.token_hex(32)
            try:
                with key_path.open('x', encoding='utf-8') as stream:
                    json.dump({'token': token}, stream)
                if os.name != 'nt':
                    key_path.chmod(0o600)
            except FileExistsError:
                pass
        self.token = read_pairing_token(config.home)
        with self.database() as db:
            prepare_database(db, SCHEMA)
            self._validate_records(db)

    @contextmanager
    def database(self):
        with closing(sqlite3.connect(self.config.home / 'companion.sqlite3')) as db:
            if db.execute("SELECT COUNT(*) FROM sqlite_schema WHERE type='table'").fetchone()[0]:
                self._validate_records(db)
            yield db

    def _validate_records(self, db):
        """已保存发送事实损坏时拒绝继续，不能覆盖成一条新消息。"""
        for row in db.execute('SELECT id,principal,session_id,url,created FROM bindings'):
            if (not isinstance(row[0], str) or not re.fullmatch(r'bind_[0-9a-f]{32}', row[0])
                    or not isinstance(row[1], str) or not row[1] or validate(row[2], SID)
                    or not isinstance(row[3], str) or chat_url(row[3]) != row[3]
                    or type(row[4]) not in (int, float) or not math.isfinite(row[4])):
                raise ValueError(tr('execution.store.invalid_record'))
        for row in db.execute('SELECT id,binding_id,milestone,phase,status,prompt,created FROM dispatches'):
            if (not isinstance(row[0], str) or not re.fullmatch(r'[0-9a-f]{32}', row[0])
                    or db.execute('SELECT 1 FROM bindings WHERE id=?', (row[1],)).fetchone() is None
                    or not isinstance(row[2], str) or not re.fullmatch(r'[0-9a-f]{64}', row[2])
                    or row[3] not in {'continue', 'generate', 'deliver', 'next_task', 'resume'}
                    or row[4] not in {'dispatching', 'confirmed', 'not_sent'}
                    or not isinstance(row[5], str) or not row[5]
                    or type(row[6]) not in (int, float) or not math.isfinite(row[6])):
                raise ValueError(tr('execution.store.invalid_record'))

    def executor(self):
        path = self.config.home / 'executor.sqlite3'
        db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
        db.row_factory = sqlite3.Row
        try:
            prepare_database(db, EXECUTOR_SCHEMA, initialize=False)
            db.execute('BEGIN')
        except BaseException:
            db.close()
            raise
        return closing(db)

    def session(self, db, sid):
        self.config = sync_projects(self.config, self.configured)
        store = object.__new__(ExecutionStore)
        store.db = db
        value = store.session(sid, self.principal)
        project = self.config.projects.get(value['project_id'])
        if project is None or self.principal not in project.principals or value['project_root'] != str(project.root):
            raise ValueError(tr('execution.companion.denied'))
        return value, store

    def sessions(self):
        with self.executor() as db:
            result = []
            for row in db.execute('SELECT id FROM sessions WHERE principal=? ORDER BY rowid DESC LIMIT 50', (self.principal,)):
                try:
                    value, _ = self.session(db, row['id'])
                except ValueError:
                    continue
                result.append({key: value[key] for key in ('session_id', 'project_id', 'title')})
            return {'sessions': result}

    def health(self):
        """配对后返回分层诊断，不暴露配对码、配置路径或对话原文。"""
        if not (self.config.home / 'executor.sqlite3').exists():
            return {'protocol': 'harness-chat-companion', 'protocol_version': PROTOCOL_VERSION,
                    'executor': 'missing', 'session_count': 0}
        try:
            count = len(self.sessions()['sessions'])
        except (ValueError, HarnessError, sqlite3.Error, OSError):
            return {'protocol': 'harness-chat-companion', 'protocol_version': PROTOCOL_VERSION,
                    'executor': 'invalid', 'session_count': 0}
        return {'protocol': 'harness-chat-companion', 'protocol_version': PROTOCOL_VERSION,
                'executor': 'ready' if self.executor_alive() else 'offline', 'session_count': count}

    def executor_alive(self):
        """只探测既有所有权锁，不创建、清空或改写执行器文件。"""
        try:
            stream = (self.config.home / 'executor.owner').open('rb')
        except FileNotFoundError:
            return False
        with stream:
            if os.name == 'nt':
                import msvcrt
                try:
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBRLCK, 1)
                except OSError as exc:
                    if exc.errno == 13:
                        return True
                    raise
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                try:
                    fcntl.flock(stream, fcntl.LOCK_SH | fcntl.LOCK_NB)
                except BlockingIOError:
                    return True
                fcntl.flock(stream, fcntl.LOCK_UN)
        return False

    def bind(self, sid, url):
        url = chat_url(url)
        with self.executor() as db:
            self.session(db, sid)
        binding = 'bind_' + secrets.token_hex(16)
        with self.database() as db, db:
            db.execute('INSERT INTO bindings VALUES(?,?,?,?,?)', (binding, self.principal, sid, url, time.time()))
        return {'binding_id': binding, 'url': url}

    def binding(self, binding):
        with self.database() as db:
            row = db.execute('SELECT principal,session_id,url FROM bindings WHERE id=?', (binding,)).fetchone()
        if row is None or row[0] != self.principal or chat_url(row[2]) != row[2]:
            raise ValueError(tr('execution.companion.denied'))
        return row[1], row[2]

    def state(self, binding, reply_key=None):
        if reply_key is not None and (not isinstance(reply_key, str) or not re.fullmatch('[0-9a-f]{16}', reply_key)):
            raise ValueError(tr('execution.companion.invalid'))
        sid, url = self.binding(binding)
        with self.executor() as db:
            session, store = self.session(db, sid)
            reader = PipelineRuntime()
            reader.store = store
            pipelines = reader._pipeline_records(sid)
            rows = db.execute('SELECT seq,kind,data FROM events WHERE session_id=? ORDER BY seq', (sid,)).fetchall()
            jobs, milestone, final = {}, 0, 0
            mutating = {'harness_apply_patch', 'harness_exec', 'harness_verify', 'harness_checkpoint', 'harness_image_request',
                        'harness_image_deliver', 'harness_pipeline_start', 'harness_pipeline_control', 'harness_verification_register'}
            for seq, kind, raw in rows:
                value = json.loads(raw)
                if kind == 'job/started':
                    jobs[value['job_id']] = seq
                elif kind == 'job/finished':
                    jobs.pop(value['job_id'], None)
                    milestone = seq
                elif kind == 'pipeline/state':
                    milestone = seq
                elif kind == 'tool/result' and not value.get('is_error'):
                    if value.get('name') in mutating:
                        milestone = seq
                    elif value.get('name') == 'harness_finalize' and value.get('data', {}).get('verified'):
                        final = seq
            restart = db.execute("SELECT COALESCE(MAX(seq),0) FROM events WHERE kind='runtime/start'").fetchone()[0]
            unknown = db.execute("SELECT 1 FROM calls WHERE principal=? AND scope=? AND status='outcome_unknown' LIMIT 1", (self.principal, sid)).fetchone()
            running = db.execute("SELECT COUNT(*) FROM calls WHERE principal=? AND scope=? AND status='running'", (self.principal, sid)).fetchone()[0]
            images = store.images(sid)
            state = 'continue'
            if unknown or any(seq < restart for seq in jobs.values()) or any(image['status'] == 'delivery_unconfirmed' for image in images):
                state = 'blocked'
            elif not self.executor_alive():
                state = 'offline'
            elif jobs or running:
                state = 'working'
            elif final > milestone:
                state = 'complete'
            elif pipelines:
                latest = pipelines[-1]
                state = {'queued': 'working', 'running': 'working', 'waiting_image': 'image', 'completed': 'continue',
                         'failed': 'continue', 'interrupted': 'blocked', 'cancelled': 'paused'}[latest['status']]
                if latest['status'] == 'interrupted' and latest['error_code'] in {'', 'execution/service-interrupted'}:
                    state = 'interrupted'
            elif any(image['status'] == 'pending' for image in images):
                state = 'image'
            progress = final if state == 'complete' else milestone
            identity = f'{sid}:{progress}:{state}'
            if reply_key is not None and state != 'image':
                identity += ':' + reply_key
            key = hashlib.sha256(identity.encode()).hexdigest()
        with self.database() as db:
            pending = db.execute("SELECT id,prompt,status FROM dispatches WHERE binding_id=? AND status='dispatching' ORDER BY created LIMIT 1", (binding,)).fetchone()
            sent = [row[0] for row in db.execute("SELECT phase FROM dispatches WHERE binding_id=? AND milestone=? AND status='confirmed'", (binding, key))]
            if state == 'image' and reply_key is not None:
                sent.extend(row[0] for row in db.execute(
                    "SELECT phase FROM dispatches WHERE binding_id=? AND milestone=? AND phase='resume' AND status='confirmed'",
                    (binding, image_resume_milestone(key, reply_key))))
        return {'session_id': sid, 'url': url, 'state': state, 'milestone': key, 'sent_phases': sent,
                'activity': {'running_jobs': len(jobs), 'running_calls': running, 'pipeline_status': pipelines[-1]['status'] if pipelines else ''},
                'pending': {'id': pending[0], 'prompt': pending[1]} if pending else None,
                'has_checkpoint': bool(session['external_summary'] or session['next_steps'])}

    def claim(self, binding, milestone, phase, reply_key=None):
        with self.lock:
            state = self.state(binding, reply_key) if reply_key is not None else self.state(binding)
            phases = {'continue': {'continue'}, 'image': {'generate', 'deliver', 'resume'},
                      'complete': {'next_task'}, 'interrupted': {'resume'}}
            if state['pending'] or state['milestone'] != milestone or state['state'] not in phases:
                raise ValueError(tr('execution.companion.changed'))
            if phase not in phases[state['state']]:
                raise ValueError(tr('execution.companion.invalid'))
            if phase == 'resume' and state['state'] == 'image':
                if reply_key is None or 'generate' not in state['sent_phases']:
                    raise ValueError(tr('execution.companion.invalid'))
                milestone = image_resume_milestone(milestone, reply_key)
            dispatch = secrets.token_hex(16)
            # 接续正文是固定载荷，英中资源均保持“继续”，不随界面语言改写。
            prompt = tr('execution.companion.prompt')
            with self.database() as db, db:
                count = db.execute('SELECT COUNT(*) FROM dispatches WHERE binding_id=?', (binding,)).fetchone()[0]
                if count >= 100:
                    raise ValueError(tr('execution.companion.limit'))
                existing = db.execute('SELECT id,prompt,status FROM dispatches WHERE binding_id=? AND milestone=? AND phase=?',
                                      (binding, milestone, phase)).fetchone()
                if existing:
                    if existing[2] != 'not_sent':
                        raise ValueError(tr('execution.companion.changed'))
                    dispatch = existing[0]
                    db.execute("UPDATE dispatches SET status='dispatching',prompt=? WHERE id=?", (prompt, dispatch))
                else:
                    db.execute('INSERT INTO dispatches VALUES(?,?,?,?,?,?,?)', (dispatch, binding, milestone, phase, 'dispatching', prompt, time.time()))
            return {'id': dispatch, 'prompt': prompt}

    def receipt(self, binding, dispatch, outcome):
        self.binding(binding)
        if outcome not in {'confirmed', 'not_sent', 'unknown'}:
            raise ValueError(tr('execution.companion.invalid'))
        with self.lock, self.database() as db, db:
            row = db.execute('SELECT status FROM dispatches WHERE id=? AND binding_id=?', (dispatch, binding)).fetchone()
            if row is None:
                raise ValueError(tr('execution.companion.denied'))
            if outcome == 'not_sent' and row[0] == 'dispatching':
                db.execute("UPDATE dispatches SET status='not_sent' WHERE id=? AND binding_id=?", (dispatch, binding))
            elif outcome == 'confirmed':
                db.execute("UPDATE dispatches SET status='confirmed' WHERE id=? AND binding_id=?", (dispatch, binding))
        return {'outcome': outcome}


def create_server(config, port=8877, principal='local'):
    """独立回环监听，供命令入口和真实 HTTP 回归共用。"""
    companion = Companion(config, principal)
    language = current_language.get()

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, format, *args):
            """不把配对凭据和请求正文写入访问日志。"""

        def do_GET(self):
            self.handle_api()

        def do_POST(self):
            self.handle_api()

        def handle_api(self):
            with current_language.set(language):
                self.dispatch()

        def dispatch(self):
            actual_port = self.server.server_port
            if (self.headers.get('Host') not in {f'127.0.0.1:{actual_port}', f'localhost:{actual_port}'}
                    or not hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + companion.token)):
                self.respond({'error': tr('execution.companion.denied')}, 403)
                return
            try:
                path = urlsplit(self.path)
                if self.command == 'GET' and path.path == '/health':
                    result = companion.health()
                elif self.command == 'GET' and path.path == '/sessions':
                    result = companion.sessions()
                elif self.command == 'GET' and path.path == '/state':
                    params = parse_qs(path.query, keep_blank_values=True)
                    reply_keys = params.get('reply_key')
                    if reply_keys is not None and len(reply_keys) != 1:
                        raise ValueError(tr('execution.companion.invalid'))
                    result = companion.state(params['binding_id'][0], reply_keys[0] if reply_keys is not None else None)
                elif self.command == 'POST':
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 0 < length <= 16384:
                        raise ValueError(tr('execution.companion.invalid'))
                    value = json.loads(self.rfile.read(length).decode('utf-8'))
                    if path.path == '/bind' and set(value) == {'session_id', 'url'}:
                        result = companion.bind(value['session_id'], value['url'])
                    elif path.path == '/claim' and set(value) in ({'binding_id', 'milestone', 'phase'}, {'binding_id', 'milestone', 'phase', 'reply_key'}):
                        if 'reply_key' in value and value['reply_key'] is None:
                            raise ValueError(tr('execution.companion.invalid'))
                        result = companion.claim(value['binding_id'], value['milestone'], value['phase'], value.get('reply_key'))
                    elif path.path == '/receipt' and set(value) == {'binding_id', 'id', 'outcome'}:
                        result = companion.receipt(value['binding_id'], value['id'], value['outcome'])
                    else:
                        raise ValueError(tr('execution.companion.invalid'))
                else:
                    raise ValueError(tr('execution.companion.invalid'))
                status = 200
            except (ValueError, KeyError, TypeError, HarnessError, sqlite3.Error, OSError):
                result, status = {'error': tr('execution.companion.request_failed')}, 409
            self.respond(result, status)

        def respond(self, result, status):
            body = json.dumps(result, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.companion = companion
    return server


def serve(config, port, principal):
    """此专用监听仅绑定回环地址，不加入 MCP 的公网隧道路由。"""
    with create_server(config, port, principal) as server:
        print(tr('execution.companion.started', port=server.server_port, token=server.companion.token), flush=True)
        server.serve_forever(poll_interval=0.5)
