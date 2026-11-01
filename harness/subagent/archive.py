"""持久化结果快照，元数据分页不读取完整结果正文。"""
from __future__ import annotations

from ..core.run_storage import RunStorage
from ..core.runs import AgentRun

from harness.i18n import tr

import time
from pathlib import Path
from typing import Any


def subagent_summary(state: Any) -> dict[str, Any]:
    row = state.to_dict()
    row['source_call_id'] = state.source_call_id
    row['result_truncated'] = len(row['result']) > 2000
    row['result'] = row['result'][:2000]
    if row['error'] is not None:
        row['error'] = row['error'][:2000]
    if row.get('error_parts'):
        # 资源键保持身份，列表中的外部诊断和参数共用有界预览预算。
        remaining = 2000
        row['error_parts'] = row['error_parts'][:20]
        for part in row['error_parts']:
            part['label'] = part['label'][:256]
            if isinstance(part['message'], str):
                part['message'] = part['message'][:remaining]
                remaining -= len(part['message'])
            else:
                params = part['message'].get('params', {})
                for key, value in params.items():
                    if isinstance(value, str):
                        params[key] = value[:remaining]
                        remaining -= len(params[key])
    return row


class SubagentArchive:
    def __init__(self, storage: RunStorage | Path):
        self.storage = storage if isinstance(storage, RunStorage) else RunStorage(storage)
        self.path = self.storage.path
        self.db = self.storage.db
        with self.db() as db:
            # 只恢复委派运行；主队列的暂停、引导和恢复规则由队列控制器管理。
            for run_id, raw, summary in db.execute("SELECT id,data,summary FROM runs WHERE json_extract(data,'$.parent_session_id') IS NOT NULL AND json_extract(data,'$.status') IN ('pending','queued','running','stopping')").fetchall():
                record = self.storage.decode(raw, expected_id=run_id)
                record.status, record.recovered, record.finished_at = 'interrupted', True, time.time()
                record.error = {'code': 'run/restarted', 'message': tr('ui.agent.restarted'), 'copy': {'key': 'ui.agent.restarted'}}
                record.archived_pending_messages = self.storage.decode_summary(summary, expected_id=run_id)['pending_messages']
                row = subagent_summary(record)
                if record.scope is not None:
                    row['scope'] = record.scope
                self.storage.write(db, record, summary=row)

    def save(self, state: AgentRun):
        """唯一运行记录落盘；有界列表投影单独索引，避免分页读取完整正文。"""
        summary = subagent_summary(state)
        if state.scope is not None:
            summary['scope'] = state.scope
        with self.db() as db:
            self.storage.write(db, state, summary=summary)

    def load(self, agent_id: str) -> AgentRun | None:
        """恢复同一种运行对象，不解析历史字段或补造另一份子运行状态。"""
        with self.db() as db:
            row = db.execute("SELECT data, summary FROM runs WHERE id=? AND json_extract(data,'$.parent_session_id') IS NOT NULL", (agent_id,)).fetchone()
        if row is None:
            return None
        record = self.storage.decode(row[0], expected_id=agent_id)
        record.archived = True
        record.archived_pending_messages = self.storage.decode_summary(row[1], expected_id=agent_id)['pending_messages']
        return record

    def member_attempts(self, scope: str, member: str) -> list[dict[str, Any]]:
        """只读取恢复判定需要的字段，不载入模型正文或改变运行状态。"""
        with self.db() as db:
            rows = db.execute("SELECT id,json_extract(data,'$.created_at'),json_extract(data,'$.status') "
                "FROM runs WHERE json_extract(data,'$.parent_session_id') IS NOT NULL "
                "AND json_extract(data,'$.scope')=? AND json_extract(data,'$.prompt_input.member')=?",
                (scope, member)).fetchall()
        return [{'id': row[0], 'created_at': row[1], 'status': row[2]} for row in rows]

    def latest_for_context(self, session_id: str) -> str | None:
        """历史地址续接先定位该上下文的最新委派，不能沿用已失效的权限快照。"""
        with self.db() as db:
            row = db.execute("SELECT id FROM runs WHERE json_extract(data,'$.session_id')=? AND json_extract(data,'$.parent_session_id') IS NOT NULL ORDER BY json_extract(data,'$.created_at') DESC,id DESC LIMIT 1", (session_id,)).fetchone()
        return row[0] if row else None

    def followup(self, key: str) -> str | None:
        """按持久幂等键定位续接运行，不重放任务或扫描完整历史。"""
        with self.db() as db:
            row = db.execute("SELECT id FROM runs WHERE json_extract(data,'$.prompt_input.followup.key')=?", (key,)).fetchone()
        return row[0] if row else None

    def get(self, agent_id: str) -> dict[str, Any] | None:
        state = self.load(agent_id)
        return {**state.to_dict(), 'prompt': state.text, 'scope': state.scope,
                'source_call_id': state.source_call_id} if state is not None else None

    def summary(self, agent_id: str) -> dict[str, Any] | None:
        with self.db() as db:
            row = db.execute("SELECT summary FROM runs WHERE id=? AND json_extract(data,'$.parent_session_id') IS NOT NULL", (agent_id,)).fetchone()
        return {**self.storage.decode_summary(row[0], expected_id=agent_id), 'archived': True} if row else None

    def delete_tree(self, owner: str) -> None:
        with self.db() as db:
            db.execute("DELETE FROM runs WHERE json_extract(data,'$.parent_session_id') IS NOT NULL AND (json_extract(data,'$.parent_session_id')=? OR json_extract(data, '$.root_session_id')=?)", (owner, owner))

    def page(self, owner: str | None, *, before: str | None = None, limit: int = 50, include_descendants: bool = False) -> dict[str, Any]:
        where, args = ["json_extract(data,'$.parent_session_id') IS NOT NULL"], []
        if owner is not None:
            where.append("(json_extract(data,'$.parent_session_id')=? OR json_extract(data, '$.root_session_id')=?)" if include_descendants else "json_extract(data,'$.parent_session_id')=?")
            args.append(owner)
            if include_descendants:
                args.append(owner)
        with self.db() as db:
            if before:
                anchor_query = "SELECT json_extract(data,'$.created_at') FROM runs WHERE id=?"
                if where:
                    anchor_query += ' AND ' + ' AND '.join(where)
                anchor = db.execute(anchor_query, (before, *args)).fetchone()
                if anchor is None:
                    raise LookupError(before)
                where.append("(json_extract(data,'$.created_at') < ? OR (json_extract(data,'$.created_at') = ? AND id < ?))")
                args.extend((anchor[0], anchor[0], before))
            query = 'SELECT id, summary FROM runs'
            if where:
                query += ' WHERE ' + ' AND '.join(where)
            rows = db.execute(query + " ORDER BY json_extract(data,'$.created_at') DESC, id DESC LIMIT ?", (*args, limit + 5)).fetchall()
        more = len(rows) >= limit + 5
        rows = rows[:limit] if more else rows
        return {'agents': [{**self.storage.decode_summary(row[1], expected_id=row[0]), 'archived': True} for row in rows],
                'has_more': more, 'next_before': rows[-1][0] if more else None}
