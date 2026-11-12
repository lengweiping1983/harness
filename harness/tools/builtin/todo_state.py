"""待办清单身份、作用域与快照校验；只从会话日志恢复权威状态。"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import hashlib
import json
from typing import Any
import uuid

from harness.i18n import tr
from harness.core.errors import SessionInvariantError


@dataclass(frozen=True)
class TodoSnapshot:
    """清单身份独立于计划版本，快照凭据绑定会话与确切版本。"""

    session_id: str
    list_id: str
    plan_id: str | None = None
    source_seq: int = -1
    todos: list[dict[str, Any]] = field(default_factory=list)

    @property
    def token(self) -> str:
        raw = json.dumps([self.session_id, self.list_id, self.plan_id, self.source_seq], ensure_ascii=False)
        return 'todo_snapshot_' + hashlib.sha256(raw.encode('utf-8')).hexdigest()

    def metadata(self) -> dict[str, Any]:
        return {'list_id': self.list_id, 'plan_id': self.plan_id,
                'source_seq': self.source_seq, 'snapshot_token': self.token,
                'write_arguments': {'snapshot_token': self.token},
                'update_arguments': {'snapshot_token': self.token}}

    def page(self, offset: int = 0, limit: int = 20) -> dict[str, Any]:
        """完整保留任务正文，限制页大小，证据只暴露事件序号。"""
        page, size = [], 0
        for item in self.todos[offset:offset + limit]:
            row = {key: item[key] for key in ('id', 'content', 'status', 'plan_id') if key in item}
            if item.get('evidence'):
                row['evidence'] = [{'source_seq': ref['source_seq']} for ref in item['evidence']]
            length = len(json.dumps(row, ensure_ascii=False))
            if page and size + length > 12000:
                break
            page.append(row)
            size += length
        end = offset + len(page)
        return {**self.metadata(), 'offset': offset, 'total': len(self.todos), 'todos': page,
                'next_offset': end if end < len(self.todos) else None,
                'notice': tr('python.todo_tools.4f906fe70456')}


class TodoConflict(Exception):
    """可恢复冲突，不通过异常字符串丢失快照与恢复入口。"""

    def __init__(self, code: str, current: TodoSnapshot, *, invalid_ids: list[str] | None = None,
                 plan_id: str | None = None, plan_status: str | None = None):
        self.code = code
        self.current = current
        self.invalid_ids = invalid_ids or []
        self.plan_id = plan_id
        self.plan_status = plan_status
        super().__init__(code)

    def payload(self) -> dict[str, Any]:
        return {'code': self.code, 'message': tr('todo.conflict.' + self.code),
                'updated': False, 'invalid_ids': self.invalid_ids, 'current': self.current.page(),
                **({'plan': {'id': self.plan_id, 'status': self.plan_status}} if self.plan_id else {}),
                'recovery': {'tool': 'todo_read', 'arguments': {'snapshot_token': self.current.token},
                             'max_retries': 0 if self.code == 'PLAN_NOT_APPROVED' else 1, 'instruction': tr(
                                 'todo.recovery.plan_not_approved' if self.code == 'PLAN_NOT_APPROVED'
                                 else 'todo.recovery.create' if self.current.source_seq == -1
                                 else 'todo.recovery.unknown_task' if self.code == 'UNKNOWN_TASK_ID'
                                 else 'todo.recovery')}}


def empty_snapshot(session_id: str, plan_id: str | None = None) -> TodoSnapshot:
    """首次创建使用可重复推导的清单身份。"""
    identity = json.dumps([session_id, plan_id], ensure_ascii=False)
    return TodoSnapshot(session_id, 'list_' + uuid.uuid5(uuid.NAMESPACE_URL, identity).hex, plan_id)


def snapshot_from_event(session_id: str, event: Any) -> TodoSnapshot:
    """SDK和工具共用持久快照校验，损坏事件不投影为空清单。"""
    def invalid():
        raise SessionInvariantError(tr('todo.snapshot.invalid', p0=event.seq))
    data = event.data
    if (not isinstance(data, Mapping) or set(data) - {'list_id', 'plan_id', 'todos'}
            or not isinstance(data.get('list_id'), str) or not data['list_id']
            or not isinstance(data.get('todos'), (list, tuple)) or len(data['todos']) > 100):
        invalid()
    plan_id = data.get('plan_id')
    if plan_id is not None and (not isinstance(plan_id, str) or not plan_id):
        invalid()
    ids, contents = set(), set()
    for row in data['todos']:
        if (not isinstance(row, Mapping) or set(row) - {'id', 'content', 'status', 'plan_id', 'evidence'}
                or not isinstance(row.get('id'), str) or not row['id'] or len(row['id']) > 200
                or row['id'] in ids or not isinstance(row.get('content'), str)
                or not row['content'].strip() or len(row['content']) > 2000 or row['content'] in contents
                or row.get('status') not in ('pending', 'in_progress', 'completed')
                or row.get('plan_id') != plan_id or not isinstance(row.get('evidence', ()), (list, tuple))):
            invalid()
        ids.add(row['id']);contents.add(row['content'])
        for evidence in row.get('evidence', ()):
            if (not isinstance(evidence, Mapping)
                    or set(evidence) != {'call_id', 'name', 'source_seq', 'summary', 'truncated'}
                    or not isinstance(evidence.get('call_id'), str) or not evidence['call_id']
                    or not isinstance(evidence.get('name'), str)
                    or type(evidence.get('source_seq')) is not int or not 0 <= evidence['source_seq'] < event.seq
                    or not isinstance(evidence.get('summary'), str) or len(evidence['summary']) > 2000
                    or type(evidence.get('truncated')) is not bool):
                invalid()
    return TodoSnapshot(session_id, data['list_id'], plan_id, event.seq, data['todos'])


def snapshots(session: Any) -> list[TodoSnapshot]:
    """恢复历次提交，保留计划重新关联之前的历史视图。"""
    return [snapshot_from_event(session.id, event) for event in session.events_of_type('todo/write')]


def resolve_snapshot(session: Any, args: dict[str, Any], *, writing: bool = False) -> TodoSnapshot:
    """读写采用相同默认作用域，凭据只能引用本会话的已知快照。"""
    history = snapshots(session)
    active = history[-1] if history else empty_snapshot(session.id)
    from ...app.plans import versions
    records = versions(session)
    empty = [empty_snapshot(session.id), *(empty_snapshot(session.id, row['id']) for row in records)]
    list_id, plan_id, token = (args.get(key) for key in ('list_id', 'plan_id', 'snapshot_token'))
    # 恢复入口跟随明确选择的清单或已知快照，不能把非活动清单错误引向活动清单。
    known = next((row for row in [*reversed(history), *empty] if row.token == token), None) if token is not None else None
    selected = next((row for row in reversed(history) if row.list_id == known.list_id), known) if known else active
    candidates = history
    if list_id is not None:
        candidates = [row for row in candidates if row.list_id == list_id]
        if not candidates:
            candidates = [row for row in empty if row.list_id == list_id]
            if not candidates:
                raise TodoConflict('UNKNOWN_LIST', selected)
        if known is None:
            selected = candidates[-1]
    if token is not None and known is None:
        raise TodoConflict('INVALID_SNAPSHOT', selected)
    if known is not None and list_id is not None and known.list_id != list_id:
        raise TodoConflict('SCOPE_MISMATCH', selected)
    if plan_id is not None:
        if not any(row['id'] == plan_id for row in records):
            raise TodoConflict('UNKNOWN_PLAN', selected, plan_id=plan_id)
        candidates = [row for row in candidates if row.plan_id == plan_id]
        if not candidates and list_id is not None:
            raise TodoConflict('SCOPE_MISMATCH', selected)
    current = candidates[-1] if candidates else empty_snapshot(session.id, plan_id)
    if token is not None:
        if plan_id is not None and known.plan_id != plan_id:
            raise TodoConflict('SCOPE_MISMATCH', selected)
        current = selected
        if current.token != token:
            raise TodoConflict('VERSION_CONFLICT', current)
    if writing:
        latest = next((row for row in reversed(history) if row.list_id == current.list_id), current)
        if latest.plan_id != current.plan_id:
            raise TodoConflict('SCOPE_MISMATCH', latest)
    expected = args.get('expected_seq')
    if expected is not None:
        if type(expected) is not int or expected < -1:
            raise ValueError(tr('python.todo_tools.f36651f2b514'))
        if expected != current.source_seq:
            raise TodoConflict('VERSION_CONFLICT', current)
    return current


def update_base_snapshot(session: Any, args: dict[str, Any], current: TodoSnapshot) -> TodoSnapshot:
    """恢复局部更新引用的确切基线；任务是否仍可合并由提交方在锁内逐项核对。"""
    token, expected = args.get('snapshot_token'), args.get('expected_seq')
    if token is None and expected is None:
        raise TodoConflict('VERSION_REQUIRED', current)
    for row in [*reversed(snapshots(session)), empty_snapshot(session.id, current.plan_id)]:
        if (row.list_id != current.list_id or token is not None and row.token != token
                or expected is not None and row.source_seq != expected):
            continue
        if row.plan_id != current.plan_id:
            raise TodoConflict('SCOPE_MISMATCH', current)
        return row
    raise TodoConflict('VERSION_CONFLICT', current)
