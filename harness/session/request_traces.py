"""传输边界的请求归档，独立于模型上下文和界面历史。

请求体按内容寻址，仅存储一次。不采集请求头，因此不会记录传输凭据。
图片载荷替换为摘要；提示词和工具文字保留，传输字节数记录原始请求。
"""
from __future__ import annotations

from ..core.sqlite import prepare_database

import hashlib
import json
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4
from urllib.parse import urlsplit, urlunsplit


_IMAGE_DATA = re.compile(r'data:image/[^;,\s"\']+;base64,[A-Za-z0-9+/=\r\n]+', re.IGNORECASE)
_USAGE_FIELDS = ('input_tokens', 'output_tokens', 'reasoning_tokens', 'cache_read_tokens', 'cache_reported_input_tokens')


def _valid_usage(usage):
    """缺失、负数或错误类型不能冒充已报告的零用量。"""
    return isinstance(usage, dict) and 'input_tokens' in usage and 'output_tokens' in usage and all(
        type(usage.get(key, 0)) is int and usage.get(key, 0) >= 0 for key in _USAGE_FIELDS)


def _usage_totals(rows):
    """同一物理请求只计一次；缓存与思考是已有输入／输出的子集。"""
    totals = dict.fromkeys(_USAGE_FIELDS, 0)
    reported = inconsistent = 0
    for row in rows:
        usage = row.get('usage')
        if not _valid_usage(usage):
            continue
        reported += 1
        # 正缓存读取本身就是报告证据；未附覆盖量时按该请求输入计算，不修改归档原件。
        cache_read = usage.get('cache_read_tokens', 0)
        cache_input = usage.get('cache_reported_input_tokens', usage['input_tokens'] if cache_read > 0 else 0)
        for key in totals:
            totals[key] += cache_input if key == 'cache_reported_input_tokens' else usage.get(key, 0)
        if not 0 <= cache_read <= cache_input <= usage['input_tokens']:
            inconsistent += 1
    return {**totals, 'total_tokens': totals['input_tokens'] + totals['output_tokens']}, reported, inconsistent


def _cost_totals(rows):
    """流式汇总元数据，额外内存只随有请求的会话数增长。"""
    counts = {'requests': 0, 'compaction_requests': 0, 'body_bytes': 0}
    sessions = set()

    def observed():
        for row in rows:
            counts['requests'] += 1
            counts['compaction_requests'] += row['purpose'] == 'compaction'
            counts['body_bytes'] += row['body_bytes']
            sessions.add(row['session_id'])
            yield row

    totals, reported, inconsistent = _usage_totals(observed())
    return {**counts, 'usage': totals, 'reported_requests': reported,
            'unreported_requests': counts['requests'] - reported,
            'cache_inconsistent_requests': inconsistent, 'session_count': len(sessions)}


def _without_image_payload(value):
    """归档只保留图片摘要，任何层级的内联图片都不得进入数据库。"""
    def marker(raw):
        digest = hashlib.sha256(raw.encode('utf-8')).hexdigest()
        return f'[image omitted; sha256={digest}; chars={len(raw)}]'

    if isinstance(value, str):
        return _IMAGE_DATA.sub(lambda match: marker(match.group(0)), value)
    if isinstance(value, list):
        return [_without_image_payload(item) for item in value]
    if isinstance(value, dict):
        if value.get('type') == 'base64' and str(value.get('media_type', '')).startswith('image/'):
            return {key: marker(item) if key == 'data' and isinstance(item, str)
                    else _without_image_payload(item) for key, item in value.items()}
        return {key: _without_image_payload(item) for key, item in value.items()}
    return value


SCHEMA = """
CREATE TABLE bodies (hash TEXT PRIMARY KEY, body TEXT NOT NULL);
CREATE TABLE requests (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
    session TEXT NOT NULL, parent TEXT, metadata TEXT NOT NULL, hash TEXT NOT NULL);
CREATE INDEX request_session ON requests(session, seq);
CREATE INDEX request_parent ON requests(parent);
CREATE TABLE trace_sessions (id TEXT PRIMARY KEY, parent TEXT);
CREATE INDEX trace_session_parent ON trace_sessions(parent);
"""


class RequestTraces:
    def __init__(self, path: Path | None = None):
        self.path = path
        self._lock = threading.RLock()
        self._rows = {}
        self._bodies = {}
        self._parents = {}
        self._next_seq = 1
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            with self._db() as db:
                prepare_database(db, SCHEMA)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def register_session(self, session):
        """即使中间代理没有调用模型，也记录其归属。"""
        with self._lock:
            parent = session.header.parent_session
            if self.path is None:
                self._parents.setdefault(session.id, parent)
            else:
                with self._db() as db:
                    db.execute('INSERT OR IGNORE INTO trace_sessions VALUES (?, ?)', (session.id, parent))

    def start(self, session, *, body, url, request_id, purpose):
        from ..llm.request_input import request_input
        # 传输体仅用于计量；图片在归档边界移除，实际模型请求不受影响。
        wire_body = body
        parsed = json.loads(body)
        archived = _without_image_payload(parsed)
        redacted = archived != parsed
        if redacted:
            body = json.dumps(archived, ensure_ascii=False)
        digest = hashlib.sha256(body.encode('utf-8')).hexdigest()
        endpoint = urlsplit(url)
        safe_url = urlunsplit((endpoint.scheme, endpoint.netloc.split('@')[-1], endpoint.path, '', ''))
        row = {'id': uuid4().hex, 'session_id': session.id, 'parent_session_id': session.header.parent_session,
               'request_id': request_id, 'purpose': purpose, 'time': int(time.time() * 1000),
               'model': parsed.get('model'), 'endpoint': safe_url, 'status': 'started',
               'body_hash': digest, 'body_chars': len(wire_body), 'body_bytes': len(wire_body.encode('utf-8')),
               'body_redacted': redacted,
               'message_count': len(request_input(parsed)), 'tool_count': len(parsed.get('tools', [])),
               'context_checkpoint': session.request_checkpoint()}
        with self._lock:
            if self.path is None:
                self._parents.setdefault(session.id, session.header.parent_session)
                row['seq'] = self._next_seq
                self._next_seq += 1
                self._bodies.setdefault(digest, body)
                self._rows[row['id']] = row
            else:
                with self._db() as db:
                    db.execute('INSERT OR IGNORE INTO trace_sessions VALUES (?, ?)', (session.id, session.header.parent_session))
                    db.execute('INSERT OR IGNORE INTO bodies VALUES (?, ?)', (digest, body))
                    cursor = db.execute('INSERT INTO requests(id, session, parent, metadata, hash) VALUES(?,?,?,?,?)',
                                        (row['id'], session.id, row['parent_session_id'], json.dumps(row, ensure_ascii=False), digest))
                    row['seq'] = cursor.lastrowid
        return row['id']

    def finish(self, trace_id, status, usage=None, observation=None):
        terminal = {'status': status, 'finished_at': int(time.time() * 1000), 'usage': usage}
        if observation is not None:
            terminal['observation'] = {key: observation[key] for key in (
                'duration_ms', 'first_event_ms', 'event_count', 'terminal_reason', 'error_type', 'error_code', 'error_details', 'text_diagnostic') if key in observation}
        with self._lock:
            if self.path is None:
                row = self._rows[trace_id]
                row.update(terminal)
            else:
                with self._db() as db:
                    row = json.loads(db.execute('SELECT metadata FROM requests WHERE id=?', (trace_id,)).fetchone()[0])
                    row.update(terminal)
                    db.execute('UPDATE requests SET metadata=? WHERE id=?', (json.dumps(row, ensure_ascii=False), trace_id))

    def delete(self, session_id):
        """删除会话的请求追踪树，并回收不再引用的请求体。"""
        with self._lock:
            if self.path is None:
                owners = self._scope(session_id, True)
                self._rows = {key: row for key, row in self._rows.items() if row['session_id'] not in owners}
                self._parents = {key: parent for key, parent in self._parents.items() if key not in owners}
                used = {row['body_hash'] for row in self._rows.values()}
                self._bodies = {key: body for key, body in self._bodies.items() if key in used}
            else:
                with self._db() as db:
                    db.execute('''WITH RECURSIVE owners(id) AS (
                        SELECT ? UNION SELECT r.id FROM trace_sessions r JOIN owners o ON r.parent=o.id)
                        DELETE FROM requests WHERE session IN (SELECT id FROM owners)''', (session_id,))
                    db.execute('''WITH RECURSIVE owners(id) AS (
                        SELECT ? UNION SELECT r.id FROM trace_sessions r JOIN owners o ON r.parent=o.id)
                        DELETE FROM trace_sessions WHERE id IN (SELECT id FROM owners)''', (session_id,))
                    db.execute('DELETE FROM bodies WHERE hash NOT IN (SELECT hash FROM requests)')

    def _scope(self, session_id, include_children):
        if self.path is None:
            owners = {session_id}
            if include_children:
                while True:
                    expanded = owners | {key for key, parent in self._parents.items() if parent in owners}
                    if expanded == owners:
                        break
                    owners = expanded
            return owners
        return {session_id}

    def page(self, session_id, *, include_children=True, before=None, limit=30, agent_session_id=None):
        limit = max(1, min(limit, 100))
        with self._lock:
            if self.path is None:
                owners = self._scope(session_id, include_children)
                rows = [dict(r) for r in reversed(list(self._rows.values()))
                        if r['session_id'] in owners and (agent_session_id is None or r['session_id'] == agent_session_id)
                        and (before is None or r['seq'] < before)][:limit + 1]
            else:
                with self._db() as db:
                    rows = db.execute('''WITH RECURSIVE owners(id) AS (
                        SELECT ? UNION SELECT r.id FROM trace_sessions r JOIN owners o ON r.parent=o.id WHERE ?)
                        SELECT seq, metadata FROM requests WHERE session IN (SELECT id FROM owners)
                        AND (? IS NULL OR session = ?) AND (? IS NULL OR seq < ?) ORDER BY seq DESC LIMIT ?''',
                        (session_id, int(include_children), agent_session_id, agent_session_id, before, before, limit + 1)).fetchall()
                    rows = [{**json.loads(r['metadata']), 'seq': r['seq']} for r in rows]
        return {'requests': rows[:limit], 'has_more': len(rows) > limit,
                'next_before': rows[limit - 1]['seq'] if len(rows) > limit else None}

    def read(self, session_id, trace_id):
        # 详情读取仅限追踪记录所属会话或其祖先。
        with self._lock:
            if self.path is None:
                row = self._rows.get(trace_id)
                if row is None or row['session_id'] not in self._scope(session_id, True):
                    return None
                return dict(row), self._bodies[row['body_hash']]
            with self._db() as db:
                result = db.execute('''WITH RECURSIVE owners(id) AS (
                    SELECT ? UNION SELECT r.id FROM trace_sessions r JOIN owners o ON r.parent=o.id)
                    SELECT r.seq, r.metadata, b.body FROM requests r JOIN bodies b ON b.hash=r.hash
                    WHERE r.id=? AND r.session IN (SELECT id FROM owners)''', (session_id, trace_id)).fetchone()
                return ({**json.loads(result['metadata']), 'seq': result['seq']}, result['body']) if result else None

    def metadata(self, session_id, trace_id):
        """按作用域查询实时状态，不加载或解析提示词正文。"""
        with self._lock:
            if self.path is None:
                row = self._rows.get(trace_id)
                return dict(row) if row is not None and row['session_id'] in self._scope(session_id, True) else None
            with self._db() as db:
                result = db.execute('''WITH RECURSIVE owners(id) AS (
                    SELECT ? UNION SELECT r.id FROM trace_sessions r JOIN owners o ON r.parent=o.id)
                    SELECT r.seq, r.metadata FROM requests r
                    WHERE r.id=? AND r.session IN (SELECT id FROM owners)''', (session_id, trace_id)).fetchone()
                return {**json.loads(result['metadata']), 'seq': result['seq']} if result else None

    def previous(self, row):
        """查询同一会话和用途的上一请求，排除未来数据。"""
        with self._lock:
            if self.path is None:
                for candidate in reversed(list(self._rows.values())):
                    if candidate['seq'] < row['seq'] and candidate['session_id'] == row['session_id'] and candidate['purpose'] == row['purpose']:
                        return dict(candidate), self._bodies[candidate['body_hash']]
                return None
            with self._db() as db:
                result = db.execute('''SELECT r.seq, r.metadata, b.body FROM requests r JOIN bodies b ON b.hash=r.hash
                    WHERE r.session=? AND r.seq<? AND json_extract(r.metadata, '$.purpose')=? ORDER BY r.seq DESC LIMIT 1''',
                    (row['session_id'], row['seq'], row['purpose'])).fetchone()
                return ({**json.loads(result['metadata']), 'seq': result['seq']}, result['body']) if result else None

    def latest(self, session_id, *, purpose='agent'):
        """仅查询元数据，上下文检查不加载全部归档请求体。"""
        with self._lock:
            if self.path is None:
                for row in reversed(list(self._rows.values())):
                    if row['session_id'] == session_id and row['purpose'] == purpose:
                        return dict(row)
                return None
            with self._db() as db:
                result = db.execute('''SELECT seq, metadata FROM requests
                    WHERE session=? AND json_extract(metadata, '$.purpose')=? ORDER BY seq DESC LIMIT 1''',
                    (session_id, purpose)).fetchone()
                return {**json.loads(result['metadata']), 'seq': result['seq']} if result else None

    def latest_sequence(self, session_id, *, include_children=False):
        """冻结请求边界，避免后台投影混入相同事件检查点的后续重试。"""
        with self._lock:
            if self.path is None:
                owners = self._scope(session_id, include_children)
                return max((row['seq'] for row in self._rows.values() if row['session_id'] in owners), default=0)
            with self._db() as db:
                return db.execute('''WITH RECURSIVE owners(id) AS (
                    SELECT ? UNION SELECT r.id FROM trace_sessions r JOIN owners o ON r.parent=o.id WHERE ?)
                    SELECT COALESCE(MAX(seq), 0) FROM requests WHERE session IN (SELECT id FROM owners)''',
                    (session_id, int(include_children))).fetchone()[0]

    def cost_summary(self, session_id, *, max_sequence=None):
        """汇总当前会话及全部后代的物理请求，不读取请求正文。"""
        with self._lock:
            if self.path is None:
                owners = self._scope(session_id, True)
                return _cost_totals(row for row in self._rows.values() if row['session_id'] in owners
                                    and (max_sequence is None or row['seq'] <= max_sequence))
            else:
                with self._db() as db:
                    rows = db.execute('''WITH RECURSIVE owners(id) AS (
                        SELECT ? UNION SELECT r.id FROM trace_sessions r JOIN owners o ON r.parent=o.id)
                        SELECT metadata FROM requests WHERE session IN (SELECT id FROM owners)
                        AND (? IS NULL OR seq<=?)''', (session_id, max_sequence, max_sequence))
                    return _cost_totals(json.loads(row['metadata']) for row in rows)

    def context_snapshot(self, session_id, *, through, max_sequence=None):
        """按本会话的事件边界汇总真实传输尝试，不读取请求正文。"""
        with self._lock:
            if self.path is None:
                rows = [dict(row) for row in self._rows.values() if row['session_id'] == session_id
                        and row['context_checkpoint']['event_seq'] <= through
                        and (max_sequence is None or row['seq'] <= max_sequence)]
            else:
                with self._db() as db:
                    rows = [{**json.loads(row['metadata']), 'seq': row['seq']} for row in db.execute(
                        '''SELECT seq, metadata FROM requests WHERE session=?
                        AND json_extract(metadata, '$.context_checkpoint.event_seq')<=?
                        AND (? IS NULL OR seq<=?) ORDER BY seq''',
                        (session_id, through, max_sequence, max_sequence))]
        totals, reported, inconsistent = _usage_totals(rows)
        latest = latest_reported = None
        for row in rows:
            usage = row.get('usage')
            valid = _valid_usage(usage)
            if row['purpose'] == 'agent':
                latest = row
                if valid:
                    latest_reported = row
        return {'usage': totals,
                'requests': len(rows), 'reported_requests': reported, 'unreported_requests': len(rows) - reported,
                'compaction_requests': sum(row['purpose'] == 'compaction' for row in rows),
                'cache_inconsistent_requests': inconsistent,
                'latest_request': latest, 'latest_reported_request': latest_reported}


def request_observer(session, *, request_id, purpose='agent'):
    """每个逻辑请求一个观察器，每次传输尝试各自拥有独立标识。"""
    async def observe(data):
        if data['phase'] == 'started':
            return session.request_traces.start(session, body=data['body'], url=data['url'],
                                                request_id=request_id, purpose=purpose)
        session.request_traces.finish(data['trace_id'], data['phase'], data.get('usage'), data.get('observation'))
    return observe
