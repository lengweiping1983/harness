"""主会话与委派运行共用状态词汇和终态判定，调度器负责执行与持久化。"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, fields
from copy import deepcopy
from typing import Any

from harness.i18n import tr

PENDING = 'pending'
QUEUED = 'queued'
RUNNING = 'running'
STOPPING = 'stopping'
COMPLETED = 'completed'
FAILED = 'failed'
INTERRUPTED = 'interrupted'
ACTIVE_STATES = frozenset({PENDING, QUEUED, RUNNING, STOPPING})
TERMINAL_STATES = frozenset({COMPLETED, FAILED, INTERRUPTED})


def run_failure(code: str, key: str, **params: Any) -> dict[str, Any]:
    """运行错误同时保存当次诊断和可在呈现时解析的资源身份。"""
    return {'code': code, 'message': tr(key, **params),
            'copy': {'key': key, **({'params': params} if params else {})}}


def run_error_copy(error: dict[str, Any] | None) -> dict[str, Any] | None:
    """只读取明确的根级或HarnessError详情资源，不按诊断原文猜测。"""
    if not error:
        return None
    if error.get('copy') is not None:
        return error['copy']
    details = error.get('details')
    return details.get('copy') if isinstance(details, dict) else None


def turn_outcome(result: Any) -> tuple[str, dict[str, Any] | None]:
    """取消优先；仅无错误的正常完成可成功，其他停止原因必须留下失败依据。"""
    if result.reason == 'aborted':
        # 普通取消说明不是执行错误；只有明确的结构化失败需要额外呈现。
        return INTERRUPTED, result.error_info
    error = result.error_info or ({'code': 'run/error', 'message': result.error} if result.error else None)
    if error:
        return FAILED, error
    if result.reason == 'completed':
        return COMPLETED, None
    return FAILED, run_failure('run/incomplete', 'ui.run.incomplete', reason=result.reason)


def run_error_message(error: dict[str, Any] | None) -> str | None:
    """按读取语言呈现明确资源及有序原因，外部诊断保持原样。"""
    if error and error.get('parts'):
        return tr('ui.agent.errorSeparator').join(tr('ui.agent.errorDetail', label=part['label'],
            message=tr(part['message']['key'], **part['message'].get('params', {})) if isinstance(part['message'], dict) else part['message'])
            for part in error['parts'])
    copy = run_error_copy(error)
    if copy is not None:
        return tr(copy['key'], **copy.get('params', {}))
    return error.get('message') if error else None


@dataclass
class AgentRun:
    """唯一运行记录；队列提交与委派只追加关系和调度信息。"""
    id: str
    session_id: str = ''
    text: str = ''
    status: str = QUEUED
    created_at: float = field(default_factory=time.time)
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    request_id: str | None = None
    requester: dict[str, str] | None = field(default=None, kw_only=True)
    access_epoch: int | None = field(default=None, kw_only=True)
    task: Any = field(default=None, repr=False, compare=False)
    finalization_task: Any = field(default=None, init=False, repr=False, compare=False)
    started_at: float | None = field(default=None, kw_only=True)
    finished_at: float | None = field(default=None, kw_only=True)
    retry_of: str | None = field(default=None, kw_only=True)
    recovery: dict[str, Any] | None = field(default=None, kw_only=True)
    goal_id: str | None = field(default=None, kw_only=True)
    prompt_input: dict[str, Any] | None = field(default=None, kw_only=True)
    team_route: dict[str, Any] | None = field(default=None, kw_only=True)
    kind: str = field(default='prompt', kw_only=True)
    description: str = ''
    language: str | None = None
    parent_session_id: str | None = None
    parent_run_id: str | None = None
    root_session_id: str | None = None
    tool_calls: int = 0
    agent: Any = field(default=None, repr=False, compare=False)
    session: Any = field(default=None, repr=False, compare=False)
    archived: bool = False
    recovered: bool = False
    archive_error: str | None = None
    archive_error_copy: dict[str, Any] | None = field(default=None, repr=False, compare=False)
    archived_pending_messages: int | None = None
    context_persisted: bool = False
    source_call_id: str | None = None
    model: str | None = None
    reasoning_effort: str | None = None
    read_only: bool = False
    workspace_cwd: str | None = None
    runner: str | None = None
    scope: str | None = None
    #: 派生深度；受管运行最小为 1（根会话直接派生），后代逐层加一。
    #: 默认值即最小合法深度，使直接构造的运行记录也能通过准入判定。
    depth: int = 1

    @property
    def finished(self) -> bool:
        return self.status in TERMINAL_STATES

    @property
    def settled(self) -> bool:
        """终态与执行、收尾句柄都已结束，才可释放上下文或开始依赖操作。"""
        return self.finished and all(task is None or task.done() for task in (self.task, self.finalization_task))

    @property
    def output(self) -> str:
        """正文从唯一结构化结果派生，不另存成员结果副本。"""
        return (self.result or {}).get('text', '')

    @property
    def error_message(self) -> str | None:
        """成员显示使用错误正文，内部始终保留错误码和详细依据。"""
        return run_error_message(self.error)

    def storage_dict(self) -> dict[str, Any]:
        """序列化当前记录，不保存进程句柄、会话对象或派生投影。"""
        excluded = {'task', 'finalization_task', 'agent', 'session', 'archived', 'archive_error', 'archived_pending_messages', 'archive_error_copy'}
        return {item.name: getattr(self, item.name) for item in fields(self) if item.name not in excluded}

    def to_dict(self) -> dict[str, Any]:
        """按队列或成员表面投影；二者始终来自同一运行对象。"""
        if self.parent_session_id is not None:
            duration = max(0, (self.finished_at if self.finished_at is not None else time.time()) - self.started_at) * 1000 if self.started_at is not None else None
            return {
                'agent_id': self.id, 'language': self.language, 'description': self.description,
                'status': self.status, 'parent_session_id': self.parent_session_id,
                'child_session_id': self.session_id or None, 'parent_agent_id': self.parent_run_id,
                'root_session_id': self.root_session_id, 'context_persisted': self.context_persisted,
                'read_only': self.read_only, 'workspace_cwd': self.workspace_cwd,
                'depth': self.depth,
                **({'runner': self.runner} if self.runner else {}),
                'created_at': int(self.created_at * 1000), 'duration_ms': int(duration) if duration is not None else None,
                'result': self.output, 'error': self.error_message, 'tool_calls': self.tool_calls,
                **({'error_parts': deepcopy(self.error['parts'])} if self.error and self.error.get('parts') else {}),
                **({'error_copy': deepcopy(copy)} if (copy := run_error_copy(self.error)) is not None else {}),
                'pending_messages': len(self.session.agent_messages.pending()) if self.session is not None else (self.archived_pending_messages or 0),
                'archived': self.archived, 'recovered': self.recovered,
                **({'archive_error': self.archive_error} if self.archive_error else {}),
                **({'archive_error_copy': deepcopy(self.archive_error_copy)} if self.archive_error_copy is not None else {}),
            }
        return {**{key: getattr(self, key) for key in ('id', 'session_id', 'text', 'status', 'created_at', 'result', 'error', 'request_id', 'started_at', 'finished_at', 'retry_of', 'goal_id', 'kind')},
                **({'requester': dict(self.requester)} if self.requester is not None else {}),
                **({'recovery': dict(self.recovery)} if self.recovery is not None else {}),
                **({'prompt_input': self.prompt_input} if self.prompt_input is not None else {}),
                **({'team_route': self.team_route} if self.team_route is not None else {})}

    def summary_line(self) -> str:
        """模型列表只展示当前状态和耗时，不持有第二份成员状态。"""
        icon = {PENDING: '…', QUEUED: '…', RUNNING: '▶', COMPLETED: '✔', FAILED: '✘', INTERRUPTED: '⏹'}.get(self.status, '?')
        text = f'{icon} {self.id} [{self.status}] {self.description}'
        member = (self.prompt_input or {}).get('member')
        if isinstance(member, str) and member:
            text += ' ' + json.dumps({'member': member}, ensure_ascii=False)
        if self.started_at is not None:
            elapsed = max(0, (self.finished_at if self.finished_at is not None else time.time()) - self.started_at)
            text += f' ({elapsed:.1f}s)'
        return text
