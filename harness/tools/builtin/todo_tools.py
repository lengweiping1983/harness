"""内置 Todo 工具。

整体替换和局部更新共用提交路径，把完整快照写进 ``todo/write`` 日志。
清单身份独立于计划，读写使用一致作用域，UI 继续从原有事件渲染清单。
"""

from __future__ import annotations

from harness.i18n import tr
from ...llm.types import SchemaResource

from dataclasses import dataclass, replace
from collections.abc import Mapping
from typing import Any, Literal, Callable
import uuid
import json
import asyncio

from ...core.jsonutil import to_jsonable
from ...core.events import EventBus
from ...llm.types import TextBlock
from ..registry import ToolDefinition, ToolRunContext, ToolResult
from .todo_state import TodoConflict, TodoSnapshot, empty_snapshot, resolve_snapshot, snapshots, snapshot_from_event, update_base_snapshot

TodoStatus = Literal["pending", "in_progress", "completed"]


@dataclass(frozen=True, slots=True)
class TodoItem:
    """一个待办项。"""

    content: str
    status: TodoStatus
    plan_id: str | None = None
    evidence: tuple[dict[str, Any], ...] = ()
    id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"content": self.content, "status": self.status,
                **({'id': self.id} if self.id else {}),
                **({'plan_id': self.plan_id} if self.plan_id else {}),
                **({'evidence': to_jsonable(self.evidence)} if self.evidence else {})}


def _copy_items(items: list[TodoItem]) -> list[TodoItem]:
    """隔离SDK输入及返回快照，持久只读映射同样按JSON值解冻。"""
    return [replace(item, evidence=tuple(to_jsonable(item.evidence))) for item in items]


class TodoStore:
    """会话级 todo 状态（挂在 :class:`ToolRegistry` 的 ``services`` 上）。"""

    def __init__(self, bus: EventBus | None = None, session_of: Callable | None = None) -> None:
        self.bus = bus
        self.session_of = session_of
        #: session_id -> 最新列表
        self._lists: dict[str, list[TodoItem]] = {}
        self._write_locks: dict[str, asyncio.Lock] = {}

    def write_lock(self, session_id: str) -> asyncio.Lock:
        """将会话的提交、内存更新与发布一起串行化。"""
        return self._write_locks.setdefault(session_id, asyncio.Lock())

    def get(self, session_id: str) -> list[TodoItem]:
        if self.session_of is not None:
            for event in reversed(self.session_of(session_id).events_of_type('todo/write')):
                snapshot = snapshot_from_event(session_id, event)
                return [TodoItem(content=row['content'], status=row['status'], plan_id=row.get('plan_id'), evidence=tuple(to_jsonable(row.get('evidence', []))), id=row['id']) for row in snapshot.todos]
        return _copy_items(self._lists.get(session_id, []))

    def write(self, session_id: str, items: list[TodoItem]) -> list[TodoItem]:
        self._lists[session_id] = _copy_items(items)
        return _copy_items(self._lists[session_id])


def validate_todos(raw: list[dict[str, Any]], *, allow_parallel: bool) -> list[TodoItem]:
    """校验并规范化 todo 列表。

    规则：内容非空、不得重复；``allow_parallel=False`` 时最多一项进行中。
    """
    items: list[TodoItem] = []
    if not isinstance(raw, list) or len(raw) > 100:
        raise ValueError(tr('python.todo_tools.6682cba69e78'))
    seen: set[str] = set()
    seen_ids: set[str] = set()
    active = 0
    for entry in raw:
        if not isinstance(entry, dict) or not isinstance(entry.get('content'), str):
            raise ValueError(tr('python.todo_tools.a3e5ee7f975b'))
        content = entry['content'].strip()
        if not content:
            raise ValueError(tr('python.todo_tools.8c15a7a05e40'))
        if len(content) > 2000:
            raise ValueError(tr('python.todo_tools.6f1c6f95d6f1'))
        if content in seen:
            raise ValueError(tr('python.todo_tools.51b4400507c0', p0=f'{content!r}'))
        seen.add(content)
        status = entry.get("status")
        if status not in ("pending", "in_progress", "completed"):
            raise ValueError(tr('python.todo_tools.686a8d062391', p0=f'{status!r}'))
        if status == "in_progress":
            active += 1
        task_id = entry.get('id')
        if task_id is not None:
            if not isinstance(task_id, str) or not task_id or len(task_id) > 200 or task_id in seen_ids:
                raise ValueError(tr('python.todo_tools.beaa8575ae90'))
            seen_ids.add(task_id)
        items.append(TodoItem(content=content, status=status, id=task_id))
    if not allow_parallel and active > 1:
        raise ValueError(tr('python.todo_tools.6126afe33679', p0=f'{active}'))
    return items


def build_todo_tools(store: TodoStore, *, allow_parallel: bool = True) -> list[ToolDefinition]:
    """构造整体替换、局部更新、显式关联和分页恢复待办工具。"""

    parallel_hint = (
        tr('python.todo_tools.3685666f2a67')
        if allow_parallel
        else tr('python.todo_tools.8dd1ad964759')
    )

    def session_for(ctx: ToolRunContext):
        return ctx.services.get('session') or (store.session_of(ctx.session_id) if store.session_of else None)

    def validate_plan(session: Any, plan_id: str | None, current: TodoSnapshot) -> None:
        if plan_id is None:
            return
        from ...app.plans import versions
        record = next((row for row in versions(session) if row['id'] == plan_id), None)
        if record is None:
            raise TodoConflict('UNKNOWN_PLAN', current, plan_id=plan_id)
        if record['status'] != 'approved':
            raise TodoConflict('PLAN_NOT_APPROVED', current, plan_id=plan_id, plan_status=record['status'])

    def current_snapshot(session: Any, args: Any, ctx: ToolRunContext, *, writing: bool = False) -> TodoSnapshot:
        """首次建单由宿主选择本轮批准计划；显式作用域和已有清单不被改写。"""
        if session.latest('todo/write') is None and not any(
                args.get(key) is not None for key in ('list_id', 'plan_id', 'snapshot_token')):
            provider = getattr(ctx.services.get('plan_controller'), 'implementation_approval', None)
            approval = provider(session) if callable(provider) else None
            if approval is not None:
                args = {**args, 'plan_id': approval['id']}
        return resolve_snapshot(session, args, writing=writing)

    def recoverable(execute):
        """冲突作为结构化错误回传模型和 UI，绝不自动重置版本或任务身份。"""
        async def run(args: Any, ctx: ToolRunContext) -> ToolResult:
            try:
                return await execute(args, ctx)
            except TodoConflict as error:
                payload = error.payload()
                if error.code in {'INVALID_EVIDENCE', 'FAILED_EVIDENCE'}:
                    payload.update(evidence_candidates(session_for(ctx)))
                return ToolResult(content=[TextBlock(text=json.dumps(payload, ensure_ascii=False))],
                                  is_error=True, display=payload, data=payload, progress=False)
        return run

    def evidence_candidates(session: Any, before: int | None = None) -> dict[str, Any]:
        """只列真实成功结果，分页展示短序号，不替模型选择完成依据。"""
        if before is not None and (type(before) is not int or before < 0):
            raise ValueError(tr('todo.evidence_before'))
        events = session.events_of_type('tool/result') if session else []
        rows = []
        for event in reversed(events):
            key, results = event.data.get('call_id'), event.data.get('results', [])
            if (before is not None and event.seq >= before or not key
                    or event.data.get('pruned') or not results
                    or any(not isinstance(row, Mapping) or row.get('is_error') is not False
                           or row.get('tool_use_id') != key for row in results)
                    or event.data.get('name') in {'todo_read', 'todo_write', 'todo_update', 'todo_bind', 'get_goal', 'update_goal', 'exit_plan_mode'}):
                continue
            text = '\n'.join(str(row.get('content', '')) for row in results)
            rows.append({'source_seq': event.seq, 'call_id': key, 'name': event.data.get('name', ''),
                         'preview': text[:240], 'truncated': len(text) > 240})
            if len(rows) == 21:
                break
        return {'evidence_candidates': rows[:20],
                'next_evidence_before': rows[19]['source_seq'] if len(rows) > 20 else None}

    @recoverable
    async def todo_read(args: Any, ctx: ToolRunContext) -> ToolResult:
        session = session_for(ctx)
        if session is None:
            raise ValueError(tr('python.todo_tools.6c58d2e47444'))
        offset, limit = args.get('offset', 0), args.get('limit', 20)
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError(tr('python.todo_tools.61dca215a92d'))
        if offset and args.get('expected_seq') is None and args.get('snapshot_token') is None:
            raise ValueError(tr('python.todo_tools.9c0f6f3bddea'))
        current = current_snapshot(session, args, ctx)
        if offset > len(current.todos):
            raise ValueError(tr('python.todo_tools.3c24f7820da4'))
        payload = current.page(offset, limit)
        payload.update(evidence_candidates(session, args.get('evidence_before')))
        return ToolResult(content=[TextBlock(text=json.dumps(payload, ensure_ascii=False))], data=payload)

    @recoverable
    async def todo_write(args: Any, ctx: ToolRunContext) -> ToolResult:
        async with store.write_lock(ctx.session_id):
            return await commit_todos(args, ctx)

    @recoverable
    async def todo_update(args: Any, ctx: ToolRunContext) -> ToolResult:
        async with store.write_lock(ctx.session_id):
            session = session_for(ctx)
            if session is None:
                raise ValueError(tr('python.todo_tools.6c58d2e47444'))
            try:
                current = current_snapshot(session, args, ctx, writing=True)
                base = current
            except TodoConflict as error:
                if error.code != 'VERSION_CONFLICT':
                    raise
                current = error.current
                base = update_base_snapshot(session, args, current)
            require_version(args, current)
            changes = args.get('updates')
            if not isinstance(changes, list) or not 1 <= len(changes) <= 100:
                raise ValueError(tr('todo.invalid_updates'))
            by_id = {row.get('id'): row for row in current.todos}
            base_by_id = {row.get('id'): row for row in base.todos}
            seen, patches = set(), {}
            for change in changes:
                if not isinstance(change, dict) or not isinstance(change.get('id'), str) or change['id'] in seen:
                    raise ValueError(tr('todo.invalid_updates'))
                task_id = change['id']
                seen.add(task_id)
                if task_id not in by_id:
                    raise TodoConflict('UNKNOWN_TASK_ID', current, invalid_ids=[task_id])
                if len(change) < 2 or change.keys() - {'id', 'content', 'status', 'evidence_call_ids', 'evidence_seqs'}:
                    raise ValueError(tr('todo.invalid_updates'))
                # 同批调用可以共享快照；只合并完整任务仍与基线一致的修改，包含正文及证据。
                if base.source_seq != current.source_seq and base_by_id.get(task_id) != by_id[task_id]:
                    raise TodoConflict('VERSION_CONFLICT', current)
                patches[task_id] = change
            rows = [{**{key: row[key] for key in ('id', 'content', 'status') if key in row},
                     **patches.get(row.get('id'), {})} for row in current.todos]
            result = await commit_todos({**args, 'todos': rows}, ctx, current=current)
            if base.source_seq != current.source_seq:
                result.display['merged_from_seq'] = base.source_seq
                result.content.append(TextBlock(text=tr('todo.update_merged', source_seq=base.source_seq)))
            return result

    def require_version(args: Any, current: TodoSnapshot, *, creating: bool = False) -> None:
        if args.get('snapshot_token') is None and args.get('expected_seq') is None:
            # 仅未有过提交的清单可免传版本；锁内判定，不能把已清空的清单当成首次创建。
            if not creating or current.source_seq != -1:
                raise TodoConflict('VERSION_REQUIRED', current)

    @recoverable
    async def todo_bind(args: Any, ctx: ToolRunContext) -> ToolResult:
        async with store.write_lock(ctx.session_id):
            session = session_for(ctx)
            if session is None:
                raise ValueError(tr('python.todo_tools.6c58d2e47444'))
            target_plan = args.get('plan_id')
            current = resolve_snapshot(session, {key: value for key, value in args.items() if key != 'plan_id'}, writing=True)
            validate_plan(session, target_plan, current)
            require_version(args, current)
            # 同一计划只允许一个活动清单，历史关联仍可按计划读取。
            latest = {row.list_id: row for row in snapshots(session)}
            if any(row.plan_id == target_plan and row.list_id != current.list_id for row in latest.values()):
                raise TodoConflict('SCOPE_MISMATCH', current)
            rows = [{key: row[key] for key in ('id', 'content', 'status') if key in row} for row in current.todos]
            return await commit_todos({'todos': rows}, ctx, current=current, binding=target_plan)

    async def commit_todos(args: Any, ctx: ToolRunContext, *, current: TodoSnapshot | None = None,
                           binding: str | None = None) -> ToolResult:
        """记录/更新任务清单。"""
        items = validate_todos(args.get("todos"), allow_parallel=allow_parallel)
        session = session_for(ctx)
        if current is None:
            if session is not None:
                current = current_snapshot(session, args, ctx, writing=True)
                require_version(args, current, creating=True)
            else:
                validate_plan(session, args.get('plan_id'), empty_snapshot(ctx.session_id))
                if any(args.get(key) is not None for key in ('expected_seq', 'snapshot_token', 'list_id')):
                    raise ValueError(tr('python.todo_tools.a981d51b92e5'))
                current = empty_snapshot(ctx.session_id)
                current = TodoSnapshot(ctx.session_id, current.list_id, todos=[row.to_dict() for row in store.get(ctx.session_id)])
        plan_id = binding if binding is not None else current.plan_id
        validate_plan(session, plan_id, current)
        previous = current.todos
        by_id = {row['id']: row for row in previous if row.get('id')}
        by_content = {row['content']: row for row in previous}
        # 模型有时为新任务指定人类可读标签，例如 "1" 或 "task-1"。
        # 规范化这些标签，不接受过期或超出作用域的已存标识。
        recorded_ids = {row['id'] for event in session.events_of_type('todo/write')
                        for row in event.data.get('todos', []) if row.get('id')} if session else set()
        evidence_by_id: dict[str, list[Any]] = {}
        if session and any(row.get('evidence_call_ids') for row in args.get('todos', [])):
            for event in session.events_of_type('tool/result'):
                evidence_by_id.setdefault(event.data.get('call_id', ''), []).append(event)
        linked = []
        assigned_ids = set()
        normalized_ids = []
        for item, raw in zip(items, args.get('todos', [])):
            old = by_id.get(item.id) if item.id is not None else by_content.get(item.content)
            if item.id is not None and old is None:
                if item.id.startswith('todo_') or item.id in recorded_ids:
                    raise TodoConflict('UNKNOWN_TASK_ID', current, invalid_ids=[item.id])
                old = by_content.get(item.content)
            task_id = old.get('id') if old else None
            task_id = task_id or 'todo_' + uuid.uuid4().hex
            if item.id is not None and item.id != task_id:
                normalized_ids.append({'input_id': item.id, 'id': task_id})
            if task_id in assigned_ids:
                raise ValueError(tr('python.todo_tools.1992a68cb7a6'))
            assigned_ids.add(task_id)
            ids = raw.get('evidence_call_ids', [])
            seqs = raw.get('evidence_seqs', [])
            if (not isinstance(seqs, list) or len(seqs) > 10
                    or any(type(seq) is not int or seq < 0 for seq in seqs) or len(set(seqs)) != len(seqs)
                    or 'evidence_call_ids' in raw and 'evidence_seqs' in raw):
                raise TodoConflict('INVALID_EVIDENCE', current)
            if not isinstance(ids, list) or len(ids) > 10 or any(not isinstance(key, str) or not key or len(key) > 200 for key in ids) or len(set(ids)) != len(ids):
                raise TodoConflict('INVALID_EVIDENCE', current)
            referenced = []
            for seq in seqs:
                event = session.event_at(seq) if session else None
                if event is None or event.type != 'tool/result' or not event.data.get('call_id'):
                    raise TodoConflict('INVALID_EVIDENCE', current, invalid_ids=[str(seq)])
                referenced.append(event)
            evidence = [dict(row) for row in old.get('evidence', [])] if old and not {'evidence_call_ids', 'evidence_seqs'} & raw.keys() else []
            for key in ids:
                matches = evidence_by_id.get(key, [])
                if len(matches) != 1:
                    raise TodoConflict('INVALID_EVIDENCE', current, invalid_ids=[key])
                referenced.append(matches[0])
            for event in referenced:
                key = event.data['call_id']
                results = event.data.get('results', [])
                if event.data.get('pruned') or not results or any(not isinstance(row, Mapping) or row.get('is_error') is not False or row.get('tool_use_id') != key for row in results):
                    raise TodoConflict('FAILED_EVIDENCE', current, invalid_ids=[key])
                text = '\n'.join(str(row.get('content', '')) for row in results)
                evidence.append({'call_id': key, 'name': event.data.get('name', ''), 'source_seq': event.seq,
                                 'summary': text[:2000], 'truncated': len(text) > 2000})
            linked.append(TodoItem(item.content, item.status, plan_id, tuple(evidence), task_id))
        items = linked
        payload = {'todos': [item.to_dict() for item in items], 'list_id': current.list_id,
                   **({'plan_id': plan_id} if plan_id else {})}
        # 公布成功或更新内存前先持久化，事件总线监听器不构成提交边界。
        committed = await session.append('todo/write', payload) if session is not None else None
        store.write(ctx.session_id, items)
        # 广播事件，供 UI 实时渲染清单
        if store.bus is not None:
            await store.bus.emit(
                "todo/write",
                ctx.session_id,
                payload,
            )
        done = sum(1 for i in items if i.status == "completed")
        saved = TodoSnapshot(ctx.session_id, current.list_id, plan_id, committed.seq if committed else -1, payload['todos'])
        revision_note = tr('python.todo_tools.cfe986b02d93', p0=f'{committed.seq}', p1=f'{committed.seq}') if committed else ''
        summary = json.dumps(saved.metadata() if committed else {'list_id': current.list_id}, ensure_ascii=False) + '\n'
        summary += tr('python.todo_tools.44745f7a1a6e', p0=f'{len(items)}', p1=f'{done}', p2=f'{revision_note}') + '\n'.join(
            f"{item.id} | {item.status} | {item.content[:80]}{'…' if len(item.content) > 80 else ''}" for item in items)
        if normalized_ids:
            summary += tr('python.todo_tools.7939f9eb4061', p0=f'{len(normalized_ids)}')
        if done < len(items):
            summary += (tr('python.todo_tools.3b8f137fdbe0'))
        return ToolResult(
            content=[TextBlock(text=summary)],
            display={"todos": [i.to_dict() for i in items], **(saved.metadata() if committed else {}),
                     **({'normalized_ids': normalized_ids} if normalized_ids else {})},
            data=items,
        )

    scope_properties = {
        'list_id': {'type': 'string', 'description': tr('todo.list_id')},
        'plan_id': {'type': 'string', 'description': tr('todo.plan_id')},
        'snapshot_token': {'type': 'string', 'description': tr('todo.snapshot_token')},
        'expected_seq': {'type': 'integer', 'minimum': -1, 'description': tr('python.todo_tools.e6defc350fa9')},
    }
    update_fields = {
        'id': {'type': 'string', 'minLength': 1, 'maxLength': 200, 'description': tr('todo.update_task_id')},
        'content': {'type': 'string', 'minLength': 1, 'maxLength': 2000},
        'status': {'type': 'string', 'enum': ['pending', 'in_progress', 'completed']},
        'evidence_call_ids': {'type': 'array', 'maxItems': 10, 'uniqueItems': True,
                              'items': {'type': 'string', 'minLength': 1, 'maxLength': 200}},
        'evidence_seqs': {'type': 'array', 'maxItems': 10, 'uniqueItems': True,
                          'items': {'type': 'integer', 'minimum': 0}, 'description': tr('todo.evidence_seqs')},
    }
    return [
        ToolDefinition(
            schema={
                "description_parts": (SchemaResource('python.todo_tools.b99ad8eb37d9', {'p0': SchemaResource('python.todo_tools.3685666f2a67' if allow_parallel else 'python.todo_tools.8dd1ad964759')}), SchemaResource('todo.write_hint'),),
                "parameter_keys": {
                    ('properties', 'list_id', 'description'): 'todo.list_id',
                    ('properties', 'plan_id', 'description'): 'todo.plan_id',
                    ('properties', 'snapshot_token', 'description'): 'todo.snapshot_token',
                    ('properties', 'expected_seq', 'description'): 'python.todo_tools.e6defc350fa9',
                    ('properties', 'todos', 'description'): 'python.todo_tools.d565a0b92a1b',
                    ('properties', 'todos', 'items', 'properties', 'id', 'description'): 'python.todo_tools.aeae2fd7887a',
                    ('properties', 'todos', 'items', 'properties', 'content', 'description'): 'python.todo_tools.ac1546c5fe19',
                    ('properties', 'todos', 'items', 'properties', 'evidence_call_ids', 'description'): 'python.todo_tools.2a253f920bc3',
                    ('properties', 'todos', 'items', 'properties', 'evidence_seqs', 'description'): 'todo.evidence_seqs',
                    ('properties', 'todos', 'items', 'properties', 'status', 'description'): 'python.todo_tools.f850d949259b',
                },
                "name": "todo_write",
                "description": (
                    tr('python.todo_tools.b99ad8eb37d9', p0=f'{parallel_hint}') + tr('todo.write_hint')
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        **scope_properties,
                        "todos": {
                            "type": "array",
                            'maxItems': 100,
                            "description": tr('python.todo_tools.d565a0b92a1b'),
                            "items": {
                                "type": "object",
                                "properties": {
                                    'id': {'type': 'string', 'minLength': 1, 'maxLength': 200, 'description': tr('python.todo_tools.aeae2fd7887a')},
                                    "content": {"type": "string", 'maxLength': 2000, "description": tr('python.todo_tools.ac1546c5fe19')},
                                    'evidence_call_ids': {'type': 'array', 'maxItems': 10, 'uniqueItems': True, 'items': {'type': 'string', 'minLength': 1, 'maxLength': 200}, 'description': tr('python.todo_tools.2a253f920bc3')},
                                    'evidence_seqs': update_fields['evidence_seqs'],
                                    "status": {
                                        "type": "string",
                                        "enum": ["pending", "in_progress", "completed"],
                                        "description": tr('python.todo_tools.f850d949259b'),
                                    },
                                },
                                "required": ["content", "status"],
                                "additionalProperties": False,
                            },
                        }
                    },
                    "required": ["todos"],
                    "additionalProperties": False,
                },
            },
            execute=todo_write,
            concurrency_safe=False,
        ),
        ToolDefinition(
            schema={
                "description_parts": (SchemaResource('python.todo_tools.cdaf28d2551e'), SchemaResource('todo.read_hint'),),
                "parameter_keys": {
                    ('properties', 'list_id', 'description'): 'todo.list_id',
                    ('properties', 'plan_id', 'description'): 'todo.plan_id',
                    ('properties', 'snapshot_token', 'description'): 'todo.snapshot_token',
                    ('properties', 'evidence_before', 'description'): 'todo.evidence_before',
                    ('properties', 'expected_seq', 'description'): 'python.todo_tools.5db2a67917fb',
                },
                'name': 'todo_read',
                'description': tr('python.todo_tools.cdaf28d2551e') + tr('todo.read_hint'),
                'parameters': {'type': 'object', 'properties': {
                    **scope_properties,
                    'offset': {'type': 'integer', 'minimum': 0, 'default': 0},
                    'limit': {'type': 'integer', 'minimum': 1, 'maximum': 20, 'default': 20},
                    'evidence_before': {'type': 'integer', 'minimum': 0, 'description': tr('todo.evidence_before')},
                    'expected_seq': {'type': 'integer', 'minimum': -1, 'description': tr('python.todo_tools.5db2a67917fb')},
                }, 'additionalProperties': False},
            },
            execute=todo_read, concurrency_safe=True, effect='read',
        ),
        ToolDefinition(
            schema={
                "description_parts": (SchemaResource('todo.update_description'),),
                "parameter_keys": {
                    ('properties', 'updates', 'items', 'properties', 'id', 'description'): 'todo.update_task_id',
                    ('properties', 'list_id', 'description'): 'todo.list_id',
                    ('properties', 'plan_id', 'description'): 'todo.plan_id',
                    ('properties', 'snapshot_token', 'description'): 'todo.snapshot_token',
                    ('properties', 'expected_seq', 'description'): 'python.todo_tools.e6defc350fa9',
                    ('properties', 'updates', 'items', 'properties', 'evidence_seqs', 'description'): 'todo.evidence_seqs',
                },'name': 'todo_update', 'description': tr('todo.update_description'),
                    'parameters': {'type': 'object', 'properties': {**scope_properties,
                        'updates': {'type': 'array', 'minItems': 1, 'maxItems': 100,
                                    'items': {'type': 'object', 'properties': update_fields,
                                              'required': ['id'], 'additionalProperties': False}}},
                                   'required': ['updates'], 'additionalProperties': False}},
            execute=todo_update, concurrency_safe=False,
        ),
        ToolDefinition(
            schema={
                "description_parts": (SchemaResource('todo.bind_description'),),
                "parameter_keys": {
                    ('properties', 'list_id', 'description'): 'todo.list_id',
                    ('properties', 'plan_id', 'description'): 'todo.plan_id',
                    ('properties', 'snapshot_token', 'description'): 'todo.snapshot_token',
                    ('properties', 'expected_seq', 'description'): 'python.todo_tools.e6defc350fa9',
                },'name': 'todo_bind', 'description': tr('todo.bind_description'),
                    'parameters': {'type': 'object', 'properties': scope_properties,
                                   'required': ['list_id', 'plan_id'], 'additionalProperties': False}},
            execute=todo_bind, concurrency_safe=False,
        ),
    ]


__all__ = ["TodoStore", "TodoItem", "build_todo_tools", "validate_todos"]
