"""Academy 独立事实存储，不向对话日志或用户项目写入。"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path
from typing import Any

from .errors import AcademyError


def _unique_fields(pairs):
    """有界读取的记录拒绝重复字段，不能让后值覆盖身份或终态。"""
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('duplicate field')
        value[key] = item
    return value


class AcademyStore:
    """每次访问关闭连接，未知操作保留原记录且不自动重放。"""

    def __init__(self, home: Path):
        self.root = Path(home).resolve() / 'academy'
        self.projects_root = self.root / 'projects'
        if self.root.is_symlink() or self.projects_root.is_symlink():
            raise AcademyError('store-invalid')
        self.root.mkdir(parents=True, exist_ok=True)
        self.projects_root.mkdir(exist_ok=True)
        self.path = self.root / 'academy.sqlite3'
        self.lock = threading.RLock()
        fresh = not self.path.exists()
        with closing(self._db()) as db, db:
            if fresh:
                db.executescript('''
                    CREATE TABLE records(kind TEXT NOT NULL, id TEXT NOT NULL, body TEXT NOT NULL,
                        PRIMARY KEY(kind,id));
                    CREATE TABLE calls(id TEXT PRIMARY KEY, operation TEXT NOT NULL, scope TEXT NOT NULL,
                        fingerprint TEXT NOT NULL, status TEXT NOT NULL, body TEXT);
                    PRAGMA user_version=1;
                ''')
            elif db.execute('PRAGMA user_version').fetchone()[0] != 1:
                raise AcademyError('store-invalid')

    def _db(self):
        try:
            return sqlite3.connect(self.path, timeout=10)
        except sqlite3.Error as exc:
            raise AcademyError('store-invalid') from exc

    def get(self, kind: str, identity: str, *, max_bytes: int | None = None) -> dict[str, Any] | None:
        if max_bytes is not None and (type(max_bytes) is not int or not 1 <= max_bytes <= 4 * 1024 * 1024):
            raise AcademyError('invalid-request')
        with self.lock, closing(self._db()) as db:
            if max_bytes is None:
                row = db.execute('SELECT body FROM records WHERE kind=? AND id=?', (kind, identity)).fetchone()
            else:
                row = db.execute('SELECT substr(body,1,?),length(CAST(body AS BLOB)) FROM records WHERE kind=? AND id=?',
                                 (max_bytes + 1, kind, identity)).fetchone()
        if row is None:
            return None
        if max_bytes is not None and row[1] > max_bytes:
            raise AcademyError('store-invalid')
        try:
            if max_bytes is not None and not isinstance(row[0], str):
                raise ValueError('invalid text')
            value = json.loads(row[0], object_pairs_hook=_unique_fields if max_bytes is not None else None)
            if not isinstance(value, dict) or value.get('id') != identity:
                raise ValueError
            return value
        except (ValueError, TypeError, RecursionError) as exc:
            raise AcademyError('store-invalid') from exc

    def list(self, kind: str, *, limit: int | None = None, max_bytes: int | None = None) -> list[dict[str, Any]]:
        if limit is not None and (type(limit) is not int or not 1 <= limit <= 100):
            raise AcademyError('invalid-request')
        with self.lock, closing(self._db()) as db:
            query = 'SELECT id FROM records WHERE kind=? ORDER BY rowid DESC'
            params = (kind,)
            if limit is not None:
                query += ' LIMIT ?'
                params += (limit,)
            ids = [row[0] for row in db.execute(query, params)]
        return [self.get(kind, identity, max_bytes=max_bytes) for identity in ids]

    def put(self, kind: str, value: dict[str, Any]):
        with self.lock, closing(self._db()) as db, db:
            db.execute('INSERT INTO records VALUES(?,?,?) ON CONFLICT(kind,id) DO UPDATE SET body=excluded.body',
                       (kind, value['id'], json.dumps(value, ensure_ascii=False)))

    def is_earlier(self, kind: str, earlier: str, later: str) -> bool:
        """按持久记录顺序核对两个身份，不依赖时间戳或加载全量正文。"""
        with self.lock, closing(self._db()) as db:
            row = db.execute('SELECT a.rowid < b.rowid FROM records a JOIN records b ON a.kind=b.kind '
                             'WHERE a.kind=? AND a.id=? AND b.id=?', (kind, earlier, later)).fetchone()
        return row is not None and bool(row[0])

    def begin(self, request_id: str, operation: str, scope: str, fingerprint: str):
        if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', request_id):
            raise AcademyError('invalid-request')
        with self.lock, closing(self._db()) as db, db:
            row = db.execute('SELECT operation,scope,fingerprint,status,body FROM calls WHERE id=?', (request_id,)).fetchone()
            if row:
                if tuple(row[:3]) != (operation, scope, fingerprint):
                    raise AcademyError('request-conflict')
                if row[3] == 'unknown':
                    raise AcademyError('outcome-unknown')
                return self.receipt(request_id)
            # 先记意图。任何未完成操作在重启后仍是未知，禁止隐式补跑。
            db.execute('INSERT INTO calls VALUES(?,?,?,?,?,NULL)', (request_id, operation, scope, fingerprint, 'unknown'))
        return None

    def finish(self, request_id: str, result: dict[str, Any], *, failed=False):
        value = {'request_id': request_id, 'status': 'failed' if failed else 'ready', 'result': result, 'finished_at': time.time()}
        with self.lock, closing(self._db()) as db, db:
            db.execute('UPDATE calls SET status=?,body=? WHERE id=?', (value['status'], json.dumps(value, ensure_ascii=False), request_id))
        return value

    def receipt(self, request_id: str):
        with self.lock, closing(self._db()) as db:
            row = db.execute('SELECT status,body FROM calls WHERE id=?', (request_id,)).fetchone()
        if row is None:
            raise AcademyError('receipt-not-found')
        if row[0] == 'unknown':
            return {'request_id': request_id, 'status': 'unknown', 'result': {}}
        try:
            value = json.loads(row[1])
            if not isinstance(value, dict) or value.get('request_id') != request_id or value.get('status') != row[0] or not isinstance(value.get('result'), dict):
                raise ValueError
            return value
        except (ValueError, TypeError) as exc:
            raise AcademyError('store-invalid') from exc
