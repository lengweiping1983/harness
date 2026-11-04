"""仅在本回合有效的建议提醒，工具成功不代表任务完成。"""

from __future__ import annotations

from harness.i18n import tr
from harness.tools.builtin.todo_state import TodoSnapshot

import json
from collections.abc import Mapping
from typing import Any


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
