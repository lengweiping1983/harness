"""具体操作审批：只复用相同工具、参数与目录的短期授权。"""
from __future__ import annotations

from harness.i18n import tr

import json
import asyncio
import time
import uuid
import math
from pathlib import Path
from dataclasses import dataclass
from collections.abc import Callable, Mapping
from typing import Any

from .runtime import operation_fingerprint
from .argument_sensitivity import code_has_sensitive_reference, shell_has_sensitive_reference
from ..llm.types import SystemMessage, UserMessage, LlmCallConfig
from ..core.errors import HarnessError, SessionNotFoundError
from ..core.jsonutil import to_jsonable
from ..core.cleanup import join_cleanup, raise_cleanup_errors
from ..core.operation_lock import OperationLock
from ..core.redaction import (
    is_sensitive_path as _is_sensitive_path,
    contains_sensitive_value as _contains_sensitive_value,
    redact_path as _redact_path,
    redact_sensitive as _redact_sensitive,
)

# 基础敏感判定与脱敏只有 core.redaction 一处实现；这里按本模块既有私有名再导出，
# 使审批展示、审阅证据与递归参数检查继续共用同一份规则。
# 路径与明文凭据判定的对外入口在 core.redaction 与 app.plan_mode，不在此重复暴露。


#: 审阅证据里保留的用户消息条数上限；超出部分以计数与截断声明如实报告。
MAX_REVIEW_USER_MESSAGES = 8

_PUBLIC_TOOL_ARGUMENTS = {
    name: frozenset({'snapshot_token'})
    for name in ('todo_read', 'todo_write', 'todo_update', 'todo_bind')
}


def _argument_reference_checks(semantics: Mapping[str, str], arguments: Any) -> dict[str, Callable[[str], bool]]:
    """仅由可信用途选择引用检查，秘密扫描与嵌套检查仍由核心入口执行。"""
    checks = {
        'text': lambda value: False,
        'path': _is_sensitive_path,
        'shell': shell_has_sensitive_reference,
        'code': code_has_sensitive_reference,
    }
    if isinstance(arguments, dict) and arguments.get('language') in ('bash', 'sh', 'powershell'):
        checks['code'] = shell_has_sensitive_reference
    return {key: checks[semantic] for key, semantic in semantics.items()
            if isinstance(semantic, str) and semantic in checks}


class _PolicyChanged(Exception):
    """撤回尚未生效的审阅，按新的会话权限重新判断。"""


@dataclass(frozen=True)
class _PolicyCache:
    """保存已扫描事件位置，后续只检查新增事件或恢复后的新历史。"""

    session: Any
    seq: int
    policy: str | None
    observed_seq: int
    generation: int


class PermissionPolicy:
    def __init__(self, harness: Any) -> None:
        self.harness = harness
        self.grants: dict[tuple[str, str], dict[str, Any]] = {}
        self._policies: dict[str, _PolicyCache] = {}
        self._changes: dict[str, asyncio.Event] = {}
        self._locks: dict[str, OperationLock] = {}

    def scope_for(self, session_id: str) -> str:
        """只沿宿主委派记录继承；历史分叉的父字段不授予权限。"""
        seen: set[str] = set()
        while session_id not in seen:
            seen.add(session_id)
            state = self.harness.subagents.state_for_session(session_id)
            if state is None:
                try:
                    # 正式聊天即使来源于历史分叉，也拥有独立权限作用域。
                    self.harness.get_session(session_id)
                    return session_id
                except SessionNotFoundError:
                    # 私有成员上下文可跨重启恢复，只信任同一运行存储的归属。
                    state = self.harness.subagents.runs.context_owner(session_id)
                    if state is None:
                        raise
            if state.session_id != session_id or not state.parent_session_id:
                break
            session_id = state.parent_session_id
        raise HarnessError(tr('python.permissions.1fbe7fb9b27c'), code="permissions/invalid-scope")

    def snapshot(self, session_id: str) -> dict[str, Any]:
        scope = self.scope_for(session_id)
        session = self.harness.get_session(scope)
        previous = self._policies.get(scope)
        same_history = previous is not None and previous.session is session and previous.generation == session.history_generation
        seq, override, observed = (previous.seq, previous.policy, previous.observed_seq) if same_history else (-1, None, -1)
        latest = session.latest('note')
        end = latest.seq if latest is not None else -1
        for index in range(observed + 1, end + 1):
            event = session.event_at(index)
            if event.type == 'note' and event.data.get('kind') == 'permission_policy':
                seq, override = event.seq, event.data.get('policy')
                if override is not None and (not isinstance(override, str) or override not in {'request_approval', 'auto_review', 'full_access', 'read_only'}):
                    raise HarnessError(tr('python.permissions.4cff88af67df'), code='permissions/invalid-policy')
        self._policies[scope] = _PolicyCache(session, seq, override, end, session.history_generation)
        if previous is not None and (not same_history or previous.seq != seq):
            self.clear_grants(scope)
            changed = self._changes.pop(scope, None)
            if changed is not None:
                changed.set()
        policy = override if override is not None else self.harness.config.permission_policy
        version = operation_fingerprint("permission-policy", {"policy": policy, "seq": seq}, scope)
        return {"session_id": session_id, "scope": scope, "permission_policy": policy,
                "default_policy": self.harness.config.permission_policy, "inherited": override is None,
                "version": version}

    async def set_policy(self, session_id: str, policy: str | None, *, expected_version: str) -> dict[str, Any]:
        async with self._locks.setdefault(session_id, OperationLock()):
            return await self._set_policy(session_id, policy, expected_version=expected_version)

    async def _set_policy(self, session_id: str, policy: str | None, *, expected_version: str) -> dict[str, Any]:
        if policy is not None and (not isinstance(policy, str) or policy not in {"request_approval", "auto_review", "full_access", "read_only"}):
            raise HarnessError(tr('python.permissions.15cfede69a40'), code="permissions/invalid-policy")
        current = self.snapshot(session_id)
        scope = current["scope"]
        if scope != session_id:
            raise HarnessError(tr('python.permissions.aac5bdee60fc'), code="permissions/inherited")
        if expected_version != current["version"]:
            raise HarnessError(tr('python.permissions.3ef9c8c7c01a'), code="permissions/changed", **current)
        cached = self._policies[scope]
        if cached.policy == policy:
            return current
        # 广播等待失败也可能已提交；只核对实际事件，不重写或补偿。
        try:
            await cached.session.append('note', {'kind': 'permission_policy', 'policy': policy})
        except BaseException as primary:
            try:
                self.snapshot(session_id)
            except BaseException as failure:
                raise_cleanup_errors([primary, failure])
            raise
        self.snapshot(session_id)
        await self.publish(scope)
        return self.snapshot(session_id)

    async def publish(self, scope: str | None = None, *, invalidate: bool = False) -> None:
        """先提交，再向各会话自己的通道发布生效状态。"""
        states = []
        for session in self.harness.sessions.loaded_sessions():
            try:
                state = self.snapshot(session.id)
            except HarnessError:
                continue  # 损坏或已删除的父会话不能影响其他会话通道。
            if scope is None or state["scope"] == scope:
                states.append(state)
        if invalidate:
            roots = {state["scope"] for state in states if state["inherited"]}
            self.clear_grants(*roots)
            for root in roots:
                changed = self._changes.pop(root, None)
                if changed is not None:
                    changed.set()
        for state in states:
            await self.harness.bus.emit("permission_policy", {"session_id": state["session_id"], "permissions": state})

    def clear_grants(self, *scopes: str) -> None:
        """通过一次有界遍历使权限作用域和元数据失效。"""
        roots = set(scopes)
        self.grants = {key: value for key, value in self.grants.items() if key[0] not in roots}

    def assert_idle(self, session_id: str) -> None:
        """拒绝在策略写入或等待者仍存在时替换锁与授权状态。"""
        lock = self._locks.get(session_id)
        if lock is not None and lock.busy:
            raise HarnessError(tr('ui.collaboration.error.permissions/busy'), code='permissions/busy')

    def forget(self, session_id: str) -> None:
        self.assert_idle(session_id)
        self._policies.pop(session_id, None)
        self._locks.pop(session_id, None)
        self.clear_grants(session_id)
        changed = self._changes.pop(session_id, None)
        if changed is not None:
            changed.set()

    async def _await_current(self, operation: Any, scope: str, version: str) -> Any:
        changed = self._changes.setdefault(scope, asyncio.Event())
        work = asyncio.create_task(operation)
        watch = asyncio.create_task(changed.wait())
        primary: BaseException | None = None
        try:
            await asyncio.wait({work, watch}, return_when=asyncio.FIRST_COMPLETED)
            if changed.is_set() or self.snapshot(scope)["version"] != version:
                raise _PolicyChanged()
            return await work
        except BaseException as exc:
            primary = exc
            raise
        finally:
            for task in (work, watch):
                if not task.done():
                    task.cancel()
            # 清理只取消一次；后续观察者取消不能打断工作自身的 finally。
            cleanup = asyncio.gather(work, watch, return_exceptions=True)
            try:
                await join_cleanup(cleanup)
            finally:
                if cleanup.done() and not cleanup.cancelled():
                    failures = [result for result in cleanup.result()
                                if isinstance(result, BaseException)
                                and not isinstance(result, asyncio.CancelledError) and result is not primary]
                    if failures:
                        raise_cleanup_errors(([primary] if primary is not None else []) + failures)

    @staticmethod
    def _tool_authority(tool: Any) -> tuple[Any, Any, str]:
        """冻结工具实体、执行入口和公开契约，不能靠名称复用授权。"""
        contract = to_jsonable({'schema': tool.schema.to_dict(), 'effect': tool.effect,
            'dangerous': tool.dangerous, 'approval_required': tool.approval_required,
            'discovery_namespace': tool.discovery_namespace,
            'argument_semantics': dict(tool.argument_semantics)})
        return tool, tool.execute, operation_fingerprint('tool-authority', contract, '')

    def _same_authority(self, authority: tuple[Any, Any, str], tool: Any) -> bool:
        current = self._tool_authority(tool)
        return authority[0] is current[0] and authority[1] is current[1] and authority[2] == current[2]

    def _active_grant(self, tool: Any, key: tuple[str, str]) -> dict[str, Any] | None:
        row = self.grants.get(key)
        if row is not None and (row['_expires_at'] <= time.monotonic()
                                or not self._same_authority(row['_authority'], tool)):
            self.grants.pop(key, None)
            return None
        return row

    async def guard(self, tool: Any, args: Any, ctx: Any) -> str | None:
        while True:
            checks_start = len(ctx.admission_checks)
            try:
                state = self.snapshot(ctx.session_id)
                fingerprint = operation_fingerprint(tool.schema.name, args, ctx.cwd)
                authority = self._tool_authority(tool)
                reason = await self._guard(tool, args, ctx)
                if reason:
                    return reason
                if self.snapshot(ctx.session_id)['version'] != state['version']:
                    del ctx.admission_checks[checks_start:]
                    continue
                if not self._same_authority(authority, tool):
                    return tr('python.permissions.e6e4e44a4b91')
                if operation_fingerprint(tool.schema.name, args, ctx.cwd) != fingerprint:
                    return tr('python.permissions.a7731e1d810e')
                grant_key = (state['scope'], fingerprint)
                grant = self._active_grant(tool, grant_key)
                def current_authority() -> str | None:
                    if self.snapshot(ctx.session_id)['version'] != state['version']:
                        return tr('python.permissions.aa8a081ba837')
                    if operation_fingerprint(tool.schema.name, args, ctx.cwd) != fingerprint or not self._same_authority(authority, tool):
                        return tr('python.permissions.e6e4e44a4b91')
                    if grant is not None and (self.grants.get(grant_key) is not grant or grant['_expires_at'] <= time.monotonic()):
                        return tr('python.permissions.b83a2056b623')
                    return None
                ctx.admission_checks.append(current_authority)
                return None
            except _PolicyChanged:
                del ctx.admission_checks[checks_start:]
                continue

    async def run_external_agent(self, operation: Any, state: dict[str, Any]) -> Any:
        """权限版本变更时取消整次外部执行，等待进程清理完成后返回。"""
        from ..core.errors import ToolDeniedError
        try:
            return await self._await_current(operation, state['scope'], state['version'])
        except _PolicyChanged:
            raise ToolDeniedError(tr('acp.tool.policy_changed')) from None

    def restrict_external_agent(self, tool: Any, args: Any, ctx: Any) -> str | None:
        """无系统沙箱的独立进程不能作为只读工具启动。"""
        if tool.schema.name == 'delegate_agent' and self.snapshot(ctx.session_id)['permission_policy'] == 'read_only':
            return tr('acp.tool.read_only')
        return None

    async def external_agent_permission(self, ctx: Any, runner: str, params: dict[str, Any]) -> str | None:
        """外部请求只授予本次操作，不让 Agent 建立宿主不可撤销的长期授权。"""
        state = self.snapshot(ctx.session_id)
        raw_options = params.get('options', [])
        if not isinstance(raw_options, list) or any(not isinstance(item, dict) for item in raw_options):
            return None
        ids = [item.get('optionId') for item in raw_options]
        if any(not isinstance(value, str) or not value for value in ids) or len(set(ids)) != len(ids):
            return None
        operation = params.get('toolCall', {})
        if not isinstance(operation, dict):
            return None
        sensitive = _contains_sensitive_value(operation)
        detail = json.dumps(_redact_sensitive(operation), ensure_ascii=False)
        if len(detail) > 60000:
            return None
        options = [item for item in raw_options if isinstance(item, dict)
                   and item.get('kind') in {'allow_once', 'reject_once'}
                   and isinstance(item.get('optionId'), str) and item['optionId']]
        if not options or len(options) > 16 or state['permission_policy'] == 'read_only':
            return None
        selected = None
        if state['permission_policy'] == 'full_access':
            if not sensitive:
                selected = next((item['optionId'] for item in options if item['kind'] == 'allow_once'), None)
        elif self.harness.config.enable_interaction:
            labels = [f"{index + 1}. {str(item.get('name', item['optionId']))[:150]}" for index, item in enumerate(options)]
            agent = self.harness._agents.get(ctx.session_id)
            if agent is None:
                agent = next((state.agent for state in self.harness.subagents.list_states()
                              if state.session_id == ctx.session_id), None)
            if agent:
                agent.status.status = 'waiting_approval'
            try:
                answers = await self._await_current(self.harness.questions.ask(
                    session_id=state['scope'], call_id=ctx.call_id, timeout=600, max_question_chars=64000,
                    questions=[{'id': 'external_permission', 'header': runner,
                                'question': tr('acp.tool.permission', agent=runner, cwd=_redact_path(str(ctx.cwd)), detail=detail),
                                'options': [{'id': item['optionId'], 'label': label} for item, label in zip(options, labels)]}]), state['scope'], state['version'])
            finally:
                if agent and agent.status.status == 'waiting_approval':
                    agent.status.status = 'tool'
            answer = answers[0] if answers else {}
            selected_ids = [str(value) for value in (answer.get('selected_ids') or [])]
            selected_labels = [str(value) for value in (answer.get('selected') or [])]
            if not answer.get('custom') and len(selected_ids) == 1:
                selected = next((item['optionId'] for item in options if item['optionId'] == selected_ids[0]), None)
            elif not answer.get('custom') and len(selected_labels) == 1 and selected_labels[0] in labels:
                selected = options[labels.index(selected_labels[0])]['optionId']
        session = ctx.services.get('session') or self.harness.get_session(ctx.session_id)
        await session.append('note', {'kind': 'external_agent_permission', 'call_id': ctx.call_id,
            'agent': runner, 'scope': state['scope'], 'policy': state['permission_policy'],
            'operation': _redact_sensitive(operation),
            'sensitive': sensitive,
            'tool_call_id': _redact_sensitive(str(params.get('toolCall', {}).get('toolCallId', '')))[:500],
            'option_id': selected, 'decision': next((item['kind'] for item in options if item['optionId'] == selected), 'cancelled')})
        if self.snapshot(ctx.session_id)['version'] != state['version']:
            return None
        return selected

    def grants_view(self, session_id: str) -> dict[str, Any]:
        scope = self.scope_for(session_id)
        now = time.monotonic()
        for key, row in list(self.grants.items()):
            name = row['tool']
            if not self.harness.tools.has(name):
                self.grants.pop(key, None)
            else:
                self._active_grant(self.harness.tools.get(name), key)
        rows = [{**{field: row[field] for field in ('id', 'tool', 'cwd', 'call_id')},
                 'remaining_seconds': max(0, math.ceil(row['_expires_at'] - now))}
                for key, row in self.grants.items() if key[0] == scope]
        return {'scope': scope, 'parent_controlled': scope != session_id, 'grants': rows}

    def grant_detail(self, session_id: str, grant_id: str) -> dict[str, Any] | None:
        view = self.grants_view(session_id)
        row = next((row for row in view['grants'] if row['id'] == grant_id), None)
        if row is None:
            return None
        operation = next(row['operation'] for key, row in self.grants.items() if key[0] == view['scope'] and row['id'] == grant_id)
        return {**row, 'operation': operation}

    async def revoke_grant(self, session_id: str, grant_id: str) -> dict[str, Any]:
        view = self.grants_view(session_id)
        if view['parent_controlled']:
            raise HarnessError(tr('python.permissions.bfd7663f08c7'), code='permissions/inherited')
        key = next((key for key, row in self.grants.items() if key[0] == view['scope'] and row['id'] == grant_id), None)
        if key is None:
            raise LookupError(tr('python.permissions.a9001a456a3e'))
        # 先移除执行准入权限，审计失败不能让授权继续生效。
        row = self.grants.pop(key)
        result = {'revoked': grant_id}
        try:
            await self.harness.get_session(session_id).append('note', {'kind': 'permission_grant_revoked',
                'grant_id': grant_id, 'fingerprint': key[1], 'tool': row['tool'], 'scope': view['scope']})
        except Exception:
            result['audit_error'] = tr('ui.permissions.auditIncomplete')
        return result

    async def _guard(self, tool: Any, args: Any, ctx: Any) -> str | None:
        authority = self._tool_authority(tool)
        snapshot = self.snapshot(ctx.session_id)
        policy = snapshot["permission_policy"]
        name = tool.schema.name
        sensitive = _contains_sensitive_value(args, public_fields=_PUBLIC_TOOL_ARGUMENTS.get(name, frozenset()),
                                              reference_checks=_argument_reference_checks(tool.argument_semantics, args))
        if sensitive:
            if policy == "full_access":
                return tr('python.permissions.sensitive_reference')
        if policy == "full_access":
            return None
        scope = snapshot["scope"]
        reasons = []
        if sensitive:
            reasons.append('sensitive_data_access')
        if tool.approval_required:
            reasons.append('tool_requires_approval')
        if tool.dangerous:
            reasons.append('dangerous_tool')
        if policy in {"request_approval", "auto_review"} and tool.effect == "workspace_write":
            reasons.append('workspace_write')
        if tool.effect == 'external_write':
            reasons.append('external_write')
        if tool.effect == 'execute':
            reasons.append('command_execution')
        if name in {"bash", "pwsh", "run_code", "terminal_open", "terminal_send", "workflow"} or name.startswith("mcp__") and tool.effect != "read":
            reasons.append('connector_operation' if name.startswith('mcp__') else 'command_execution')
        if name == "browser" and args.get("action") not in {"navigate", "observe", "screenshot", "tabs", "diagnostics", "options"}:
            reasons.append('browser_interaction')
        if policy in {"request_approval", "auto_review"} and name in {"web_fetch", "web_search", "browser"}:
            reasons.append('network_access')
        if policy in {"request_approval", "auto_review"} and tool.effect == "unknown":
            trusted = {"get_goal", "create_goal", "update_goal", "todo_write", "todo_update", "todo_bind", "ask_user_question", "exit_plan_mode",
                "spawn_agent", "followup_task", "list_agents", "wait_agent", "wait_agents", "send_message", "interrupt_agent", "list_subagent_models",
                "session_search", "session_trace", "session_event_search", "session_event_trace", "session_event_read",
                "load_workspace_dependencies", "list_mcp_resources", "list_mcp_resource_templates", "read_mcp_resource",
                "terminal_read", "terminal_list", "job_output", "job_list", "present"}
            if name not in trusted:
                reasons.append('unknown_effect')
        if name in {"write", "edit"}:
            raw = args.get("path", "")
            if raw:
                root = Path(ctx.cwd or ".").resolve()
                target = Path(raw)
                target = (root / target).resolve() if not target.is_absolute() else target.resolve()
                if not target.is_relative_to(root):
                    reasons.append('outside_working_directory')
        if name == "read" and isinstance(args, dict):
            raw = args.get("path", "")
            if raw:
                root = Path(ctx.cwd or ".").resolve()
                target = Path(raw)
                target = (root / target).resolve() if not target.is_absolute() else target.resolve()
                if not target.is_relative_to(root):
                    reasons.append('outside_working_directory')
        if policy == "read_only" and name in {"read", "grep"}:
            raw = args.get("path", "")
            if raw:
                root = Path(ctx.cwd or ".").resolve()
                target = Path(raw)
                target = (root / target).resolve() if not target.is_absolute() else target.resolve()
                if not target.is_relative_to(root):
                    reasons.append('outside_working_directory')
        if policy == "read_only" and (reasons or tool.effect != "read"):
            reasons.append('read_only_policy')
        if not reasons:
            return None
        approval = {'policy': policy, 'reasons': list(dict.fromkeys(reasons))}
        fingerprint = operation_fingerprint(name, args, ctx.cwd)
        grant_key = (scope, fingerprint)
        if self._active_grant(tool, grant_key) is not None:
            return None
        agent = self.harness._agents.get(ctx.session_id)
        if agent is None:
            agent = next((s.agent for s in self.harness.subagents.list_states() if s.session is not None and s.session.id == ctx.session_id), None)
        session = ctx.services.get("session")
        hooks = getattr(self.harness, 'hooks', None)
        attempts = ctx.services.setdefault('_hook_permission_attempts', set())
        attempt = (ctx.session_id, ctx.turn, ctx.step, ctx.call_id, scope, fingerprint)
        if hooks is not None and attempt not in attempts:
            # 权限版本变化会重判审批；已开始的处理器不得因此自动重放。
            attempts.add(attempt)
            hook = await self._await_current(hooks.permission(tool, args, ctx), scope, snapshot['version'])
            if hook.get('behavior') in ('allow', 'deny'):
                hook_reason = _redact_sensitive(str(hook.get('reason', '')))
                if session is not None:
                    await session.append('note', {'kind': 'permission_decision', 'call_id': ctx.call_id, 'tool': name,
                        'fingerprint': fingerprint, 'scope': scope, 'decision': [hook['behavior']], 'reviewer': 'hook',
                        'reason': hook_reason})
                return hook_reason or tr('hook.feedback.0a5ca5ad2cac') if hook['behavior'] == 'deny' else None
        if policy == "auto_review":
            decision, reason, review = await self._await_current(self._review(tool, args, ctx, scope, agent), scope, snapshot["version"])
            if fingerprint != operation_fingerprint(name, args, ctx.cwd):
                return tr('python.permissions.6d257511c3cf')
            if session is not None:
                await session.append("note", {"kind": "permission_decision", "call_id": ctx.call_id, "tool": name,
                    "fingerprint": fingerprint, "scope": scope, "decision": [decision], "reviewer": "auto_review", "reason": reason})
            if self.snapshot(scope)["version"] != snapshot["version"]:
                raise _PolicyChanged()
            if decision == "allow":
                return None
            if decision == "deny":
                return tr('permissions.review_denied', reason=reason)
            approval['review'] = review
        if agent:
            agent.status.status = "waiting_approval"
        try:
            prompt = tr('permissions.confirm_operation', tool=name, cwd=_redact_path(str(ctx.cwd)), arguments=json.dumps(_redact_sensitive(args), ensure_ascii=False))
            if len(prompt) > 64000:
                return tr('python.permissions.9f1aa794f201')
            once_label = tr('python.permissions.f9881e36e9cd')
            temporary_label = tr('python.permissions.4e9b6b4d7c21')
            deny_label = tr('python.permissions.136de7a8c46f')
            answers = await self._await_current(self.harness.questions.ask(session_id=scope, call_id=ctx.call_id,
                timeout=3600, max_question_chars=64000, questions=[{
                "id": "permission", "header": tr('python.permissions.e83c7a5bf2a8'), "question": prompt,
                "operation": {"tool": name, "cwd": _redact_path(str(ctx.cwd)), "effect": tool.effect, "approval": approval},
                "options": [{"id": "allow-once", "label": once_label}, {"id": "allow-temporarily", "label": temporary_label}, {"id": "deny", "label": deny_label}],
            }]), scope, snapshot["version"])
        finally:
            if agent and agent.status.status == "waiting_approval":
                agent.status.status = "tool"
        if fingerprint != operation_fingerprint(name, args, ctx.cwd):
            return tr('python.permissions.18c92bb13d63')
        selected = answers[0].get("selected", []) if answers else []
        selected_ids = [str(value) for value in ((answers[0].get("selected_ids", []) if answers else []) or [])]
        custom = bool(answers and answers[0].get("custom"))
        decision = [tr('python.permissions.a149ea344e74')] if custom else selected
        decision_ids = ['custom'] if custom else selected_ids
        if session is not None:
            await session.append("note", {"kind": "permission_decision", "call_id": ctx.call_id, "tool": name, "fingerprint": fingerprint, "scope": scope, "decision": decision, "decision_ids": decision_ids, "approval": approval, "expires_after_seconds": 900 if not custom and (selected_ids == ['allow-temporarily'] or selected == [temporary_label]) else 0})
        if self.snapshot(scope)["version"] != snapshot["version"]:
            raise _PolicyChanged()
        if not self._same_authority(authority, tool):
            return tr('python.permissions.e6e4e44a4b91')
        if custom:
            return tr('python.permissions.97230ae1cdd7')
        if selected_ids == ['allow-temporarily'] or selected == [temporary_label]:
            self.grants[grant_key] = {'_expires_at': time.monotonic() + 900,
                '_authority': authority, 'id': 'grant_' + uuid.uuid4().hex, 'tool': name,
                'cwd': str(ctx.cwd), 'call_id': ctx.call_id, 'operation': prompt}
        elif not (selected_ids == ['allow-once'] or selected == [once_label]):
            return tr('python.permissions.4cbec050e546')
        return None

    def _review_requester(self, session_id: str) -> dict[str, Any]:
        """如实标注请求方是根代理还是受管子代理，以及它当前的委派边界。

        审阅者据此判断"父代理已获批准"是否被当成子代理的通用授权；
        无法归属到任何受管运行时按根代理报告，不猜测身份。
        """
        subagents = getattr(self.harness, "subagents", None)
        state = subagents.state_for_session(session_id) if subagents is not None else None
        if state is None:
            return {"kind": "root", "depth": 0}
        return {"kind": "subagent", "depth": getattr(state, "depth", 0), "agent_id": state.id,
                "read_only": bool(state.read_only), "runner": state.runner,
                "parent_session_id": state.parent_session_id}

    async def _review(self, tool: Any, args: Any, ctx: Any, scope: str, agent: Any) -> tuple[str, str, str]:
        """独立、无工具、受预算约束的审阅；失败回退人工而不是放行。

        证据被截断时显式声明截断，而不是让审阅者把不完整样本当成完整授权。
        """
        session = self.harness.sessions.get(scope)
        recorded = [e for e in session.events_of_type("user/message")
                    if e.data.get("source", "user") in {"user", "steer"}]
        kept = recorded[-MAX_REVIEW_USER_MESSAGES:]
        intent = [_redact_sensitive(e.data.get("text", "")) for e in kept]
        payload = json.dumps({
            "user_messages": intent,
            "user_messages_total": len(recorded),
            "user_messages_incomplete": len(recorded) > len(kept),
            "requester": self._review_requester(ctx.session_id),
            "operation": {"tool": tool.schema.name, "arguments": _redact_sensitive(args),
                          "cwd": _redact_path(str(ctx.cwd)), "effect": tool.effect}}, ensure_ascii=False)
        if len(payload) > 24000:
            return "ask", tr('python.permissions.517c05d73aee'), 'input_too_large'
        config = (agent.config if agent is not None else LlmCallConfig(model=self.harness.config.model)).merged(max_tokens=1024)
        budget = getattr(agent, "budget", None)
        reservation = None
        response = None
        review_policy = tr('owned.permissions.1d72dffc9a0f')
        input_cost = len(review_policy + payload) + 256  # 保守预留，避免低估中文。
        try:
            async with asyncio.timeout(45):
                if budget is not None:
                    reservation = await budget.acquire(input_cost, config.max_tokens)
                    if reservation is None:
                        return "ask", tr('python.permissions.6e2907c15a08'), 'budget_unavailable'
                    config = config.merged(max_tokens=reservation.max_tokens)
                response = await self.harness.adapter.complete(
                    [SystemMessage(review_policy), UserMessage(payload)], config=config, tools=[])
            value = json.loads(response.text)
            if response.has_tool_use or not isinstance(value, dict) or set(value) != {"decision", "reason"}:
                raise ValueError(tr('runtime.validation.79f30161af60'))
            if value["decision"] not in {"allow", "deny", "ask"} or not isinstance(value["reason"], str) or not value["reason"].strip():
                raise ValueError(tr('runtime.validation.3250cd19ce4a'))
            return value["decision"], _redact_sensitive(value["reason"].strip()[:1500]), 'confirmation_requested'
        except Exception:
            return "ask", tr('python.permissions.83426f82d874'), 'unavailable'
        finally:
            if reservation is not None:
                reservation.settle(response.usage.total_tokens if response is not None and response.usage is not None else input_cost + 1024)
