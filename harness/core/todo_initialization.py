"""普通实施提供建单建议，已批准计划在实施前要求真实清单。"""
from __future__ import annotations

import json
from typing import Any

from harness.i18n import tr
from .errors import HarnessError
from .todo_progress import BOOKKEEPING_TOOLS


class TodoInitialization:
    """批准计划立即要求建单；无计划时连续写入只触发有限提醒。"""

    MAX_REPAIR_REQUESTS = 2
    WRITE_BATCH_THRESHOLD = 3

    def __init__(self, start_seq: int):
        self.start_seq = start_seq
        self.plan: dict[str, Any] | None = None
        self.required = False
        self.write_batches = 0
        self.repair_requests = 0
        self.pending_notice = False

    def sync(self, plan: dict[str, Any] | None) -> None:
        """新的已批准计划开启独立的建单检查，不从 Markdown 猜任务。"""
        if plan is None and self.plan is not None:
            self.plan = None
            self.required = self.write_batches >= self.WRITE_BATCH_THRESHOLD
            self.repair_requests = 0
            self.pending_notice = self.required
        if plan is not None and (self.plan is None or plan['id'] != self.plan['id']):
            self.plan = plan
            self.required = True
            self.repair_requests = 0
            self.pending_notice = True

    def has_list(self, event: Any) -> bool:
        if event is None or not event.data.get('todos'):
            return False
        if self.plan is not None:
            return (event.data.get('plan_id') == self.plan['id']
                    or event.data.get('plan_id') is None and event.seq > self.plan['source_seq'])
        return event.seq > self.start_seq or any(
            row.get('status') != 'completed' for row in event.data['todos'])

    @staticmethod
    def implementation_tool(definition: Any) -> bool:
        """只读和记账不受实施门禁影响；未知副作用不能绕过已批准计划。"""
        return (definition is not None and definition.schema.name not in BOOKKEEPING_TOOLS
                and definition.schema.name not in {'job_kill', 'terminal_signal', 'terminal_close', 'cancel_agent'}
                and definition.effect != 'read')

    def observe_batch(self, event: Any, *, successful_write: bool, list_attempted: bool = False) -> None:
        """仅统计成功落地的写入批次，失败、只读和 Shell 诊断不算开发证据。"""
        if self.required:
            if list_attempted and not self.has_list(event):
                self.pending_notice = True
            return
        if self.has_list(event) or not successful_write:
            return
        self.write_batches += 1
        if self.write_batches >= self.WRITE_BATCH_THRESHOLD:
            self.required = True
            self.pending_notice = True

    def denial(self, event: Any, definition: Any) -> dict[str, Any] | None:
        if self.plan is None or not self.required or self.has_list(event) or not self.implementation_tool(definition):
            return None
        self.pending_notice = True
        return {'code': 'todo/list-required', 'message': tr('todo.initialization.before_work'),
                'not_executed': True, 'recovery': {'tool': 'todo_write'},
                **({'plan_id': self.plan['id']} if self.plan is not None else {})}

    def defer_completion(self, event: Any) -> bool:
        """只有已批准计划的建单要求会阻止直接收尾。"""
        if self.plan is None or not self.required or self.has_list(event):
            return False
        self.pending_notice = True
        return True

    def take_notice(self, event: Any) -> dict[str, Any] | None:
        if not self.required or self.has_list(event) or not self.pending_notice:
            return None
        if self.repair_requests >= self.MAX_REPAIR_REQUESTS:
            if self.plan is None:
                self.pending_notice = False
                return None
            raise HarnessError(tr('todo.initialization.failed'), code='todo/list-required')
        self.repair_requests += 1
        self.pending_notice = False
        scope = {'reason': 'approved_plan' if self.plan is not None else 'repeated_writes',
                 'plan_id': self.plan['id'] if self.plan is not None else None,
                 'write_batches': self.write_batches}
        approved = self.plan is not None
        return {'kind': 'todo_initialization_required' if approved else 'todo_initialization_reminder',
                **scope, 'attempt': self.repair_requests,
                'text': tr('todo.initialization.instructions' if approved else 'todo.initialization.advisory')
                + '\n' + json.dumps(scope, ensure_ascii=False)}
