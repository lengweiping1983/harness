"""主运行与委派运行的唯一当前落盘格式；调度器只管理自己的运行。"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .runs import ACTIVE_STATES, TERMINAL_STATES, AgentRun
from .sqlite import prepare_database
from ..i18n import tr

SCHEMA = """
PRAGMA user_version = 3;
CREATE TABLE runs(id TEXT PRIMARY KEY, data TEXT NOT NULL, summary TEXT);
CREATE TABLE run_keys(session_id TEXT,key TEXT,fingerprint TEXT,run_id TEXT,PRIMARY KEY(session_id,key));
CREATE TABLE queue_orders(session_id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE queue_controls(session_id TEXT PRIMARY KEY, paused INTEGER NOT NULL, reason TEXT NOT NULL, updated_at REAL NOT NULL);
CREATE INDEX runs_session_history ON runs(json_extract(data,'$.session_id'),json_extract(data,'$.created_at') DESC,id DESC);
CREATE UNIQUE INDEX runs_followup_key ON runs(json_extract(data,'$.prompt_input.followup.key')) WHERE json_extract(data,'$.prompt_input.followup.key') IS NOT NULL;
CREATE INDEX runs_status ON runs(json_extract(data,'$.status'));
CREATE INDEX runs_owner_created ON runs(json_extract(data,'$.parent_session_id'),json_extract(data,'$.created_at') DESC,id DESC);
CREATE INDEX runs_root_created ON runs(json_extract(data,'$.root_session_id'),json_extract(data,'$.created_at') DESC,id DESC);
"""


def _unique_fields(pairs):
    """运行正文与有界摘要都拒绝重复字段，不猜测哪个值有效。"""
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('run/duplicate-field')
        value[key] = item
    return value


class RunStorage:
    """一份运行正文、一套事务写入；摘要仅供成员列表有界读取。"""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            prepare_database(db, SCHEMA)

    @contextmanager
    def db(self):
        conn = sqlite3.connect(self.path, timeout=10)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def _decode_record(raw: str, expected_id: str, key: str) -> dict:
        """正文和摘要必须具有实际主键和明确的当前状态。"""
        data = json.loads(raw, object_pairs_hook=_unique_fields) if isinstance(raw, str) else None
        if (not isinstance(data, dict) or not isinstance(expected_id, str)
                or not expected_id or data.get(key) != expected_id):
            raise ValueError('run/identity-mismatch')
        status = data.get('status')
        if not isinstance(status, str) or status not in ACTIVE_STATES | TERMINAL_STATES:
            raise ValueError('run/invalid-status')
        return data

    @staticmethod
    def decode_summary(raw: str, *, expected_id: str) -> dict:
        """校验有界摘要的归属，不为列表加载完整运行正文。"""
        return RunStorage._decode_record(raw, expected_id, 'agent_id')

    @staticmethod
    def decode(raw: str, *, expected_id: str) -> AgentRun:
        """只解析当前字段；身份不符或非法恢复声明明确报错。"""
        data = RunStorage._decode_record(raw, expected_id, 'id')
        requester = data.get('requester')
        if requester is not None:
            from .identity import validate_subject
            if (not isinstance(requester, dict) or set(requester) != {'subject', 'display_name'}
                    or not isinstance(requester['display_name'], str) or not 1 <= len(requester['display_name']) <= 128
                    or type(data.get('access_epoch')) is not int or data['access_epoch'] < 0):
                raise ValueError('run/invalid-requester')
            validate_subject(requester['subject'])
        elif data.get('access_epoch') is not None:
            raise ValueError('run/invalid-requester')
        recovery = data.get('recovery')
        if recovery is not None and (not isinstance(recovery, dict) or recovery.get('mode') not in ('inspect', 'continue')):
            raise ValueError(tr('python.runtime.8610d27e44a0'))
        return AgentRun(**data)

    @staticmethod
    def write(db, record: AgentRun, *, initial=False, summary=None):
        """调用方可将幂等键与运行在同一事务提交，不另存权威元数据。"""
        statement = 'INSERT INTO runs VALUES(?,?,?)'
        if not initial:
            statement += ' ON CONFLICT(id) DO UPDATE SET data=excluded.data,summary=excluded.summary'
        db.execute(statement, (record.id, json.dumps(record.storage_dict(), ensure_ascii=False),
                              json.dumps(summary, ensure_ascii=False) if summary is not None else None))
