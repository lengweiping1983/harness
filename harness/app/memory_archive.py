"""显式归档与恢复：独立当前格式、跨库事务，不迁移已有记忆库。"""
from __future__ import annotations

import json
import hashlib
import sqlite3
import time
import tempfile
from pathlib import Path
from contextlib import closing, contextmanager

from harness.i18n import tr
from ..core.errors import HarnessError
from ..core.sqlite import prepare_database

SCHEMA = '''
CREATE TABLE archives (
    seq INTEGER PRIMARY KEY, id TEXT NOT NULL, revision INTEGER NOT NULL,
    record TEXT NOT NULL, archived_at INTEGER NOT NULL, UNIQUE(id, revision));
PRAGMA user_version=1;
'''
FIELDS = {'id', 'revision', 'content', 'topic', 'pinned', 'source', 'evidence', 'created_at', 'updated_at'}


class MemoryArchive:
    """原文持久保留；只允许显式请求移动，恢复不覆盖当前资料。"""

    def __init__(self, memory):
        self.memory = memory

    def path(self, cwd):
        path = self.memory.path(cwd).with_suffix('.archive.sqlite3')
        if path.resolve() != path.absolute():
            raise HarnessError(tr('python.memory.unsafe'), code='memory/unsafe-path')
        return path

    @staticmethod
    def _references(inputs):
        if (not isinstance(inputs, list) or not 1 <= len(inputs) <= 6
                or any(not isinstance(item, dict) or set(item) != {'id', 'revision'}
                    or not isinstance(item['id'], str) or not 1 <= len(item['id']) <= 100
                    or type(item['revision']) is not int or not 1 <= item['revision'] < 9223372036854775807
                    for item in inputs) or len({item['id'] for item in inputs}) != len(inputs)):
            raise HarnessError(tr('python.memory.invalid'), code='memory/invalid')

    def _decode(self, row):
        try:
            # 合法字段中的控制字符经JSON转义可膨胀六倍，不能误判为损坏。
            if len(row['record']) > 65536:
                raise ValueError
            value = json.loads(row['record'])
            if (not isinstance(value, dict) or set(value) != FIELDS or value['id'] != row['id']
                    or type(row['seq']) is not int or not 1 <= row['seq'] <= 9223372036854775807
                    or type(row['archived_at']) is not int or not 0 <= row['archived_at'] <= 9223372036854775807
                    or type(value['revision']) is not int or value['revision'] != row['revision']
                    or not isinstance(value['id'], str) or not 1 <= len(value['id']) <= 100
                    or not 1 <= value['revision'] < 9223372036854775807
                    or any(type(value[key]) is not int or not 0 <= value[key] <= 9223372036854775807
                           for key in ('created_at', 'updated_at'))):
                raise ValueError
            self.memory._fields(value['content'], value['topic'], value['pinned'], value['evidence'], value['source'])
        except (ValueError, TypeError, KeyError, HarnessError):
            raise HarnessError(tr('python.memory.archive_invalid'), code='memory/archive-invalid') from None
        return value

    def _connect(self, path, *, write=False):
        if write:
            path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(str(path) if write else path.as_uri() + '?mode=ro', uri=not write, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            prepare_database(db, SCHEMA, initialize=write)
            return db
        except BaseException:
            db.close()
            raise

    @staticmethod
    def _journal(db, schema):
        if db.execute(f'PRAGMA {schema}.journal_mode').fetchone()[0].lower() not in {'delete', 'truncate', 'persist'}:
            raise HarnessError(tr('python.memory.archive_journal'), code='memory/archive-journal')
        # 同步级别属于当前连接，不隐式切换用户库的持久日志模式。
        db.execute(f'PRAGMA {schema}.synchronous=FULL')

    @contextmanager
    def _transaction(self, cwd):
        path = self.path(cwd)
        with closing(self.memory._connect(self.memory.path(cwd), write=True)) as db:
            self._journal(db, 'main')
            with closing(self._connect(path, write=True)):
                pass
            db.execute('ATTACH DATABASE ? AS archive', (str(path),))
            self._journal(db, 'archive')
            with db:
                db.execute('BEGIN IMMEDIATE')
                prepare_database(db, SCHEMA, initialize=False, schema='archive')
                yield db

    def list(self, cwd, *, before: int | None = None, limit: int = 20, memory_id: str | None = None):
        if (type(limit) is not int or not 1 <= limit <= 100
                or before is not None and (type(before) is not int or not 1 <= before <= 9223372036854775807)):
            raise HarnessError(tr('python.memory.invalid_query'), code='memory/invalid')
        if memory_id is not None and (not isinstance(memory_id, str) or not 1 <= len(memory_id) <= 100):
            raise HarnessError(tr('python.memory.invalid_query'), code='memory/invalid')
        path = self.path(cwd)
        if not path.exists():
            return {'archives': [], 'next_before': None}
        with closing(self._connect(path)) as db:
            # 按完整身份查历史版本，避免把正文提到的其他记忆误当成该身份。
            condition = ' WHERE (? IS NULL OR seq < ?)'
            parameters = [before, before]
            if memory_id is not None:
                condition += ' AND id=?'
                parameters.append(memory_id)
            rows = db.execute('SELECT * FROM archives' + condition + ' ORDER BY seq DESC LIMIT ?',
                              (*parameters, limit + 1)).fetchall()
            return {'archives': [{'seq': row['seq'], 'archived_at': row['archived_at'], 'memory': self._decode(row)}
                                 for row in rows[:limit]], 'next_before': rows[limit - 1]['seq'] if len(rows) > limit else None}

    def search(self, cwd, query: str, *, before: int | None = None):
        """每次最多检索一百条历史，空命中仍返回可继续的扫描游标。"""
        if not isinstance(query, str) or not query.strip() or len(query) > 500:
            raise HarnessError(tr('python.memory.invalid_query'), code='memory/invalid')
        from .memory_search import rank
        page = self.list(cwd, before=before, limit=100)
        records = page['archives']
        by_version = {(item['memory']['id'], item['memory']['revision']): item for item in records}
        matches = rank([item['memory'] for item in records], query)
        return {'archives': [by_version[(row['id'], row['revision'])] for row in matches],
                'next_before': page['next_before'], 'scanned': len(records),
                'meaning': 'historical-keyword-candidates-only', 'ranking_scope': 'current-page'}

    def export_jsonl(self, cwd, stream):
        """向调用者提供的文本流导出单一快照；尾记录证明完整读取，异常不吞掉。"""
        digest = hashlib.sha256()
        count = 0

        def write(line):
            if stream.write(line) != len(line):
                raise OSError(tr('runtime.message.5e104e1d484c'))

        def emit(value):
            line = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n'
            write(line)
            digest.update(line.encode('utf-8'))

        emit({'type': 'header', 'format': self.memory.format + '-archive-jsonl-v1'})
        path = self.path(cwd)
        if path.exists():
            with closing(self._connect(path)) as db:
                # 固定读事务；流输出期间追加的归档不混入本次备份。
                db.execute('BEGIN')
                for row in db.execute('SELECT * FROM archives ORDER BY seq'):
                    emit({'type': 'archive', 'seq': row['seq'], 'archived_at': row['archived_at'],
                          'memory': self._decode(row)})
                    count += 1
        checksum = digest.hexdigest()
        write(json.dumps({'type': 'footer', 'count': count, 'sha256': checksum},
                         sort_keys=True, separators=(',', ':')) + '\n')
        return {'count': count, 'sha256': checksum}

    def verify_jsonl(self, stream):
        """有界读取校验备份帧、作用域、原文与完整性；不导入或恢复任何数据。"""
        return self._read_jsonl(stream)

    def _read_jsonl(self, stream, consume=None):
        """共享逐帧验证；暂存回调不能在校验结束前写入目标库。"""
        def unique_fields(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError
                value[key] = item
            return value

        digest = hashlib.sha256()
        count, previous = 0, 0
        header = False
        try:
            while True:
                line = stream.readline(65536)
                if not line or not line.endswith('\n'):
                    raise ValueError
                value = json.loads(line, object_pairs_hook=unique_fields)
                if not header:
                    if value != {'type': 'header', 'format': self.memory.format + '-archive-jsonl-v1'}:
                        raise ValueError
                    header = True
                elif isinstance(value, dict) and value.get('type') == 'footer':
                    if (set(value) != {'type', 'count', 'sha256'} or type(value['count']) is not int
                            or value['count'] != count or value['sha256'] != digest.hexdigest() or stream.read(1)):
                        raise ValueError
                    return {'count': count, 'sha256': digest.hexdigest()}
                else:
                    if not isinstance(value, dict) or set(value) != {'type', 'seq', 'archived_at', 'memory'} or value['type'] != 'archive':
                        raise ValueError
                    row = {**value, 'id': value['memory']['id'], 'revision': value['memory']['revision'],
                           'record': json.dumps(value['memory'], ensure_ascii=False)}
                    self._decode(row)
                    if row['seq'] <= previous:
                        raise ValueError
                    previous = row['seq']
                    count += 1
                    if consume is not None:
                        consume(row)
                digest.update(line.encode('utf-8'))
        except (ValueError, TypeError, KeyError):
            raise HarnessError(tr('python.memory.archive_invalid'), code='memory/archive-invalid') from None

    def import_jsonl(self, cwd, stream):
        """完整暂存后事务合并历史；不覆盖冲突、不写活动记忆，也不隐式恢复。"""
        with tempfile.TemporaryDirectory(prefix='harness-archive-import-') as directory:
            with closing(self._connect(Path(directory) / 'staging.sqlite3', write=True)) as staging:
                def consume(row):
                    try:
                        staging.execute('INSERT INTO archives(seq,id,revision,record,archived_at) VALUES(?,?,?,?,?)',
                            tuple(row[key] for key in ('seq', 'id', 'revision', 'record', 'archived_at')))
                    except sqlite3.IntegrityError:
                        raise HarnessError(tr('python.memory.archive_invalid'), code='memory/archive-invalid') from None
                with staging:
                    receipt = self._read_jsonl(stream, consume)
                if not receipt['count']:
                    return {**receipt, 'imported': 0, 'replayed': 0}
                imported, replayed = 0, 0
                with closing(self._connect(self.path(cwd), write=True)) as db:
                    self._journal(db, 'main')
                    with db:
                        db.execute('BEGIN IMMEDIATE')
                        for row in staging.execute('SELECT * FROM archives ORDER BY seq'):
                            old = db.execute('SELECT * FROM archives WHERE id=? AND revision=?',
                                             (row['id'], row['revision'])).fetchone()
                            if old is not None:
                                if self._decode(old) != self._decode(row) or old['archived_at'] != row['archived_at']:
                                    raise HarnessError(tr('python.memory.changed'), code='memory/changed')
                                replayed += 1
                            else:
                                # seq是目标库本地游标；原文身份、版本与归档时间保持不变。
                                db.execute('INSERT INTO archives(id,revision,record,archived_at) VALUES(?,?,?,?)',
                                    tuple(row[key] for key in ('id', 'revision', 'record', 'archived_at')))
                                imported += 1
                return {**receipt, 'imported': imported, 'replayed': replayed}

    def archive(self, cwd, inputs):
        self._references(inputs)
        if not self.memory.path(cwd).exists():
            raise HarnessError(tr('python.memory.changed'), code='memory/changed')
        from .memory_consolidation import parse
        with self._transaction(cwd) as db:
            active = {row['id']: self.memory._row(row) for row in db.execute('SELECT * FROM memories')}
            ids = {item['id'] for item in inputs}
            for row in active.values():
                if row['id'] in ids:
                    continue
                try:
                    derived = parse(row['evidence'])
                except ValueError:
                    # 损坏的派生关系不能证明移动来源安全，保留全部原文。
                    raise HarnessError(tr('python.memory.archive_referenced'), code='memory/archive-referenced') from None
                if derived is not None and any(item['id'] in ids for item in derived['inputs']):
                    raise HarnessError(tr('python.memory.archive_referenced'), code='memory/archive-referenced')
            results = []
            for item in inputs:
                old = db.execute('SELECT * FROM archive.archives WHERE id=? AND revision=?',
                                 (item['id'], item['revision'])).fetchone()
                current = active.get(item['id'])
                if current is None:
                    if old is None:
                        raise HarnessError(tr('python.memory.changed'), code='memory/changed')
                    self._decode(old)
                    results.append({'id': item['id'], 'revision': item['revision'], 'replayed': True})
                    continue
                if current['revision'] != item['revision']:
                    raise HarnessError(tr('python.memory.changed'), code='memory/changed')
                if old is not None and self._decode(old) != current:
                    raise HarnessError(tr('python.memory.changed'), code='memory/changed')
                if old is None:
                    db.execute('INSERT INTO archive.archives(id,revision,record,archived_at) VALUES(?,?,?,?)',
                        (item['id'], item['revision'], json.dumps(current, ensure_ascii=False, sort_keys=True), int(time.time() * 1000)))
                db.execute('DELETE FROM memories WHERE id=? AND revision=?', (item['id'], item['revision']))
                results.append({'id': item['id'], 'revision': item['revision'], 'replayed': False})
        return {'archived': results}

    def restore(self, cwd, memory_id: str, revision: int):
        self._references([{'id': memory_id, 'revision': revision}])
        if not self.path(cwd).exists():
            raise HarnessError(tr('python.memory.changed'), code='memory/changed')
        from .memory import MAX_MEMORIES
        with self._transaction(cwd) as db:
            archived = db.execute('SELECT * FROM archive.archives WHERE id=? AND revision=?', (memory_id, revision)).fetchone()
            latest = db.execute('SELECT max(revision) FROM archive.archives WHERE id=?', (memory_id,)).fetchone()[0]
            if archived is None or latest != revision:
                raise HarnessError(tr('python.memory.changed'), code='memory/changed')
            value = self._decode(archived)
            current = db.execute('SELECT * FROM memories WHERE id=?', (memory_id,)).fetchone()
            if current is not None:
                current = self.memory._row(current)
                if (current['revision'] != revision + 1
                        or any(current[key] != value[key] for key in FIELDS - {'revision', 'updated_at'})):
                    raise HarnessError(tr('python.memory.changed'), code='memory/changed')
                return {'memory': current, 'replayed': True}
            if db.execute('SELECT 1 FROM memories WHERE content=? AND topic=?', (value['content'], value['topic'])).fetchone():
                raise HarnessError(tr('python.memory.duplicate'), code='memory/duplicate')
            if db.execute('SELECT count(*) FROM memories').fetchone()[0] >= MAX_MEMORIES:
                raise HarnessError(tr('python.memory.full'), code='memory/full')
            value.update(revision=revision + 1, updated_at=int(time.time() * 1000))
            db.execute('INSERT INTO memories(id,content,topic,pinned,source,evidence,created_at,updated_at,revision) VALUES(?,?,?,?,?,?,?,?,?)',
                       tuple(value[key] for key in ('id', 'content', 'topic', 'pinned', 'source', 'evidence', 'created_at', 'updated_at', 'revision')))
        return {'memory': value, 'replayed': False}
