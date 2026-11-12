"""业务会话、幂等请求和可信证据的持久记录。"""
from __future__ import annotations

from ..i18n import tr

from ..core.sqlite import prepare_database
from ..core.file_lock import lock_file

import json
import math
from pathlib import Path
import sqlite3
import time
import uuid

from ..tools.schema import validate
from .catalog import VERIFICATION_COMMAND, VERIFICATION_ID
from .policy import fail


SCHEMA = """
CREATE TABLE sessions(id TEXT PRIMARY KEY, principal TEXT NOT NULL,
    project TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0, data TEXT NOT NULL);
CREATE TABLE calls(principal TEXT, scope TEXT, tool TEXT, request_id TEXT,
    fingerprint TEXT NOT NULL, status TEXT NOT NULL, result TEXT,
    PRIMARY KEY(principal,scope,tool,request_id));
CREATE TABLE events(seq INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL, time REAL NOT NULL);
CREATE TABLE session_verification(session_id TEXT NOT NULL,
    name TEXT NOT NULL, command TEXT NOT NULL, PRIMARY KEY(session_id,name));
CREATE TABLE image_requests(id TEXT PRIMARY KEY, session_id TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE evidence(id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
    kind TEXT NOT NULL, data TEXT NOT NULL);
"""


def _record_data(raw, *, reserved=()):
    """损坏正文保留原件；身份只能来自数据库列。"""
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        fail("execution/store-invalid", tr('execution.store.invalid_record'))
    if not isinstance(value, dict) or any(key in value for key in reserved):
        fail("execution/store-invalid", tr('execution.store.invalid_record'))
    return value


def _image_delivery_valid(delivery, path, *, committed):
    """交付回执绑定原目标和内容身份，不接受深层损坏字段。"""
    fields = {'mime_type', 'width', 'height', 'file_id', 'bytes', 'sha256'}
    if committed:
        fields.update(('backup_id', 'changed_files', 'versions'))
    if not isinstance(delivery, dict) or set(delivery) != fields:
        return False
    digest = delivery['sha256']
    if (not isinstance(digest, str) or len(digest) != 64
            or any(char not in '0123456789abcdef' for char in digest)
            or not isinstance(delivery['file_id'], str) or not delivery['file_id']
            or any(type(delivery[key]) is not int or delivery[key] <= 0 for key in ('width', 'height', 'bytes'))
            or delivery['width'] * delivery['height'] > 40_000_000):
        return False
    suffixes = {'image/png': {'.png'}, 'image/jpeg': {'.jpg', '.jpeg'}, 'image/webp': {'.webp'}}
    mime = delivery['mime_type']
    if not isinstance(mime, str) or Path(path).suffix.lower() not in suffixes.get(mime, set()):
        return False
    if committed:
        backup = delivery['backup_id']
        if (not isinstance(backup, str) or not backup.startswith('patch_')
                or len(backup) != 38 or any(char not in '0123456789abcdef' for char in backup[6:])
                or delivery['changed_files'] != [path] or delivery['versions'] != {path: digest}):
            return False
    return True


class ExecutionStore:
    """一个服务进程拥有存储；恢复时不重放未知副作用。"""

    def __init__(self, home: Path):
        home.mkdir(parents=True, exist_ok=True)
        self._owner = (home / "executor.owner").open("a+b")
        try:
            lock_file(self._owner)
        except OSError:
            self._owner.close()
            fail("execution/store-in-use", tr('execution.store.c4c64233c935'))
        try:
            self.db = sqlite3.connect(home / "executor.sqlite3")
            try:
                self.db.row_factory = sqlite3.Row
                prepare_database(self.db, SCHEMA)
                self.db.execute("PRAGMA journal_mode=WAL")
                self.db.execute("UPDATE calls SET status='outcome_unknown' WHERE status='running'")
                self.db.commit()
            except BaseException:
                self.db.close()
                raise
        except BaseException:
            self._owner.close()
            raise

    def open_session(self, principal, project, title, project_root):
        sid = "he_" + uuid.uuid4().hex
        data = {"title": title, "created_at": time.time(), "external_summary": "", "next_steps": [], "project_root": project_root}
        with self.db:
            self.db.execute("INSERT INTO sessions VALUES(?,?,?,?,?)", (sid, principal, project, 0, json.dumps(data, ensure_ascii=False)))
        return self.session(sid, principal)

    def session(self, sid, principal):
        row = self.db.execute("SELECT * FROM sessions WHERE id=? AND principal=?", (sid, principal)).fetchone()
        if row is None:
            fail("execution/session-not-found", tr('execution.store.9eadfc791ca7'))
        data = _record_data(row["data"], reserved=('session_id', 'project_id', 'revision'))
        if (set(data) != {'title', 'created_at', 'external_summary', 'next_steps', 'project_root'}
                or not all(isinstance(data[key], str) for key in ('title', 'external_summary', 'project_root'))
                or type(data['created_at']) not in (int, float)
                or isinstance(data['created_at'], float) and not math.isfinite(data['created_at'])
                or not isinstance(data['next_steps'], list) or not all(isinstance(step, str) for step in data['next_steps'])
                or type(row['revision']) is not int or row['revision'] < 0):
            fail("execution/store-invalid", tr('execution.store.invalid_record'))
        return {"session_id": row["id"], "project_id": row["project"], "revision": row["revision"], **data}

    def checkpoint(self, sid, principal, revision, summary, next_steps):
        current = self.session(sid, principal)
        if current["revision"] != revision:
            fail("execution/revision-conflict", tr('execution.store.e5ed3877a4c9'))
        data = {key: current[key] for key in ("title", "created_at", "project_root")}
        data.update(external_summary=summary, next_steps=next_steps)
        with self.db:
            changed = self.db.execute("UPDATE sessions SET revision=revision+1,data=? WHERE id=? AND principal=? AND revision=?", (json.dumps(data, ensure_ascii=False), sid, principal, revision)).rowcount
            if changed != 1:
                fail("execution/revision-conflict", tr('execution.store.641947762efd'))
        return self.session(sid, principal)

    def begin(self, principal, scope, tool, request_id, fingerprint):
        if not isinstance(request_id, str) or not request_id or len(request_id) > 128:
            fail("execution/request-id-required", tr('execution.store.3f5b8373c7ce'))
        key = (principal, scope, tool, request_id)
        row = self.db.execute("SELECT * FROM calls WHERE principal=? AND scope=? AND tool=? AND request_id=?", key).fetchone()
        if row:
            if row["fingerprint"] != fingerprint:
                fail("execution/request-conflict", tr('execution.store.9ddba31a5c5e'))
            if row["status"] != "finished":
                fail("execution/outcome-unknown", tr('execution.store.f8ddf9268667'))
            return _record_data(row["result"])
        with self.db:
            self.db.execute("INSERT INTO calls VALUES(?,?,?,?,?,'running',NULL)", (*key, fingerprint))
        return None

    def finish(self, principal, scope, tool, request_id, result):
        with self.db:
            changed = self.db.execute("UPDATE calls SET status='finished',result=? WHERE principal=? AND scope=? AND tool=? AND request_id=? AND status='running'", (json.dumps(result, ensure_ascii=False), principal, scope, tool, request_id)).rowcount
            if changed != 1:
                fail("execution/outcome-unknown", tr('execution.store.3d8085c93e6b'))

    def call_receipt(self, principal, scope, tool, request_id):
        """只回查原请求，不将缺失、运行中或未知结果解释为可以重放。"""
        row = self.db.execute('SELECT status,result FROM calls WHERE principal=? AND scope=? AND tool=? AND request_id=?',
                              (principal, scope, tool, request_id)).fetchone()
        if row is None:
            return {'state': 'not_found', 'result': None}
        if row['status'] not in {'running', 'outcome_unknown', 'finished'}:
            fail('execution/store-invalid', tr('execution.store.invalid_record'))
        result = None
        if row['status'] == 'finished':
            result = _record_data(row['result'])
            if (set(result) != {'session_id', 'call_id', 'request_id', 'is_error', 'status', 'data'}
                    or result['request_id'] != request_id or not isinstance(result['call_id'], str) or not result['call_id']
                    or type(result['is_error']) is not bool
                    or result['status'] != ('error' if result['is_error'] else 'completed')
                    or result['session_id'] is not None and not isinstance(result['session_id'], str)
                    or tool != 'harness_session_open' and result['session_id'] != scope):
                fail('execution/store-invalid', tr('execution.store.invalid_record'))
        elif row['result'] is not None:
            fail('execution/store-invalid', tr('execution.store.invalid_record'))
        return {'state': row['status'], 'result': result}

    def event(self, sid, kind, data):
        with self.db:
            cursor = self.db.execute("INSERT INTO events(session_id,kind,data,time) VALUES(?,?,?,?)", (sid, kind, json.dumps(data, ensure_ascii=False), time.time()))
        return cursor.lastrowid

    def events(self, sid, cursor=0, limit=100):
        rows = self.db.execute("SELECT * FROM events WHERE session_id=? AND seq>? ORDER BY seq LIMIT ?", (sid, cursor, limit + 1)).fetchall()
        return {"events": [{"cursor": row["seq"], "kind": row["kind"], "data": json.loads(row["data"]), "time": row["time"]} for row in rows[:limit]], "cursor": rows[min(len(rows), limit)-1]["seq"] if rows else cursor, "has_more": len(rows) > limit}

    def evidence(self, sid, kind, data):
        eid = "ev_" + uuid.uuid4().hex
        with self.db:
            self.db.execute("INSERT INTO evidence VALUES(?,?,?,?)", (eid, sid, kind, json.dumps(data, ensure_ascii=False)))
        return eid

    def verification(self, sid):
        """读取本会话持久保存的验证命令。"""
        commands = {}
        for row in self.db.execute("SELECT name,command FROM session_verification WHERE session_id=?", (sid,)):
            try:
                command = json.loads(row['command'])
            except (ValueError, TypeError):
                fail("execution/store-invalid", tr('execution.store.invalid_record'))
            # 复用登记协议，字符串或映射不能通过 tuple 转换伪装成命令参数。
            if validate(row['name'], VERIFICATION_ID) or validate(command, VERIFICATION_COMMAND):
                fail("execution/store-invalid", tr('execution.store.invalid_record'))
            commands[row['name']] = tuple(command)
        return commands

    def register_verification(self, sid, name, command):
        """仅更新指定会话的一项登记，不修改本机项目权限。"""
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO session_verification VALUES(?,?,?)",
                            (sid, name, json.dumps(command)))

    def get_evidence(self, sid, eid):
        row = self.db.execute("SELECT * FROM evidence WHERE id=? AND session_id=?", (eid, sid)).fetchone()
        if row is None:
            fail("execution/evidence-not-found", tr('execution.store.48091929aa49'))
        data = _record_data(row["data"], reserved=('id', 'kind', 'session_id'))
        return {"id": eid, "kind": row["kind"], **data}

    def image_request(self, sid, data):
        """需求仅属于创建它的会话，状态在重启后仍可恢复。"""
        image_id = "img_" + uuid.uuid4().hex
        value = {"image_id": image_id, "status": "pending", "created_at": time.time(), **data}
        with self.db:
            self.db.execute("INSERT INTO image_requests VALUES(?,?,?)", (image_id, sid, json.dumps(value, ensure_ascii=False)))
        return value

    def images(self, sid, image_id=None):
        """不允许通过图片标识越过会话边界。"""
        rows = self.db.execute("SELECT id,data FROM image_requests WHERE session_id=? ORDER BY rowid", (sid,)).fetchall()
        values = []
        for row in rows:
            if image_id is not None and row['id'] != image_id:
                continue
            value = _record_data(row['data'])
            fields = {'image_id', 'status', 'created_at', 'prompt', 'path'}
            delivered = value.get('status') == 'delivered'
            if delivered:
                fields.add('delivered_at')
            if value.get('status') in ('delivered', 'delivery_unconfirmed'):
                fields.add('delivery')
            if (set(value) != fields or value.get('image_id') != row['id']
                    or value.get('status') not in ('pending', 'delivery_unconfirmed', 'delivered')
                    or not all(isinstance(value.get(key), str) and value[key] for key in ('prompt', 'path'))
                    or not all(type(value.get(key)) in (int, float)
                               and (not isinstance(value[key], float) or math.isfinite(value[key]))
                               for key in (('created_at', 'delivered_at') if delivered else ('created_at',)))
                    or value.get('status') in ('delivered', 'delivery_unconfirmed')
                    and not _image_delivery_valid(value['delivery'], value['path'], committed=delivered)):
                fail("execution/store-invalid", tr('execution.store.invalid_record'))
            values.append(value)
        if image_id is not None and not values:
            fail("execution/image-not-found", tr('execution.store.6b2ed133d184'))
        return values

    def image_delivery_begin(self, sid, image_id, delivery):
        """先持久保存交付意图，文件写入与最终记录之间失败时不重放。"""
        value = self.images(sid, image_id)[0]
        if value['status'] != 'pending':
            fail("execution/image-delivery-unconfirmed", tr('execution.runtime.image_delivery_unconfirmed'))
        if not _image_delivery_valid(delivery, value['path'], committed=False):
            fail("execution/store-invalid", tr('execution.store.invalid_record'))
        value.update(status='delivery_unconfirmed', delivery=delivery)
        with self.db:
            self.db.execute("UPDATE image_requests SET data=? WHERE id=? AND session_id=?",
                            (json.dumps(value, ensure_ascii=False), image_id, sid))

    def image_delivered(self, sid, image_id, delivery):
        """只保存图片元信息，不保存带凭据的临时下载地址。"""
        value = self.images(sid, image_id)[0]
        if (value['status'] != 'delivery_unconfirmed'
                or not _image_delivery_valid(delivery, value['path'], committed=True)
                or any(delivery.get(key) != item for key, item in value['delivery'].items())):
            fail("execution/store-invalid", tr('execution.store.invalid_record'))
        value.update(status="delivered", delivered_at=time.time(), delivery=delivery)
        with self.db:
            self.db.execute("UPDATE image_requests SET data=? WHERE id=? AND session_id=?", (json.dumps(value, ensure_ascii=False), image_id, sid))
        return value

    def close(self):
        """先关闭数据库再释放所有权，重复关闭保持安全。"""
        self.db.close()
        self._owner.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
