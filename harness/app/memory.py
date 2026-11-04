"""项目携带的长期记忆：事务写入、显式来源与可移植导入导出。"""
from __future__ import annotations

from ..core.sqlite import prepare_database

import sqlite3
import hashlib
import json
import re
import subprocess
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any

from harness.i18n import tr
from ..tools.process_env import execution_env
from ..core.errors import HarnessError
from .memory_context import MemoryContext, select_context
from .memory_sources import SourceChecker

SCHEMA = """
CREATE TABLE memories (
    id TEXT PRIMARY KEY, content TEXT NOT NULL, topic TEXT NOT NULL,
    pinned INTEGER NOT NULL, source TEXT NOT NULL, evidence TEXT NOT NULL,
    created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1, UNIQUE(content, topic));
PRAGMA user_version=1;
"""

MAX_MEMORIES = 1000
FORMAT = "harness-project-memory-v1"


class ProjectMemory:
    """记忆留在项目里，同一仓库的工作树共享主工作区的数据库。"""

    context_key = "python.memory.context"
    format = FORMAT
    check_sources = True

    def __init__(self) -> None:
        self._roots: dict[tuple[str, int, int], Path] = {}

    def root(self, cwd: str | Path) -> Path:
        target = Path(cwd).resolve()
        marker = next((parent / ".git" for parent in (target, *target.parents) if (parent / ".git").exists()), None)
        if marker is None:
            return target
        if marker.is_dir():
            return marker.parent
        stamp = marker.stat()
        key = (str(marker), stamp.st_mtime_ns, stamp.st_size)
        if key in self._roots:
            return self._roots[key]
        if len(self._roots) >= 128:
            self._roots.clear()
        try:
            result = subprocess.run(["git", "-C", str(target), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                                    env=execution_env(), stdin=subprocess.DEVNULL, capture_output=True, encoding="utf-8", errors="strict", timeout=5)
            if result.returncode == 0:
                common = Path(result.stdout.strip()).resolve()
                # 普通仓库及 linked worktree 的公共 .git 均位于主工作区。
                if common.name.casefold() == ".git":
                    self._roots[key] = common.parent
                    return common.parent
                result = subprocess.run(["git", "-C", str(target), "rev-parse", "--show-toplevel"],
                                        env=execution_env(), stdin=subprocess.DEVNULL, capture_output=True, encoding="utf-8", errors="strict", timeout=5)
                if result.returncode == 0:
                    root = Path(result.stdout.strip()).resolve()
                    self._roots[key] = root
                    return root
        except (OSError, subprocess.TimeoutExpired):
            pass
        return target

    def path(self, cwd: str | Path) -> Path:
        root = self.root(cwd)
        path = root / ".harness" / "memory.sqlite3"
        # samefile 比字符串前缀可靠，Windows 创建目录期间可能返回不同的 8.3 别名。
        if not path.resolve().parent.parent.samefile(root):
            raise HarnessError(tr("python.memory.unsafe"), code="memory/unsafe-path")
        return path

    def _connect(self, path: Path, *, write: bool = False) -> sqlite3.Connection:
        if write:
            path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(str(path) if write else path.as_uri() + "?mode=ro", uri=not write, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            prepare_database(db, SCHEMA, initialize=write)
            return db
        except Exception:
            db.close()
            raise

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["pinned"] = bool(result["pinned"])
        return result

    @staticmethod
    def _fields(content: str, topic: str, pinned: bool, evidence: str, source: str) -> tuple:
        if (not isinstance(content, str) or not content.strip() or len(content) > 4000
                or not isinstance(topic, str) or not topic.strip() or len(topic) > 100
                or not isinstance(pinned, bool) or not isinstance(evidence, str) or len(evidence) > 1000
                or not isinstance(source, str) or len(source) > 200):
            raise HarnessError(tr("python.memory.invalid"), code="memory/invalid")
        return content.strip(), topic.strip(), int(pinned), source, evidence.strip()

    def list(self, cwd: str | Path, query: str = "", *, limit: int = 1000) -> list[dict[str, Any]]:
        self._query_fields(query, limit)
        path = self.path(cwd)
        if not path.exists():
            return []
        with closing(self._connect(path)) as db:
            rows = [self._row(row) for row in db.execute("SELECT * FROM memories ORDER BY pinned DESC, updated_at DESC, id LIMIT ?", (MAX_MEMORIES,))]
        from .memory_search import rank
        return rank(rows, query)[:limit]

    @staticmethod
    def _query_fields(query: str, limit: int) -> None:
        if not isinstance(query, str) or len(query) > 500 or type(limit) is not int or not 1 <= limit <= MAX_MEMORIES:
            raise HarnessError(tr('python.memory.invalid_query'), code='memory/invalid')

    def recall(self, cwd: str | Path, query: str = '', *, limit: int = 20,
               queries: list[str] | None = None) -> list[dict[str, Any]]:
        """从同一读取快照查询和核验来源，不改变可移植存储记录。"""
        self._query_fields(query, limit)
        from .memory_consolidation import inspect_record
        from .memory_search import rank, rank_queries
        if queries is not None:
            if (query or not isinstance(queries, list) or not 1 <= len(queries) <= 4
                    or any(not isinstance(item, str) or not item.strip() or len(item) > 500 for item in queries)):
                raise HarnessError(tr('python.memory.invalid_query'), code='memory/invalid')
        rows = self.list(cwd)
        by_id = {row['id']: row for row in rows}
        checker = SourceChecker(cwd) if self.check_sources else None
        selected, matches = rank_queries(rows, queries) if queries is not None else (rank(rows, query), {})
        return [{**row, **inspect_record(row, by_id, checker),
                 **({'query_matches': matches[row['id']]} if queries is not None else {})}
                for row in selected[:limit]]

    def check(self, cwd: str | Path, references: list[dict]) -> list[dict[str, Any]]:
        """按当前同一快照核对版本和逐字摘录，不判断摘录是否蕴含任务结论。"""
        if (not isinstance(references, list) or not 1 <= len(references) <= 6
                or any(not isinstance(item, dict) or set(item) != {'id', 'revision', 'excerpt'}
                    or not isinstance(item['id'], str) or not 1 <= len(item['id']) <= 100
                    or type(item['revision']) is not int or not 1 <= item['revision'] <= 9223372036854775807
                    or not isinstance(item['excerpt'], str) or not item['excerpt'].strip()
                    or len(item['excerpt']) > 1000 for item in references)
                or len({item['id'] for item in references}) != len(references)):
            raise HarnessError(tr('python.memory.invalid_references'), code='memory/invalid')
        from .memory_consolidation import inspect_record
        rows = self.list(cwd)
        by_id = {row['id']: row for row in rows}
        checker = SourceChecker(cwd) if self.check_sources else None
        checks = []
        for item in references:
            result = {'id': item['id'], 'requested_revision': item['revision']}
            row = by_id.get(item['id'])
            if row is None:
                result['status'] = 'missing'
            elif row['revision'] != item['revision']:
                result.update(status='changed', current_revision=row['revision'])
            else:
                start = row['content'].find(item['excerpt'])
                result.update(status='excerpt_missing' if start < 0 else 'matched', current_revision=row['revision'])
                if start >= 0:
                    result.update(excerpt=item['excerpt'], start=start, end=start + len(item['excerpt']),
                                  **inspect_record(row, by_id, checker))
            checks.append(result)
        return checks

    def save(self, cwd: str | Path, content: str, *, topic: str = "project", pinned: bool = False,
             source: str = "", evidence: str = "", memory_id: str | None = None,
             expected_revision: int | None = None) -> dict[str, Any]:
        values = self._fields(content, topic, pinned, evidence, source)
        path = self.path(cwd)
        stamp = int(time.time() * 1000)
        with closing(self._connect(path, write=True)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if memory_id:
                if type(expected_revision) is not int or expected_revision < 1:
                    raise HarnessError(tr("python.memory.changed"), code="memory/changed")
                try:
                    cursor = db.execute("UPDATE memories SET content=?, topic=?, pinned=?, source=?, evidence=?, updated_at=?, revision=revision+1 WHERE id=? AND revision=?",
                                        (*values, stamp, memory_id, expected_revision))
                except sqlite3.IntegrityError as exc:
                    raise HarnessError(tr("python.memory.duplicate"), code="memory/duplicate") from exc
                if cursor.rowcount != 1:
                    raise HarnessError(tr("python.memory.changed"), code="memory/changed")
                mid = memory_id
            else:
                existing = db.execute("SELECT * FROM memories WHERE content=? AND topic=?", values[:2]).fetchone()
                if existing:
                    return self._row(existing)
                if db.execute("SELECT count(*) FROM memories").fetchone()[0] >= MAX_MEMORIES:
                    raise HarnessError(tr("python.memory.full"), code="memory/full")
                mid = "mem_" + uuid.uuid4().hex
                db.execute("INSERT INTO memories VALUES(?,?,?,?,?,?,?,?,1)", (mid, *values, stamp, stamp))
            result = self._row(db.execute("SELECT * FROM memories WHERE id=?", (mid,)).fetchone())
        return result

    def delete(self, cwd: str | Path, memory_id: str, expected_revision: int) -> None:
        if type(expected_revision) is not int or expected_revision < 1:
            raise HarnessError(tr("python.memory.invalid"), code="memory/invalid")
        path = self.path(cwd)
        if not path.exists():
            raise HarnessError(tr("python.memory.changed"), code="memory/changed")
        with closing(self._connect(path, write=True)) as db, db:
            cursor = db.execute("DELETE FROM memories WHERE id=? AND revision=?", (memory_id, expected_revision))
            if cursor.rowcount != 1:
                raise HarnessError(tr("python.memory.changed"), code="memory/changed")

    def consolidate(self, cwd: str | Path, inputs: list[dict], content: str, *, topic: str = 'project',
                    pinned: bool = True, source: str = '', evidence: str = '') -> dict[str, Any]:
        """原子创建摘要并取消来源固定；保留来源正文，以确定性身份识别重试。"""
        from .memory_consolidation import PREFIX, MAX_INPUTS, parse, lineage
        values = self._fields(content, topic, pinned, evidence, source)
        if not isinstance(inputs, list) or not 2 <= len(inputs) <= MAX_INPUTS:
            raise HarnessError(tr('python.memory.invalid'), code='memory/invalid')
        normalized = []
        for item in inputs:
            if (not isinstance(item, dict) or set(item) != {'id', 'revision'}
                    or not isinstance(item['id'], str) or not 1 <= len(item['id']) <= 100
                    or type(item['revision']) is not int or item['revision'] < 1):
                raise HarnessError(tr('python.memory.invalid'), code='memory/invalid')
            normalized.append(dict(item))
        normalized.sort(key=lambda item: item['id'])
        if len({item['id'] for item in normalized}) != len(normalized):
            raise HarnessError(tr('python.memory.invalid'), code='memory/invalid')
        request = hashlib.sha256(json.dumps([normalized, values], ensure_ascii=False,
                                            separators=(',', ':')).encode('utf-8')).hexdigest()
        mid = 'mem_' + request[:32]
        path = self.path(cwd)
        if not path.exists():
            raise HarnessError(tr('python.memory.changed'), code='memory/changed')
        stamp = int(time.time() * 1000)
        with closing(self._connect(path, write=True)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            def receipt(row, references, replayed):
                current = {item['id']: self._row(item) for item in db.execute('SELECT * FROM memories LIMIT ?', (MAX_MEMORIES,))}
                state, _ = lineage(row, current)
                return {'memory': row, 'inputs': references, 'replayed': replayed,
                        'derivation': {'status': state, 'requires_reverification': state != 'current'}}
            previous = db.execute('SELECT * FROM memories WHERE id=?', (mid,)).fetchone()
            if previous is not None:
                try:
                    saved_receipt = parse(previous['evidence'])
                except ValueError:
                    saved_receipt = None
                if (saved_receipt is None or saved_receipt['request'] != request or previous['revision'] != 1
                        or tuple(previous[key] for key in ('content', 'topic', 'pinned', 'source')) != values[:4]):
                    raise HarnessError(tr('python.memory.changed'), code='memory/changed')
                return receipt(self._row(previous), saved_receipt['inputs'], True)
            if db.execute('SELECT 1 FROM memories WHERE content=? AND topic=?', values[:2]).fetchone():
                raise HarnessError(tr('python.memory.duplicate'), code='memory/duplicate')
            if db.execute('SELECT count(*) FROM memories').fetchone()[0] >= MAX_MEMORIES:
                raise HarnessError(tr('python.memory.full'), code='memory/full')
            references = []
            for item in normalized:
                row = db.execute('SELECT * FROM memories WHERE id=?', (item['id'],)).fetchone()
                if row is None or row['revision'] != item['revision']:
                    raise HarnessError(tr('python.memory.changed'), code='memory/changed')
                references.append({'id': row['id'], 'input_revision': row['revision'],
                                   'revision': row['revision'] + int(bool(row['pinned'])), 'pinned': bool(row['pinned'])})
            encoded = PREFIX + json.dumps({'request': request, 'note': values[4], 'inputs': references},
                                           ensure_ascii=False, separators=(',', ':'))
            if len(encoded) > 1000:
                raise HarnessError(tr('python.memory.consolidation_full'), code='memory/consolidation-too-large')
            final = self._fields(values[0], values[1], pinned, encoded, source)
            for item in references:
                if item['pinned']:
                    db.execute('UPDATE memories SET pinned=0, updated_at=?, revision=revision+1 WHERE id=? AND revision=?',
                               (stamp, item['id'], item['input_revision']))
            db.execute('INSERT INTO memories VALUES(?,?,?,?,?,?,?,?,1)', (mid, *final, stamp, stamp))
            result = self._row(db.execute('SELECT * FROM memories WHERE id=?', (mid,)).fetchone())
            reply = receipt(result, references, False)
        return reply

    def export(self, cwd: str | Path) -> dict[str, Any]:
        return {"format": self.format, "memories": self.list(cwd)}

    def import_data(self, cwd: str | Path, data: Any) -> dict[str, int]:
        if not isinstance(data, dict) or data.get("format") != self.format or not isinstance(data.get("memories"), list) or len(data["memories"]) > MAX_MEMORIES:
            raise HarnessError(tr("python.memory.format"), code="memory/format")
        values = []
        identities = []
        imported_rows = {}
        for row in data["memories"]:
            if not isinstance(row, dict):
                raise HarnessError(tr("python.memory.invalid"), code="memory/invalid")
            values.append(self._fields(row.get("content"), row.get("topic", "project"), row.get("pinned", False), row.get("evidence", ""), row.get("source", "import")))
            mid, revision = row.get('id'), row.get('revision', 1)
            if ((mid is not None and (not isinstance(mid, str) or not re.fullmatch('mem_[0-9a-f]{32}', mid) or mid in imported_rows))
                    or type(revision) is not int or not 1 <= revision <= 2**63 - 1):
                raise HarnessError(tr('python.memory.invalid'), code='memory/invalid')
            identities.append((mid or 'mem_' + uuid.uuid4().hex, revision))
            if mid is not None:
                imported_rows[mid] = {**row, 'revision': revision, 'evidence': values[-1][4]}
        if not values:
            return {"imported": 0, "duplicates": 0}
        stamp = int(time.time() * 1000)
        imported = 0
        with closing(self._connect(self.path(cwd), write=True)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            count = db.execute("SELECT count(*) FROM memories").fetchone()[0]
            if count > MAX_MEMORIES:
                raise HarnessError(tr('python.memory.full'), code='memory/full')
            for value, (mid, revision) in zip(values, identities):
                same_id = db.execute('SELECT content,topic FROM memories WHERE id=?', (mid,)).fetchone()
                if same_id is not None and tuple(same_id) != value[:2]:
                    raise HarnessError(tr('python.memory.changed'), code='memory/changed')
                if db.execute("SELECT 1 FROM memories WHERE content=? AND topic=?", value[:2]).fetchone():
                    continue
                if count + imported >= MAX_MEMORIES:
                    raise HarnessError(tr("python.memory.full"), code="memory/full")
                db.execute("INSERT INTO memories VALUES(?,?,?,?,?,?,?,?,?)", (mid, *value, stamp, stamp, revision))
                imported += 1
            # 原本闭合的整理引用必须仍闭合；不能把内容重复误当成相同条目身份。
            from .memory_consolidation import lineage
            current = {row['id']: self._row(row) for row in db.execute('SELECT * FROM memories')}
            for mid, row in imported_rows.items():
                if (lineage(row, imported_rows)[0] == 'current'
                        and (mid not in current or lineage(current[mid], current)[0] != 'current')):
                    raise HarnessError(tr('python.memory.changed'), code='memory/changed')
        return {"imported": imported, "duplicates": len(values) - imported}

    def context(self, cwd: str | Path, *, budget: int = 6000, query: str = "") -> str:
        """固定记忆优先，再补充当前任务相关资料，均作为有来源的数据。"""
        return self.preview(cwd, budget=budget, query=query).text

    def preview(self, cwd: str | Path, *, budget: int = 6000, query: str = "") -> MemoryContext:
        """按同一读取快照生成注入和解释，不缓存其他会话的选择。"""
        if type(budget) is not int or budget < 0:
            raise HarnessError(tr("python.memory.invalid"), code="memory/invalid")
        if not cwd:
            return MemoryContext('', budget, 0)
        return select_context(self.list(cwd), context_key=self.context_key, query=query, budget=budget,
                              checker=SourceChecker(cwd) if self.check_sources else None)
