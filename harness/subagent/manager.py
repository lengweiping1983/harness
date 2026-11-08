"""子代理管理器：把独立 AgentLoop 组织成可观测、可中断的并发单元。

通过协作工具提供派工、续接、单个或批量等待、消息及中断能力。

设计取舍：

* **共享 ToolRegistry，但独立 AgentLoop + 独立 Session**。子代理因此拥有
  自己的上下文窗口与消息历史，不会污染主会话，同时能复用同一套工具与
  事件总线，避免重复实现工具分发。
* **原生角色在同进程运行，独立 Agent 由宿主执行器启动受监督进程**。
  两者共用状态、归档和任务取消；外部执行器负责等待进程清理完成，
  不把工作目录约束冒充系统沙箱。
* **状态机 pending -> running -> completed/failed/interrupted** 显式建模，
  ``wait_agent`` 的等待与 ``list_agents`` 的展示都基于它，而不是靠猜。
"""

from __future__ import annotations

from harness.i18n import tr
from ..core.run_storage import RunStorage
from ..session.events import AppendOp
from ..session.agent_messages import validate_message_text

import asyncio
import contextlib
import contextvars
import hashlib
import json
import math
import uuid
import sqlite3
import logging
import time
from dataclasses import replace as dataclasses_replace
from typing import Any, Callable
from pathlib import Path

from ..core.agent import AgentLoop
from ..llm.types import LlmCallConfig
from ..core.cleanup import join_cleanup, raise_cleanup_errors
from ..core.events import EventBus
from .events import SubagentEventBus
from ..core.errors import HarnessError, PersistenceError
from ..core.runs import PENDING, RUNNING, COMPLETED, FAILED, INTERRUPTED, TERMINAL_STATES, AgentRun, turn_outcome, run_error_message, run_error_copy
from .admission import AdmissionGate
from ..session.session import create_session
from ..session.events import SessionHeader

logger = logging.getLogger(__name__)

#: 子代理默认超时（秒）
DEFAULT_AGENT_TIMEOUT = 600.0
#: 默认不限制子代理轮数；任务仍受运行预算、取消、名额与超时约束。
DEFAULT_MAX_STEPS = None

#: 当前协程所属受管运行的名额凭证；阻塞观察期间据此让渡名额，
#: 使父代理等待子代理时不会与全树并发闸互相等待。
_CURRENT_PERMIT: contextvars.ContextVar[Any] = contextvars.ContextVar("subagent_permit", default=None)


def validate_spawn_text(prompt: Any, description: Any) -> tuple[str, str]:
    """SDK与工具共用正文边界，不把非法参数转换为任务。"""
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError(tr('subagent.spawn.invalid_prompt'))
    if not isinstance(description, str):
        raise ValueError(tr('subagent.spawn.invalid_description'))
    return prompt.strip(), description.strip()


def validate_wait_timeout(value: Any) -> float:
    """等待时间只接受可表示为有限浮点数的非负值，零秒表示立即核对状态。"""
    if type(value) not in (int, float):
        raise ValueError(tr('subagent.wait.invalid_timeout'))
    try:
        timeout = float(value)
    except OverflowError:
        raise ValueError(tr('subagent.wait.invalid_timeout')) from None
    if not math.isfinite(timeout) or timeout < 0:
        raise ValueError(tr('subagent.wait.invalid_timeout'))
    return timeout


def validate_wait_ids(value: Any) -> list[str]:
    """批量观察只接收有界且互不重复的真实身份，不隐式枚举成员。"""
    if (not isinstance(value, list) or not 1 <= len(value) <= 32
            or any(not isinstance(agent_id, str) or not agent_id.strip() for agent_id in value)
            or len(set(value)) != len(value)):
        raise ValueError(tr('agent.message.wait_many.invalid_ids'))
    return list(value)


class SubagentManager:
    """管理所有子代理的生命周期。"""

    def __init__(
        self,
        *,
        adapter: Any,
        tools: Any,
        prompts: Any,
        skills: Any = None,
        config: LlmCallConfig | None = None,
        bus: EventBus | None = None,
        cwd: str | None = None,
        max_steps: int | None = DEFAULT_MAX_STEPS,
        run_storage: RunStorage | Path | None = None,
        contexts: Any = None,
        max_concurrent: int | None = None,
        max_depth: int | None = None,
        job_timeout: float | None = None,
    ) -> None:
        self.adapter = adapter
        self.tools = tools
        self.prompts = prompts
        self.skills = skills
        self.config = config
        self.bus = bus
        self.cwd = cwd
        self.max_steps = max_steps
        self.session_resolver = None
        self.configure_agent: Callable[[AgentRun, AgentLoop], None] | None = None
        self.parent_language = None
        self.before_spawn = None
        self.external_runner = None
        self.request_traces = None
        self.contexts = contexts
        from ..app.run_directory import RunDirectory
        self.runs = RunDirectory(self)
        self.scope_resolver = None
        self.parent_config: Callable[[str], LlmCallConfig | None] | None = None
        self._followup_lock = asyncio.Lock()
        self._closing: dict[str | None, int] = {}
        #: 整棵代理树的并发上限；None 表示不限制名额。后代同样占名额，
        #: 阻塞观察期间通过让渡避免父子互锁（见 :mod:`.admission`）。
        # 准入上限交给 AdmissionGate 严格校验；非法值不能静默退化为无限制。
        self.max_concurrent = max_concurrent
        #: 派生深度上限；None 表示不限制。深度在派生时判定，超限立即失败。
        self.max_depth = max_depth
        #: 单个受管运行的墙钟时限（秒）；None 表示不设时限。
        #: bool 是 int 的子类，且非有限值不能交给 asyncio 超时调度。
        if job_timeout is not None:
            try:
                valid_job_timeout = (
                    type(job_timeout) in (int, float)
                    and job_timeout > 0
                    and math.isfinite(job_timeout)
                )
            except OverflowError:
                valid_job_timeout = False
            if not valid_job_timeout:
                raise ValueError('subagent/invalid-job-timeout')
        self.job_timeout = job_timeout
        self._gate = AdmissionGate(capacity=self.max_concurrent, max_depth=self.max_depth)

        self._states: dict[str, AgentRun] = {}
        from .archive import SubagentArchive
        self.archive = SubagentArchive(run_storage) if run_storage is not None else None
        #: 父 session -> 它名下所有 agent_id，用于会话级清理
        self._by_parent: dict[str, list[str]] = {}

    # ==================================================================
    # 查询
    # ==================================================================
    def get(self, agent_id: str) -> AgentRun | None:
        live = self._states.get(agent_id)
        if live is not None or self.archive is None:
            return live
        restored = self.archive.load(agent_id)
        if restored is not None and not restored.finished:
            restored.status = INTERRUPTED
            restored.recovered = True
            restored.finished_at = time.time()
            restored.error = {'code': 'run/restarted', 'message': tr('ui.agent.handleReleased'), 'copy': {'key': 'ui.agent.handleReleased'}}
        return restored

    def history_page(self, owner: str | None, *, before: str | None = None, limit: int = 50, include_descendants: bool = False) -> dict[str, Any]:
        page = self.archive.page(owner, before=before, limit=limit, include_descendants=include_descendants) if self.archive else {'agents': [], 'has_more': False, 'next_before': None}
        from .archive import subagent_summary
        page['agents'] = [subagent_summary(self._states[row['agent_id']]) if row['agent_id'] in self._states else self._without_handle(row) for row in page['agents']]
        return page

    def summary(self, agent_id: str) -> dict[str, Any] | None:
        from .archive import subagent_summary
        live = self._states.get(agent_id)
        if live is not None:
            return subagent_summary(live)
        row = self.archive.summary(agent_id) if self.archive is not None else None
        return self._without_handle(row) if row is not None else None

    @staticmethod
    def _without_handle(row: dict[str, Any]) -> dict[str, Any]:
        if row['status'] not in TERMINAL_STATES:
            row.update(status=INTERRUPTED, recovered=True, error=tr('ui.agent.handleReleased'), error_copy={'key': 'ui.agent.handleReleased'})
        if row.get('error_parts'):
            row['error'] = run_error_message({'parts': row['error_parts']})
        for field in ('error', 'archive_error'):
            copy = row.get(field + '_copy')
            if copy is not None:
                row[field] = tr(copy['key'], **copy.get('params', {}))
        return row

    def _save(self, state: AgentRun, *, required: bool = False) -> None:
        if self.archive is None:
            return
        try:
            self.archive.save(state)
            state.archive_error = None
            state.archive_error_copy = None
        except (OSError, sqlite3.Error) as exc:
            state.archive_error = tr('ui.agent.archiveFailed')
            state.archive_error_copy = {'key': 'ui.agent.archiveFailed'}
            if required:
                raise PersistenceError(state.archive_error, copy=state.archive_error_copy) from exc
            logger.error(tr('feedback.168f34544753'), exc)

    def list_states(self) -> list[AgentRun]:
        return list(self._states.values())

    def member_failures(self, scope: str, member: str) -> int:
        """当前内存覆盖同一持久记录，成功终态截断连续失败序列。"""
        rows = {row['id']: row for row in self.archive.member_attempts(scope, member)} if self.archive else {}
        for state in self.list_states():
            if state.scope == scope and (state.prompt_input or {}).get('member') == member:
                rows[state.id] = {'id': state.id, 'created_at': state.created_at, 'status': state.status}
        failures = 0
        for row in sorted(rows.values(), key=lambda row: (row['created_at'], row['id']), reverse=True):
            if row['status'] == 'completed':
                break
            if row['status'] in {'failed', 'interrupted'}:
                failures += 1
        return failures

    def state_for_session(self, session_id: str) -> AgentRun | None:
        """同一持久成员上下文多次运行时，优先返回最新的活动状态。"""
        matches = [state for state in reversed(list(self._states.values())) if state.session_id == session_id]
        return next((state for state in matches if not state.finished), matches[0] if matches else None)

    def delete_contexts(self, root_session_id: str) -> None:
        """停止根任务树后调用，不移除其他根任务的数据。"""
        if self.archive is None:
            return
        before = None
        while True:
            page = self.archive.page(root_session_id, before=before, include_descendants=True)
            if self.contexts is not None:
                for row in page['agents']:
                    if row.get('context_persisted') and row.get('child_session_id'):
                        self.contexts.delete(row['child_session_id'])
            if not page['has_more']:
                break
            before = page['next_before']
        self.archive.delete_tree(root_session_id)

    def _next_id(self, parent_session_id: str) -> str:
        prefix = (parent_session_id or "root").replace("ses_", "")[:8]
        return f"sub_{prefix}_{uuid.uuid4().hex}"

    # ==================================================================
    # 事件广播
    # ==================================================================
    async def _emit(self, event: str, payload: dict[str, Any]) -> None:
        if self.bus is None:
            return
        with contextlib.suppress(Exception):
            await self.bus.emit(event, payload)

    async def _emit_status(self, state: AgentRun) -> None:
        self._save(state)
        await self._emit("subagent/status", state.to_dict())

    # ==================================================================
    # 启动
    # ==================================================================
    def require_accepting(self, parent_session_id: str) -> AgentRun | None:
        """关闭中的范围及后代拒绝新派工，祖先归属复用运行目录的唯一规则。"""
        if not isinstance(parent_session_id, str) or not parent_session_id:
            raise ValueError('agent/invalid-parent-session')
        blocked = None in self._closing or parent_session_id in self._closing
        parent = None
        if not blocked:
            parent = self.runs.context_owner(parent_session_id)
            blocked = parent is not None and any(
                self.runs.controls(scope, parent) for scope in self._closing if scope is not None)
        if blocked:
            raise HarnessError(tr('subagent.parent_closing'), code='agent/parent-closing')
        return parent

    def spawn(
        self,
        *,
        prompt: str,
        description: str,
        parent_session_id: str,
        model: str | None = None,
        read_only: bool = False,
        workspace_cwd: str | Path | None = None,
        runner: str | None = None,
        context_session_id: str | None = None,
        source_call_id: str | None = None,
        scope: str | None = None,
        reasoning_effort: str | None = None,
        fork_turns: str | int = 'none',
        prompt_input: dict | None = None,
    ) -> AgentRun:
        """创建一个后台子代理任务并立即返回（不阻塞父代理）。

        :param model: 可选的模型覆盖。必须在 ``configure_agent`` 钩子之后
            应用——钩子（父配置继承）会整体覆写 ``agent.config``，先应用
            会被冲掉。
        """
        from ..core.language import current_language
        from ..collaboration.context_inheritance import inherited_context
        text, desc = validate_spawn_text(prompt, description)
        if fork_turns not in ('none', 'all') and (type(fork_turns) is not int or fork_turns <= 0):
            raise ValueError('agent/invalid-fork-turns')
        if fork_turns == 'all' and (model is not None or reasoning_effort is not None):
            raise ValueError('agent/full-context-model-override')
        if context_session_id is not None and fork_turns != 'none':
            raise ValueError('agent/context-already-inherited')
        if model is not None and (not isinstance(model, str) or not model.strip()):
            raise ValueError('agent/invalid-model')
        if runner and fork_turns != 'none':
            raise ValueError('agent/runner-context-unsupported')
        if reasoning_effort is not None and reasoning_effort not in ('none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra'):
            raise ValueError('agent/invalid-reasoning-effort')
        parent = self.require_accepting(parent_session_id)
        # 深度在创建会话之前判定：超限不留下半成品运行记录或孤立上下文。
        depth = (getattr(parent, 'depth', 0) or 0) + 1
        self._gate.check_depth(depth)
        inherited = inherited_context(self.session_for(parent_session_id), fork_turns)
        # 落盘成员恢复后也必须收紧后代权限，不能只依赖内存中的父 Agent。
        read_only = bool(read_only or parent is not None and parent.read_only)
        if runner is not None:
            if runner not in {'codex', 'claude', 'opencode'} or model or reasoning_effort or read_only:
                raise ValueError(tr('acp.team.invalid_member'))
            if self.external_runner is None:
                raise ValueError(tr('acp.team.unavailable'))
        desc = desc or text[:40]
        inherited_config = self.parent_config(parent_session_id) if self.parent_config else self.config
        if workspace_cwd is None and parent is not None and parent.workspace_cwd is not None:
            # 子智能体继续使用宿主授权目录，不能通过省略覆盖而放宽文件编辑范围。
            workspace_cwd = parent.workspace_cwd
        if workspace_cwd is not None:
            try:
                target = Path(workspace_cwd)
                if not target.is_absolute() or not target.is_dir():
                    raise ValueError
                workspace_cwd = str(target.resolve(strict=True))
            except (OSError, ValueError, TypeError) as exc:
                raise ValueError(tr('subagent.workspace.invalid')) from exc
        if self.before_spawn is not None:
            self.before_spawn(parent_session_id, workspace_cwd)
        agent_id = self._next_id(parent_session_id)
        language = self.parent_language(parent_session_id) if self.parent_language else current_language.get()
        factory = self.contexts.create if self.contexts is not None else create_session
        options = {} if self.contexts is not None else {'bus': self.bus}
        if context_session_id is not None:
            if self.contexts is None:
                raise ValueError(tr('subagent.context.invalid'))
            session = self.contexts.get(context_session_id)
            owner = self.state_for_session(context_session_id)
            if session.header.parent_session != parent_session_id or session.open_turn is not None or session.persistence_error or (owner is not None and not owner.settled):
                raise ValueError(tr('subagent.context.invalid'))
            if workspace_cwd is not None and Path(session.header.cwd).resolve() != Path(workspace_cwd):
                raise ValueError(tr('subagent.context.invalid'))
            session = self.contexts.reopen(context_session_id)
        else:
            with current_language.set(language):
                title = tr('subagent.conversation_title', description=desc)
            session = factory(cwd=workspace_cwd if workspace_cwd is not None else parent.session.header.cwd if parent and parent.session else self.cwd,
                          title=title, parent_session=parent_session_id, **options)
        state = AgentRun(
            id=agent_id,
            status=PENDING,
            description=desc,
            text=text,
            parent_session_id=parent_session_id,
            session=session,
            session_id=session.id,
            source_call_id=source_call_id,
            parent_run_id=parent.id if parent else None,
            root_session_id=(parent.root_session_id or parent.session_id) if parent else parent_session_id,
            context_persisted=self.contexts is not None and self.contexts.root is not None,
            model=None if runner else str(model).strip() if model else inherited_config.model if inherited_config else None,
            reasoning_effort=None if runner else reasoning_effort if reasoning_effort is not None else inherited_config.reasoning_effort if inherited_config else None,
            prompt_input={**(prompt_input or {}), 'fork_turns': fork_turns, **({'inheritance': {key: inherited[key] for key in ('source_session_id', 'after', 'through')}} if inherited else {})},
            read_only=read_only,
            workspace_cwd=workspace_cwd,
            runner=runner,
            # 未显式指定时继承父作用域，使整棵子树同属一个可见性边界。
            scope=scope if scope is not None else (parent.scope if parent else None),
            depth=depth,
            language=language,
        )
        state._inherited_context = inherited
        try:
            self._save(state, required=True)
        except Exception:
            if self.contexts is not None:
                if context_session_id is None:
                    with contextlib.suppress(Exception):
                        self.contexts.delete(session.id)
            raise
        if context_session_id is not None:
            for previous in self._states.values():
                if previous.session_id == context_session_id and previous.settled:
                    # 旧运行只保留审计元数据，避免重复持有逐轮增长的完整会话。
                    previous.session = None
                    previous.agent = None
                    previous.task = None
        self._states[agent_id] = state
        self._by_parent.setdefault(parent_session_id, []).append(agent_id)
        # create_task 保证拿到运行中的 asyncio 句柄，从而可以 cancel()
        state.task = asyncio.create_task(self._run(state, inherited_config), name=f"subagent-{agent_id}")
        # 协程首次调度前被取消时也要拥有同一次收尾，不能依赖尚未进入的 finally。
        state.task.add_done_callback(lambda done: self._finalization(state))
        return state

    async def _run(self, state: AgentRun, request_config: LlmCallConfig | None) -> None:
        """执行任务持有派工时的请求配置，状态与错误沿用父回合语言。"""
        from ..core.language import current_language
        token = current_language.set(state.language or current_language.get())
        # 全树限流：后代同样领取名额；阻塞观察期间由 _yield_slot 让渡，
        # 因此父代理等待子代理不会与本闸门互相等待。
        permit_token = None
        try:
            permit = await self._gate.acquire(state.id, state.depth)
            permit_token = _CURRENT_PERMIT.set(permit)
            try:
                self.require_accepting(state.session_id)
            except HarnessError as exc:
                if exc.code != 'agent/parent-closing':
                    raise
                raise asyncio.CancelledError from None
            await self._run_in_language(state, request_config)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._failed(state, exc, tr('feedback.ac087a2cc05d'))
        finally:
            try:
                await join_cleanup(self._finalization(state))
            finally:
                # 领取过程被取消时凭证仍在闸门队列中，按身份释放同样收敛。
                self._gate.release_run(state.id)
                if permit_token is not None:
                    _CURRENT_PERMIT.reset(permit_token)
                current_language.reset(token)

    async def _run_in_language(self, state: AgentRun, request_config: LlmCallConfig | None) -> None:
        """子代理主协程：跑完整个回合并收敛状态。"""
        state.status = RUNNING
        state.started_at = time.time()
        await self._emit_status(state)

        session = state.session or create_session(
            bus=self.bus,
            cwd=state.workspace_cwd or self.cwd,
            title=tr('subagent.conversation_title', description=state.description),
            parent_session=state.parent_session_id,
        )
        if self.request_traces is not None:
            session.request_traces = self.request_traces
        state.session = session
        state.session_id = session.id
        agent = AgentLoop(
            self.adapter,
            self.tools,
            self.prompts,
            skills=self.skills,
            config=request_config,
            bus=SubagentEventBus(self.bus, state.id, state.parent_session_id),
            max_steps=self.max_steps,
        )
        inherited = state.__dict__.pop('_inherited_context', None)
        if inherited is not None:
            await session.append('developer/message', {'source': 'agent-inheritance',
                'text': tr('agent.inheritance.context_notice') + json.dumps(inherited, ensure_ascii=False)}, surface_op=AppendOp())
        state.agent = agent
        if self.configure_agent is not None:
            self.configure_agent(state, agent)
        agent.run_id = state.id
        if state.workspace_cwd is not None:
            # 宿主选定目录在配置继承之后确认，不能被父会话覆盖。
            session.header = SessionHeader.from_dict({**session.header.to_dict(), "cwd": state.workspace_cwd})
        agent.workspace_write_root = state.workspace_cwd
        agent.task_read_only = bool(agent.task_read_only or state.read_only)
        state.read_only = agent.task_read_only
        if state.model:
            # 钩子（父配置继承）可能整体覆写过 agent.config，成员级
            # 模型覆盖必须最后应用才能生效。
            agent.config = dataclasses_replace(agent.config, model=state.model)

        if state.reasoning_effort is not None:
            agent.config = dataclasses_replace(agent.config, reasoning_effort=state.reasoning_effort)
        if not state.runner:
            state.model = agent.config.model
            state.reasoning_effort = agent.config.reasoning_effort
        self._save(state, required=True)
        # 消息在步骤边界提交到模型上下文；最后一步之后收到消息时，
        # 延续原成员上下文处理，消息正文只保存在会话事件中。
        text = state.text
        source = 'subagent'
        timeout = getattr(state, '_timeout_seconds', None)
        if timeout is None:
            timeout = self.job_timeout
        deadline = time.monotonic() + timeout if timeout is not None else None
        while True:
            execution = self.external_runner(state, agent) if state.runner else agent.prompt(session, text, source=source)
            if deadline is None:
                result = await execution
            else:
                try:
                    result = await asyncio.wait_for(execution, max(0.001, deadline - time.monotonic()))
                except TimeoutError as exc:
                    # asyncio 抛出的是无正文超时；换成可核验的时限失败，
                    # 不把空消息当成成员错误呈现，也不重放已开始的执行。
                    raise TimeoutError(tr('runtime.validation.e121bb0c13e1')) from exc
            if deadline is not None and time.monotonic() >= deadline and result.reason == 'aborted':
                raise TimeoutError(tr('runtime.validation.e121bb0c13e1'))
            if result.text or result.reason == 'completed':
                state.result = result.to_dict()
            state.tool_calls += len(result.tool_calls or [])
            self._save(state)
            outcome, error = turn_outcome(result)
            if outcome != COMPLETED:
                break
            if state.runner:
                break
            if not session.agent_messages.pending() or agent.cancelled:
                break
            text = tr('agent.message.continue')
            source = 'agent-message'
            state.status = RUNNING
            await self._emit('subagent/message', {
                'agent_id': state.id, 'parent_session_id': state.parent_session_id,
                'status': RUNNING, 'result': '', 'error': None,
                'pending_messages': len(session.agent_messages.pending()),
            })
        state.status = outcome
        state.error = error

    @staticmethod
    def _failed(state: AgentRun, exc: Exception, message: str) -> None:
        """执行、会话关闭和交付错误使用同一个失败投影，不吞掉收尾故障。"""
        from copy import deepcopy
        previous = deepcopy(state.error)
        error = {'code': 'run/error'}
        if previous:
            error['causes'] = [previous, {'type': type(exc).__name__, 'message': str(exc)}]
        parts = previous.get('parts') if previous else None
        if previous and not parts:
            parts = [{'label': previous.get('code', 'run/error'),
                      'message': run_error_copy(previous) or previous.get('message') or previous.get('code') or ''}]
        details = getattr(exc, 'details', {})
        copy = details.get('copy') if isinstance(details, dict) else None
        error['parts'] = [*(parts or []), {'label': type(exc).__name__, 'message': copy or str(exc)}]
        error['message'] = run_error_message(error)
        state.status = FAILED
        state.error = error
        logger.exception(message, state.id)

    def _finalization(self, state: AgentRun) -> asyncio.Task:
        """同一运行只拥有一个收尾任务，所有等待者都汇合到这个句柄。"""
        task = state.finalization_task
        if task is None:
            from ..core.language import current_language
            context = contextvars.copy_context()
            context.run(current_language.set, state.language or current_language.get())
            task = asyncio.create_task(self._finalize(state), name=f'subagent-finalize-{state.id}', context=context)
            state.finalization_task = task
        return task

    async def _finalize(self, state: AgentRun) -> None:
        """关闭上下文、保存交付并发布终态；取消请求不能覆盖交付失败。"""
        if not state.finished:
            state.status = INTERRUPTED
            if not state.error or state.error.get('code') != 'run/cancelled':
                state.error = {'code': 'run/cancelled', 'message': tr('ui.agent.interrupted'), 'copy': {'key': 'ui.agent.interrupted'}}
        reason = getattr(state, '_interrupt_reason', None)
        if state.status == INTERRUPTED and reason is not None:
            state.error = {'code': 'run/cancelled', 'message': reason}
            copy = getattr(state, '_interrupt_reason_copy', None)
            if copy is not None:
                state.error['copy'] = copy
        if state.session is not None:
            try:
                await state.session.close()
            except Exception as exc:
                self._failed(state, exc, tr('log.14ba69656e18'))
        state.finished_at = time.time()
        observer = getattr(state, '_on_finished', None)
        if observer is not None:
            try:
                await observer(state)
            except Exception as exc:
                self._failed(state, exc, tr('log.6811ea57a92e'))
        await self._emit_status(state)
        await self._emit('subagent/message', {
            'agent_id': state.id, 'parent_session_id': state.parent_session_id,
            'status': state.status, 'result': state.output, 'error': state.error_message,
        })

    # ==================================================================
    # 等待 / 消息 / 中断
    # ==================================================================
    @staticmethod
    def _wait_errors(state: AgentRun) -> list[BaseException]:
        """同步核对已结束句柄，避免观察任务归并后终态变化漏掉原失败。"""
        errors = []
        for future in (state.task, state.finalization_task):
            if future is None or not future.done() or future is state.task and future.cancelled():
                continue
            try:
                future.result()
            except BaseException as exc:
                if not any(error is exc for error in errors):
                    errors.append(exc)
        return errors

    @contextlib.asynccontextmanager
    async def _yield_slot(self):
        """阻塞观察期间让出名额，使后代能被准入。

        只有当前协程确实持有名额时才让渡；召回被取消时凭证仍留在闸门队列中，
        由运行收尾按身份释放，因此名额既不丢失也不重复计数。
        """
        permit = _CURRENT_PERMIT.get()
        if not self._gate.lend(permit):
            yield
            return
        try:
            yield
        finally:
            with contextlib.suppress(asyncio.CancelledError):
                await self._gate.recall(permit)

    async def wait(self, agent_id: str, timeout: float | None = None) -> AgentRun:
        """等待子代理进入终态。超时只停止等待，不杀死子代理。

        受管代理在此阻塞期间让出并发名额，其后代因此可以被准入；
        非法超时在让渡之前就被拒绝，不会留下已让出却未召回的名额。
        """
        wait_for = validate_wait_timeout(DEFAULT_AGENT_TIMEOUT if timeout is None else timeout)
        async with self._yield_slot():
            return await self._observe(agent_id, wait_for)

    async def _observe(self, agent_id: str, wait_for: float) -> AgentRun:
        """实际观察一次运行到终态或超时；名额让渡由调用方负责。"""
        state = self.get(agent_id)
        if state is None:
            raise KeyError(agent_id)
        task = state.task
        deadline = asyncio.get_running_loop().time() + wait_for

        async def observed(future):
            if not future.done():
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0 or not (await asyncio.wait((future,), timeout=remaining))[0]:
                    return False
            return True

        if task is not None:
            if not await observed(task):
                raise_cleanup_errors(self._wait_errors(state))
                return state
            # 执行被正常中断后仍须观察所属收尾；等待方取消不会传给执行任务。
            if not task.cancelled():
                task.result()
        finalization = (self._finalization(state) if task is not None and wait_for > 0
                        else state.finalization_task)
        if finalization is not None and await observed(finalization):
            # 收尾自身的取消和 TimeoutError 是原始失败，不是本次观察超时。
            finalization.result()
        raise_cleanup_errors(self._wait_errors(state))
        return state

    async def wait_many(self, agent_ids: list[str], timeout: float | None = None) -> list[AgentRun]:
        """等待一组子代理；超时只返回当前快照，不取消任何成员。"""
        agent_ids = validate_wait_ids(agent_ids)
        wait_for = validate_wait_timeout(DEFAULT_AGENT_TIMEOUT if timeout is None else timeout)
        async with self._yield_slot():
            return await self._observe_many(agent_ids, wait_for)

    async def _observe_many(self, agent_ids: list[str], wait_for: float) -> list[AgentRun]:
        """批量观察；逐个成员仍委派公开 :meth:`wait`，宿主可据此拦截单次观察。

        名额只在本方法入口让渡一次：内层 :meth:`wait` 再次进入
        :meth:`_yield_slot` 时凭证已处于让渡态，不会重复记账。
        """
        states = []
        for agent_id in agent_ids:
            state = self.get(agent_id)
            if state is None:
                raise KeyError(agent_id)
            states.append(state)
        deadline = asyncio.get_running_loop().time() + wait_for

        async def observe(agent_id):
            remaining = max(0.0, deadline - asyncio.get_running_loop().time())
            return await self.wait(agent_id, timeout=remaining)

        waits = [asyncio.create_task(observe(agent_id),
                                     name=f'subagent-wait-many-{agent_id}') for agent_id in agent_ids]
        joined = asyncio.gather(*waits, return_exceptions=True)
        cancelled = None
        cancelled_observers = set()
        try:
            await asyncio.wait((joined,))
        except asyncio.CancelledError as exc:
            cancelled = exc
            # 只取消本次观察任务；成员执行及其唯一收尾仍由原管理器拥有。
            for task in waits:
                if not task.done() and not task.cancelling():
                    cancelled_observers.add(task)
                    task.cancel()
        try:
            results = await join_cleanup(joined)
        except asyncio.CancelledError as exc:
            cancelled = cancelled or exc
            results = joined.result()
        errors = [result for task, result in zip(waits, results) if isinstance(result, BaseException)
                  and not (isinstance(result, asyncio.CancelledError) and task in cancelled_observers)]
        for state, task, result in zip(states, waits, results):
            if not isinstance(result, BaseException) or task in cancelled_observers:
                for error in self._wait_errors(state):
                    if not any(original is error for original in errors):
                        errors.append(error)
        raise_cleanup_errors([*errors, *([cancelled] if cancelled is not None else [])])
        return states

    def session_for(self, session_id: str):
        """主会话与子上下文共用实际 Session，不创建虚拟父 Agent 对象。"""
        state = self.state_for_session(session_id)
        if state is not None and state.session is not None:
            return state.session
        if self.session_resolver is not None:
            return self.session_resolver(session_id)
        return None

    def visible_to(self, state: AgentRun, session_id: str) -> bool:
        """身份、根会话和团队作用域同时满足才可观察或投递。"""
        owner = self.state_for_session(session_id)
        root = (owner.root_session_id or owner.parent_session_id) if owner else session_id
        scope = owner.scope if owner else self.scope_resolver(session_id) if self.scope_resolver else None
        return (state.session_id != session_id
                and (state.root_session_id or state.parent_session_id) == root
                and (scope is None or state.scope == scope))

    def target_for(self, agent_id: str, session_id: str, *, interrupt=False) -> AgentRun:
        """统一目标授权；取消只允许管理自己委派的后代，失败不泄漏其他身份。"""
        state = self.get(agent_id)
        if state is None or not self.visible_to(state, session_id):
            raise KeyError(agent_id)
        if interrupt:
            try:
                allowed = self.runs.controls(session_id, state)
            except HarnessError as exc:
                if exc.code != 'run/invalid-lineage':
                    raise
                allowed = False
            if not allowed:
                raise KeyError(agent_id)
        return state

    async def send_message(self, agent_id: str, message: str, *, sender_session_id: str, call_id: str, turn: int) -> dict[str, Any]:
        """投递到实际会话并返回提交回执；parent 只表示当前委派者。"""
        validate_message_text(message)
        sender = self.runs.current(sender_session_id) or self.state_for_session(sender_session_id)
        state = self.target_for(agent_id, sender_session_id) if agent_id != 'parent' else None
        if agent_id == 'parent':
            if sender is None or sender.parent_session_id is None or sender.finished:
                raise KeyError(agent_id)
            parent = self.runs.get(sender.parent_run_id) if sender.parent_run_id else None
            if parent is None:
                raise ValueError(tr('agent.message.target_inactive'))
            if (parent.session_id != sender.parent_session_id
                    or (parent.root_session_id or parent.session_id) != sender.root_session_id):
                raise KeyError(agent_id)
            recipient = self.session_for(sender.parent_session_id)
            state = parent
            recipient_run_id = parent.id
        else:
            if state is None:
                raise KeyError(agent_id)
            recipient_run_id = state.id
            recipient = state.session or self.session_for(state.session_id)
            if recipient is None and state.context_persisted and self.contexts is not None:
                recipient = self.contexts.get(state.session_id)
        if recipient is None:
            raise KeyError(agent_id)
        text = message.strip()
        identifier = 'msg_' + hashlib.sha256(json.dumps(
            [sender_session_id, turn, call_id, recipient.id]).encode()).hexdigest()
        envelope = {'message_id': identifier, 'sender_session_id': sender_session_id,
                    'sender_agent_id': sender.id if sender else None,
                    'recipient_session_id': recipient.id, 'recipient_run_id': recipient_run_id, 'text': text,
                    'scope': sender.scope if sender is not None and sender.parent_session_id is not None else state.scope if state else None}
        # 未知写结果的同一调用可以查回既有回执，终态目标不能接收新消息。
        if recipient.agent_messages.receipt(identifier) is None:
            if state is not None and (not self.runs.active(state) or state.runner):
                raise ValueError(tr('agent.message.target_inactive'))
        receipt = await recipient.agent_messages.accept(envelope)
        if state is not None and state.parent_session_id is not None:
            self._save(state)
        return receipt

    async def followup_task(self, agent_id: str, message: str, *, sender_session_id: str,
                            call_id: str, turn: int, start=None):
        """运行中投递；空闲时复用原会话启动新运行，幂等重试只读回既有结果。"""
        validate_message_text(message)
        text = message.strip()
        async with self._followup_lock:
            def target(identifier):
                # 跨回合只允许原委派者续接已落定成员；当前团队配置由启动回调复核。
                state = self.get(identifier)
                if (start is not None and state is not None and state.settled
                        and state.parent_session_id == sender_session_id
                        and state.scope is not None and (state.prompt_input or {}).get('member')):
                    return state
                return self.target_for(identifier, sender_session_id)

            previous = target(agent_id)
            if previous.parent_session_id != sender_session_id:
                raise KeyError(agent_id)
            key = hashlib.sha256(json.dumps([sender_session_id, turn, call_id, agent_id]).encode()).hexdigest()
            fingerprint = hashlib.sha256(text.encode()).hexdigest()
            identifier = self.archive.followup(key) if self.archive is not None else next(
                (state.id for state in self._states.values() if (state.prompt_input or {}).get('followup', {}).get('key') == key), None)
            if identifier is not None:
                existing = self.get(identifier)
                if existing.prompt_input['followup']['fingerprint'] != fingerprint:
                    raise ValueError('agent/followup-conflict')
                return {'run': existing.to_dict(), 'started': True, 'reused': True}
            latest = self.state_for_session(previous.session_id)
            if (latest is None or latest.finished) and self.archive is not None:
                latest_id = self.archive.latest_for_context(previous.session_id)
                latest = self.get(latest_id) if latest_id else latest
            if latest is not None and latest.id != previous.id:
                previous = target(latest.id)
                if previous.parent_session_id != sender_session_id:
                    raise KeyError(agent_id)
            if previous.runner:
                raise ValueError('agent/runner-followup-unsupported')
            session = previous.session or self.session_for(previous.session_id)
            if session is None and previous.context_persisted and self.contexts is not None:
                session = self.contexts.get(previous.session_id)
            message_id = 'msg_' + hashlib.sha256(json.dumps(
                [sender_session_id, turn, 'followup:' + call_id, previous.session_id]).encode()).hexdigest()
            receipt = session.agent_messages.receipt(message_id) if session is not None else None
            if receipt is not None:
                if session.agent_messages.records[message_id].data['text'] != text:
                    raise ValueError('agent/followup-conflict')
                acknowledged = self.get(session.agent_messages.records[message_id].data['recipient_run_id']) or previous
                return {'run': acknowledged.to_dict(), 'started': False, 'reused': True, 'receipt': receipt}
            if not previous.finished and previous.task is not None and not previous.task.done():
                receipt = await self.send_message(previous.id, text, sender_session_id=sender_session_id,
                                                  call_id='followup:' + call_id, turn=turn)
                return {'run': previous.to_dict(), 'started': False, 'reused': False, 'receipt': receipt}
            if not previous.settled:
                raise ValueError('agent/member-settling')
            if not previous.finished or self.contexts is None or not previous.context_persisted:
                raise ValueError('agent/context-unavailable')
            metadata = {'followup': {'key': key, 'fingerprint': fingerprint, 'source_run_id': previous.id}}
            if start is not None:
                state = await start(previous, text, metadata)
            else:
                if previous.scope is not None:
                    raise ValueError('agent/team-followup-unavailable')
                state = self.spawn(prompt=text, description=previous.description,
                    parent_session_id=sender_session_id, context_session_id=previous.session_id,
                    model=previous.model, reasoning_effort=previous.reasoning_effort,
                    read_only=previous.read_only, workspace_cwd=previous.workspace_cwd,
                    runner=previous.runner, source_call_id=call_id, scope=previous.scope, prompt_input=metadata)
            return {'run': state.to_dict(), 'started': True, 'reused': False}

    async def interrupt(self, agent_id: str, reason: str | None = None) -> AgentRun:
        """中断运行中的子代理。"""
        state = self.get(agent_id)
        if state is None:
            raise KeyError(agent_id)
        if reason is None and not hasattr(state, '_interrupt_reason'):
            state._interrupt_reason_copy = {'key': 'ui.agent.parentRequested'}
        await self._stop_record(state, tr('ui.agent.parentRequested') if reason is None else reason)
        return state

    async def _stop_record(self, state: AgentRun, reason: str) -> int:
        """按唯一运行对象请求停止，返回实际关闭的后代数。"""
        # 取消意图必须在第一次等待前生效，尚未调度的成员不能抢先发出模型请求。
        if not state.finished:
            if not hasattr(state, '_interrupt_reason'):
                state._interrupt_reason = reason
            if state.agent is not None:
                with contextlib.suppress(Exception):
                    state.agent.cancel(reason)
            if state.task is not None and not state.task.done():
                state.task.cancel()
        return await self._close_branch(state.session_id, lambda: self._finish_interrupt(state))

    async def _finish_interrupt(self, state: AgentRun) -> int:
        """先等待后代，再等待当前执行及交付；不改写父代理已确认的结果。"""
        # 父代理可能已经完成，而独立运行的子代理仍在执行。
        # 停止该分支，不改写父代理已经确认的结果。
        cleaned = await self.shutdown(state.session_id) if state.session_id is not None else 0
        task = state.task
        if state.finished and task is None:
            return cleaned
        try:
            if task is not None:
                await join_cleanup(asyncio.gather(task, return_exceptions=True))
        finally:
            await join_cleanup(self._finalization(state))
        return cleaned

    # ==================================================================
    # 清理
    # ==================================================================
    async def _close_branch(self, scope: str | None, operation):
        """关闭动作先占有范围，再等待完整操作；并发关闭不会提前重新开放。"""
        self._closing[scope] = self._closing.get(scope, 0) + 1
        try:
            return await join_cleanup(asyncio.create_task(operation(), name=f'subagent-close-{scope}'))
        finally:
            remaining = self._closing[scope] - 1
            if remaining:
                self._closing[scope] = remaining
            else:
                del self._closing[scope]

    async def shutdown(self, parent_session_id: str | None = None) -> int:
        """中断并清理子代理，防止事件循环泄漏。返回清理条数。"""
        if parent_session_id is not None and (not isinstance(parent_session_id, str) or not parent_session_id):
            raise ValueError('agent/invalid-parent-session')
        return await self._close_branch(parent_session_id, lambda: self._shutdown(parent_session_id))

    async def _shutdown(self, parent_session_id: str | None) -> int:
        """入口已经封住后，按原父索引等待已接受成员，保留其他分支。"""
        ids = list(self._by_parent.get(parent_session_id, [])) if parent_session_id is not None else list(self._states)
        cleaned = 0
        errors: list[BaseException] = []
        for agent_id in ids:
            state = self._states.get(agent_id)
            if state is None:
                continue
            try:
                if not hasattr(state, '_interrupt_reason'):
                    state._interrupt_reason_copy = {'key': 'ui.agent.sessionClosed'}
                cleaned += await self._stop_record(state, tr('ui.agent.sessionClosed'))
            except BaseException as exc:
                errors.append(exc)
                continue
            if self._states.pop(agent_id, None) is state:
                cleaned += 1
        # 只移除已经收尾成功的身份，失败分支继续保留供显式重试。
        for owner, members in list(self._by_parent.items()):
            remaining = [identifier for identifier in members if identifier in self._states]
            if remaining:
                self._by_parent[owner] = remaining
            else:
                self._by_parent.pop(owner, None)
        raise_cleanup_errors(errors)
        return cleaned


__all__ = [
    "SubagentManager",
    "COMPLETED",
    "FAILED",
    "INTERRUPTED",
    "PENDING",
    "RUNNING",
    "TERMINAL_STATES",
]
