"""有界输出快照、按归属分页的元数据，不重放进程。"""
from __future__ import annotations

from ..core.sqlite import prepare_database

from harness.i18n import tr

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

OUTPUT_CHARS = 32768
FINAL = frozenset({'completed', 'failed', 'killed'})


def completion_view(view: dict[str, Any]) -> dict[str, Any]:
    """等待输入适配器的 returncode 不能作为命令退出码。"""
    if view.get('kind') == 'pty-send' or view.get('completionScope') == 'terminal-wait':
        view['completionScope'] = 'terminal-wait'
        view.pop('exitCode', None)
        if view.get('status') == 'completed' and (not view.get('detail') or str(view['detail']).startswith('exit code:')):
            view['detail'] = tr('python.job_archive.df8d7eb2cb4b')
    return view


def archived_view(metadata: str, expected_id: str) -> dict[str, Any]:
    view = json.loads(metadata)
    if not isinstance(view, dict) or view.get('id') != expected_id or any(not isinstance(view.get(key), str) for key in ('kind', 'label', 'status')) or type(view.get('startedAt')) is not int:
        raise RuntimeError(tr('python.job_archive.e264e4ca4032'))
    view['archived'] = True
    if view['status'] not in FINAL:
        view['lastStatus'] = view['status']
        view['status'] = 'unavailable'
        view['recovered'] = True
        view.pop('finishedAt', None)
        view['detail'] = tr('python.job_archive.379172d103f8')
    return completion_view(view)


SCHEMA = """
CREATE TABLE jobs (
    id TEXT PRIMARY KEY, owner TEXT NOT NULL, created INTEGER NOT NULL,
    metadata TEXT NOT NULL, output TEXT NOT NULL);
CREATE INDEX jobs_owner_created ON jobs(owner, created DESC, id DESC);
"""


class JobArchive:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            prepare_database(db, SCHEMA)

    @contextmanager
    def db(self):
        conn = sqlite3.connect(self.path, timeout=2)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def save(self, owner: str, output: dict[str, Any]) -> None:
        view = dict(output['job'])
        view.pop('archiveError', None)
        for key, cap in (('label', 4000), ('detail', 4000), ('cleanupError', 2000)):
            if isinstance(view.get(key), str):
                view[key] = view[key][:cap]
        body = {key: value for key, value in output.items() if key != 'job'}
        for channel in ('stdout', 'stderr'):
            text = body[channel]
            body[channel] = text[-OUTPUT_CHARS:]
            body[channel + '_offset'] = body[channel + '_end'] - len(body[channel])
            body['truncated'] |= len(text) > OUTPUT_CHARS
        # 调用方在所属事件循环中获取快照，线程不检查实时输出尾部。
        with self.db() as db:
            result = db.execute('''INSERT INTO jobs VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE
                SET metadata=excluded.metadata, output=excluded.output WHERE jobs.owner=excluded.owner''',
                (view['id'], owner, view['startedAt'], json.dumps(view, ensure_ascii=False),
                 json.dumps(body, ensure_ascii=False)))
            if result.rowcount != 1:
                raise ValueError(tr('python.job_archive.475286e79787'))

    def output(self, owner: str, job_id: str, limit: int = OUTPUT_CHARS) -> dict[str, Any]:
        with self.db() as db:
            row = db.execute('SELECT metadata, output FROM jobs WHERE id=? AND owner=?', (job_id, owner)).fetchone()
        if row is None:
            raise LookupError(job_id)
        body = json.loads(row['output'])
        if not isinstance(body, dict) or type(body.get('truncated')) is not bool:
            raise RuntimeError(tr('python.job_archive.e70139247bbd'))
        result = {**body, 'job': archived_view(row['metadata'], job_id)}
        for channel in ('stdout', 'stderr'):
            text = result.get(channel)
            offset, end = result.get(channel + '_offset'), result.get(channel + '_end')
            if not isinstance(text, str) or type(offset) is not int or type(end) is not int or offset < 0 or offset + len(text) != end:
                raise RuntimeError(tr('python.job_archive.94b1494644cb'))
            result[channel] = text[-limit:]
            result[channel + '_offset'] = result[channel + '_end'] - len(result[channel])
            result['truncated'] |= len(text) > limit
        return result

    def page(self, owner: str | None, before: str | None = None, limit: int = 50) -> dict[str, Any]:
        where, args = [], []
        if owner is not None:
            where.append('owner=?')
            args.append(owner)
        with self.db() as db:
            if before:
                anchor = db.execute('SELECT owner, created FROM jobs WHERE id=?', (before,)).fetchone()
                if anchor is None or owner is not None and anchor['owner'] != owner:
                    raise LookupError(before)
                where.append('(created < ? OR (created = ? AND id < ?))')
                args.extend((anchor['created'], anchor['created'], before))
            query = 'SELECT id, metadata FROM jobs'
            if where:
                query += ' WHERE ' + ' AND '.join(where)
            rows = db.execute(query + ' ORDER BY created DESC, id DESC LIMIT ?', (*args, limit + 5)).fetchall()
        more = len(rows) >= limit + 5
        rows = rows[:limit] if more else rows
        return {'jobs': [archived_view(row['metadata'], row['id']) for row in rows], 'has_more': more,
                'next_before': rows[-1]['id'] if more else None}
