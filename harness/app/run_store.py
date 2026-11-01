"""运行记录的磁盘索引与有界对象缓存，不删除历史或幂等键。"""

from harness.i18n import tr
from collections import OrderedDict
from collections.abc import Mapping, MutableMapping
import threading

from ..core.runs import ACTIVE_STATES
from ..core.run_storage import RunStorage


class RunKeys(Mapping):
    def __init__(self, db):
        self.db = db

    def __getitem__(self, key):
        with self.db() as db:
            row = db.execute('SELECT fingerprint,run_id FROM run_keys WHERE session_id=? AND key=?', key).fetchone()
        if row is None:
            raise KeyError(key)
        return tuple(row)

    def __iter__(self):
        with self.db() as db:
            yield from db.execute('SELECT session_id,key FROM run_keys')

    def __len__(self):
        with self.db() as db:
            return db.execute('SELECT COUNT(*) FROM run_keys').fetchone()[0]


class RunRecords(MutableMapping):
    """活动对象保持身份；终态对象按需读取，最多缓存 128 项。"""
    SELECT = 'SELECT r.id,r.data FROM runs r'
    ROOT = "json_extract(r.data,'$.parent_session_id') IS NULL"

    def __init__(self, db, limit=128):
        self.db, self.limit = db, limit
        self.active = {}
        self.recent = OrderedDict()
        self.lock = threading.RLock()

    @staticmethod
    def decode(row):
        return RunStorage.decode(row[1], expected_id=row[0])

    def __getitem__(self, key):
        with self.lock:
            record = self.active.get(key) or self.recent.get(key)
            if record is not None:
                self.remember(record)
                return record
            with self.db() as db:
                row = db.execute(self.SELECT + ' WHERE ' + self.ROOT + ' AND r.id=?', (key,)).fetchone()
            if row is None:
                raise KeyError(key)
            record = self.decode(row)
            self.remember(record)
            return record

    def remember(self, record):
        with self.lock:
            existing = self.active.get(record.id) or self.recent.get(record.id)
            if existing is not None and existing is not record:
                existing.__dict__.update(record.__dict__)
                record = existing
            if record.status in ACTIVE_STATES:
                self.active[record.id] = record
                self.recent.pop(record.id, None)
            else:
                self.active.pop(record.id, None)
                self.recent[record.id] = record
                self.recent.move_to_end(record.id)
                while len(self.recent) > self.limit:
                    self.recent.popitem(last=False)

    def __setitem__(self, key, record):
        if key != record.id:
            raise ValueError(tr('runtime.validation.438eca651cd5'))
        self.remember(record)

    def __delitem__(self, key):
        # 生命周期缓存不是历史清理入口，禁止静默丢弃持久记录。
        raise TypeError(tr('runtime.validation.3382e58952f1'))

    def __iter__(self):
        with self.db() as db:
            for row in db.execute('SELECT r.id FROM runs r WHERE ' + self.ROOT + ' ORDER BY r.rowid'):
                yield row[0]

    def __len__(self):
        with self.db() as db:
            return db.execute('SELECT COUNT(*) FROM runs r WHERE ' + self.ROOT).fetchone()[0]

    def values(self):
        """为需要扫描的调用方提供流式读取，不把全历史同时物化。"""
        with self.db() as db:
            for row in db.execute(self.SELECT + ' WHERE ' + self.ROOT + ' ORDER BY r.rowid'):
                candidate = self.decode(row)
                with self.lock:
                    record = self.active.get(candidate.id) or self.recent.get(candidate.id)
                yield record if record is not None else candidate

    def page(self, session_id, limit, boundary=None, include_queued=True):
        clauses = [self.ROOT, "json_extract(r.data,'$.session_id')=?", "COALESCE(json_extract(r.data,'$.kind'),'prompt')='prompt'"]
        params = [session_id]
        if not include_queued:
            clauses.append("json_extract(r.data,'$.status')!='queued'")
        if boundary is not None:
            clauses.append("(json_extract(r.data,'$.created_at'),r.id)<(?,?)")
            params.extend(boundary)
        with self.db() as db:
            rows = db.execute(self.SELECT + ' WHERE ' + ' AND '.join(clauses) +
                " ORDER BY json_extract(r.data,'$.created_at') DESC,r.id DESC LIMIT ?", (*params, limit)).fetchall()
        result = []
        with self.lock:
            for row in rows:
                candidate = self.decode(row)
                record = self.active.get(candidate.id) or self.recent.get(candidate.id) or candidate
                self.remember(record)
                result.append(record)
        return result

    def latest_control(self, session_id):
        with self.db() as db:
            row = db.execute("SELECT r.id FROM runs r WHERE " + self.ROOT + " AND json_extract(r.data,'$.session_id')=? AND json_extract(r.data,'$.kind')='context_compact' ORDER BY r.rowid DESC LIMIT 1", (session_id,)).fetchone()
        return self[row[0]] if row else None
