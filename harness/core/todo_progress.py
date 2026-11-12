"""仅在本回合有效的建议提醒，工具成功不代表任务完成。"""

from __future__ import annotations

from harness.i18n import tr
from harness.tools.builtin.todo_state import TodoSnapshot, empty_snapshot, snapshot_from_event

import json
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from .jsonutil import to_jsonable


BOOKKEEPING_TOOLS = frozenset({
    'todo_write', 'todo_read', 'todo_update', 'todo_bind', 'create_goal', 'get_goal', 'update_goal',
    'project_tasks_generate', 'project_task_save',
    'exit_plan_mode', 'ask_user_question', 'job_output', 'job_list',
    'terminal_read', 'terminal_list', 'list_agents', 'wait_agent', 'wait_agents',
    'session_status', 'clock', 'sleep', 'wait',
})


def is_business_result(name: str, result: Any) -> bool:
    return not result.is_error and name not in BOOKKEEPING_TOOLS and result.progress is not False


class TodoProgressReminder:
    """每回合一个实例，仅以已提交的待办事件为依据。"""

    def __init__(self) -> None:
        self.fingerprint: tuple | None = None
        self.work_steps = 0
        self.notices = 0
        self._seen: dict[tuple, int] = {}
        self._final_checked: set[tuple] = set()
        self._selected_event: Any = None
        self._selected_empty: TodoSnapshot | None = None
        self._selection_generation: int | None = None
        self.request_event: Any = None
        self.request_snapshot: TodoSnapshot | None = None

    def begin_request(self, session: Any) -> None:
        """请求定义和上下文绑定同一已知快照，不在模型返回时改用最新版本。"""
        if self._selection_generation != session.history_generation:
            self._selected_event = None
            self._selected_empty = None
        self._selection_generation = session.history_generation
        if self._selected_empty is not None:
            self.request_event = None
            self.request_snapshot = self._selected_empty
            return
        selected = self._selected_event
        if selected is not None and session.event_at(selected.seq) is not selected:
            selected = None
        event = selected if selected is not None else session.latest('todo/write')
        self._selected_event = self.request_event = event
        self.request_snapshot = snapshot_from_event(session.id, event) if event is not None else None

    def clear_request(self) -> None:
        """工具不可写时不读取或暴露清单，恢复准入后再绑定原选择。"""
        self.request_event = None
        self.request_snapshot = None

    def selected_event(self, session: Any) -> Any:
        """进度与收尾提醒沿用所选清单，不被另一活动清单的状态覆盖。"""
        if self._selected_empty is not None:
            return None
        if self._selected_event is not None and session.event_at(self._selected_event.seq) is self._selected_event:
            return self._selected_event
        return session.latest('todo/write')

    def observe_result(self, call: Any, outcome: Any, session: Any) -> None:
        """只在成功读取或写入后选择新快照，冲突回执不能自动推进版本。"""
        if outcome.is_error or call.name not in {'todo_read', 'todo_write', 'todo_update', 'todo_bind'}:
            return
        data = outcome.data if call.name == 'todo_read' else outcome.display
        if not isinstance(data, Mapping) or type(data.get('source_seq')) is not int:
            return
        if data['source_seq'] == -1:
            if data.get('plan_id') is not None and not isinstance(data.get('plan_id'), str):
                return
            empty = empty_snapshot(session.id, data.get('plan_id'))
            if data.get('snapshot_token') == empty.token and data.get('list_id') == empty.list_id:
                self._selected_event = None
                self._selected_empty = empty
                self._selection_generation = session.history_generation
            return
        event = session.event_at(data['source_seq'])
        if event is None or event.type != 'todo/write':
            return
        saved = snapshot_from_event(session.id, event)
        if data.get('snapshot_token') == saved.token and data.get('list_id') == saved.list_id:
            self._selected_event = event
            self._selected_empty = None
            self._selection_generation = session.history_generation

    def project_schemas(self, schemas: list[Any]) -> list[Any]:
        """仅约束本请求的模型视图，注册原件和SDK版本协议保持不变。"""
        snapshot = self.request_snapshot
        if snapshot is None:
            return schemas
        projected = []
        for schema in schemas:
            if schema.name != 'todo_update':
                projected.append(schema)
                continue
            parameters = to_jsonable(schema.parameters)
            properties = parameters['properties']
            properties['snapshot_token']['enum'] = [snapshot.token]
            properties['list_id']['enum'] = [snapshot.list_id]
            properties['expected_seq']['enum'] = [snapshot.source_seq]
            if snapshot.plan_id is not None:
                properties['plan_id']['enum'] = [snapshot.plan_id]
            parameters['required'] = list(dict.fromkeys([*parameters.get('required', []), 'snapshot_token']))
            if snapshot.todos:
                properties['updates']['items']['properties']['id']['enum'] = [row['id'] for row in snapshot.todos]
            projected.append(replace(schema, parameters=parameters))
        return projected

    def sync(self, event: Any) -> bool:
        data = event.data if event is not None else {}
        todos = data.get('todos', [])
        fingerprint = (data.get('plan_id'), tuple(
            (row.get('id'), row.get('content'), row.get('status'))
            for row in todos if isinstance(row, Mapping)
        ))
        if fingerprint == self.fingerprint:
            return False
        self.fingerprint = fingerprint
        self.work_steps = 0
        # 恢复到之前相同的列表时，仍不能超出本回合配额。
        self.notices = self._seen.get(fingerprint, 0)
        return True

    def observe_batch(self, event: Any, *, successful_work: bool) -> None:
        changed = self.sync(event)
        if not changed and successful_work and self._unfinished(event):
            self.work_steps += 1

    @staticmethod
    def _unfinished(event: Any) -> list[dict]:
        if event is None:
            return []
        return [row for row in event.data.get('todos', [])
                if isinstance(row, Mapping) and row.get('status') != 'completed']

    def take_notice(self, event: Any, *, session_id: str) -> dict[str, Any] | None:
        self.sync(event)
        unfinished = self._unfinished(event)
        if not unfinished:
            return None
        threshold = 3 if self.notices == 0 else self.notices * 9
        if self.work_steps < threshold:
            return None
        self.notices += 1
        self._seen[self.fingerprint] = self.notices
        # 任务描述是数据而非指令，不能将整个列表复制到开发者消息。
        # todo_read 会恢复完整且带版本的数据。
        current = [{'id': row.get('id'), 'status': row.get('status'),
                    'content': str(row.get('content', ''))[:120]} for row in unfinished[:3]]
        saved = TodoSnapshot(session_id, event.data['list_id'], event.data.get('plan_id'), event.seq)
        snapshot = json.dumps({**saved.metadata(),
                               'unfinished': len(unfinished), 'sample': current}, ensure_ascii=False)
        text = (
            tr('owned.todo_progress.d74f79c29ecc') + snapshot
        )
        return {'kind': 'todo_progress_reminder', 'source_seq': event.seq,
                'plan_id': event.data.get('plan_id'), 'work_steps': self.work_steps, 'text': text}

    def take_final_notice(self, event: Any) -> dict[str, Any] | None:
        """最终回答前核对一次当前清单，不凭成功工具结果自动勾选。"""
        self.sync(event)
        if not self._unfinished(event) or self.fingerprint in self._final_checked:
            return None
        self._final_checked.add(self.fingerprint)
        return {'kind': 'todo_final_review', 'source_seq': event.seq,
                'text': tr('owned.todo_progress.final_review')}

    def snapshot_context(self, event: Any, *, session_id: str, start_seq: int) -> str | None:
        """请求尾部携带当前快照，避免模型遗漏版本；不自动替换调用参数。"""
        if event is None or not event.data.get('todos'):
            return None
        if event.seq <= start_seq and not self._unfinished(event):
            return None
        saved = TodoSnapshot(session_id, event.data['list_id'], event.data.get('plan_id'), event.seq)
        facts = {**saved.metadata(), 'tasks': [
            {'id': row['id'], 'status': row['status']} for row in event.data['todos']]}
        return tr('todo.runtime_snapshot') + '\n' + json.dumps(facts, ensure_ascii=False)
