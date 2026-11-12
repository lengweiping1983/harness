"""项目命令钩子：显式信任、受限进程和结果证据。"""
from __future__ import annotations

from harness.core.text_encoding import TextDecoder, utf8_prefix

from harness.i18n import tr

import asyncio
import regex
import hashlib
import json
import math
import os
import subprocess
import re
import tempfile
import threading
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from ..core.errors import HarnessError, PersistenceError, ToolNotFoundError
from ..core.cleanup import join_cleanup
from ..llm.types import TextBlock
from ..session.hook_runs import hook_summary
from ..session.events import AppendOp
from ..tools.process_env import execution_env
from ..tools.process_output import cleanup_process
from ..tools.processes import ProcessCleanupError
from ..tools.registry import ToolResult, ToolRunContext
from ..tools.windows_shell import windows_shell_command, WINDOWS_UTF8_SETUP
from .hook_output import CAPTURE_LIMIT, DEFAULT_CONTEXT_LIMIT, context_preview

EVENTS = ('PreToolUse', 'PermissionRequest', 'PostToolUse', 'PostToolUseFailure', 'PreCompact', 'PostCompact', 'SessionStart', 'SessionEnd', 'UserPromptSubmit', 'SubagentStart', 'SubagentStop', 'Stop', 'Interrupt')
FILE_LIMIT = 65536
OUTPUT_LIMIT = 16384
MATCHED_EVENTS = {'PreToolUse', 'PermissionRequest', 'PostToolUse', 'PostToolUseFailure',
                  'SubagentStart', 'SubagentStop', 'PreCompact', 'PostCompact', 'SessionStart', 'SessionEnd'}


def validate_document(document: Any) -> list[dict[str, Any]]:
    """严格验证受支持的子集，不能将不支持的钩子报告为已执行。"""
    if not isinstance(document, dict) or set(document) - {'hooks', 'description', 'enabled'}:
        raise ValueError(tr('python.hooks.8386ca9eeda2'))
    if not isinstance(document.get('enabled', True), bool):
        raise ValueError(tr('python.hooks.78c37bc7972d'))
    if 'description' in document and not isinstance(document['description'], str):
        raise ValueError(tr('python.hooks.75ec6fedcc20'))
    hooks = document.get('hooks')
    if not isinstance(hooks, dict) or set(hooks) - set(EVENTS):
        raise ValueError(tr('python.hooks.78377479628d'))
    rows = []
    for event, groups in hooks.items():
        if not isinstance(groups, list) or len(groups) > 32:
            raise ValueError(tr('python.hooks.d41973ed51a1', p0=f'{event}'))
        for group_index, group in enumerate(groups):
            if not isinstance(group, dict) or set(group) - {'matcher', 'hooks'}:
                raise ValueError(tr('python.hooks.8560b188a181', p0=f'{event}'))
            matcher = group.get('matcher', '*')
            if not isinstance(matcher, str) or len(matcher) > 256:
                raise ValueError(tr('python.hooks.bf188774d45a'))
            if event in MATCHED_EVENTS and matcher not in ('', '*'):
                try:
                    regex.compile(matcher)
                except regex.error as exc:
                    raise ValueError(tr('hook.feedback.matcher_invalid', error=exc)) from exc
            handlers = group.get('hooks')
            if not isinstance(handlers, list) or not handlers or len(handlers) > 32:
                raise ValueError(tr('python.hooks.e8aa31f30e09'))
            for index, handler in enumerate(handlers):
                if not isinstance(handler, dict) or set(handler) - {'type', 'command', 'timeout', 'statusMessage', 'enabled', 'server', 'tool', 'input', 'prompt', 'additionalContextLimit'}:
                    raise ValueError(tr('python.hooks.b452be356732'))
                kind = handler.get('type')
                if kind not in ('command', 'mcp_tool', 'prompt', 'agent'):
                    raise ValueError(tr('python.hooks.6296133aef56'))
                command = handler.get('command', '')
                if kind == 'command' and (not isinstance(command, str) or not command.strip() or len(command) > 4096 or '\0' in command):
                    raise ValueError(tr('python.hooks.5e2c4da11718'))
                if kind == 'mcp_tool':
                    if not all(isinstance(handler.get(key), str) and handler[key] for key in ('server', 'tool')) or not isinstance(handler.get('input', {}), dict):
                        raise ValueError(tr('hook.feedback.4f8fa5dc93e0'))
                    if event == 'SessionEnd':
                        raise ValueError(tr('hook.feedback.e81bdd56ff1d'))
                timeout = handler.get('timeout', 1 if kind == 'command' and event in ('Interrupt', 'SessionEnd') else 600)
                if type(timeout) not in (int, float) or not .05 <= timeout <= 3600 or not math.isfinite(float(timeout)):
                    raise ValueError(tr('python.hooks.ca99c86f0997'))
                if kind == 'command' and event in ('Interrupt', 'SessionEnd'):
                    timeout = max(1, min(3, timeout))
                label = handler.get('statusMessage', tr('python.hooks.ce636db7bf42'))
                if not isinstance(label, str) or not label.strip() or len(label) > 160:
                    raise ValueError(tr('python.hooks.fe191d486141'))
                if not isinstance(handler.get('enabled', True), bool):
                    raise ValueError(tr('python.hooks.7a0e42fbb43f'))
                context_limit = handler.get('additionalContextLimit', DEFAULT_CONTEXT_LIMIT)
                if type(context_limit) is not int or not 0 <= context_limit <= 2147483647:
                    raise ValueError(tr('hook.feedback.9385ef050a3f'))
                rows.append({'id': f'{event}:{group_index}:{index}', 'event': event,
                             'matcher': matcher, 'type': kind, 'command': command, 'timeout': timeout,
                             'server': handler.get('server'), 'tool': handler.get('tool'), 'input': handler.get('input', {}),
                             'label': label, 'enabled': document.get('enabled', True) and handler.get('enabled', True)})
                rows[-1]['additionalContextLimit'] = context_limit
                if len(rows) > 32:
                    raise ValueError(tr('python.hooks.9643d1ffe2d3'))
    return rows


def matches(matcher: str, tool: str) -> bool:
    """按正则搜索匹配，特殊通配值全匹配；限制输入和回溯时间。"""
    if matcher in ('', '*'):
        return True
    try:
        return regex.search(matcher, tool[:4096], timeout=.02) is not None
    except TimeoutError as exc:
        raise ValueError(tr('hook.feedback.b934be112f40')) from exc


def event_matches(event: str, matcher: str, value: str) -> bool:
    """预览和实际分发共用事件匹配规则。"""
    return event not in MATCHED_EVENTS or matches(matcher, value)


def atomic_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, delete=False)
    try:
        with handle:
            json.dump(data, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(handle.name, path)
    finally:
        if os.path.exists(handle.name):
            os.unlink(handle.name)


class HookConflict(ValueError):
    pass


class _DuplicateKey(ValueError):
    """完整JSON中的重复字段不能按最后一项静默覆盖。"""


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateKey(tr('hooks.json.duplicate', field=key))
        value[key] = item
    return value


def _read_json(text):
    return json.loads(text, object_pairs_hook=_unique_object)


def _exit_status(path):
    """未写完的JSON可继续等待，完整但矛盾的回执必须明确失败。"""
    try:
        value = _read_json(path.read_text(encoding='utf-8'))
    except _DuplicateKey as exc:
        raise ProcessCleanupError(tr('hooks.status.invalid')) from exc
    if not isinstance(value, dict) or set(value) != {'exit_code'} or type(value['exit_code']) is not int:
        raise ProcessCleanupError(tr('hooks.status.invalid'))
    return value['exit_code']


def _valid_run(row):
    """持久运行记录只接受当前生产字段和精确类型，扩展字段不改写。"""
    if not isinstance(row, dict):
        return False
    required = ('id', 'root', 'session_id', 'call_id', 'event', 'tool', 'hook_id', 'label',
                'command', 'handler_type', 'source_scope', 'fingerprint', 'reason', 'stdout', 'stderr')
    if any(not isinstance(row.get(key), str) for key in required) or not row['id']:
        return False
    if not isinstance(row.get('status'), str) or row['status'] not in {'running', 'passed', 'blocked', 'failed', 'timed_out', 'cancelled', 'interrupted', 'skipped', 'stopped', 'cleanup_failed'}:
        return False
    if row['event'] not in EVENTS or row['handler_type'] not in {'command', 'mcp_tool', 'prompt', 'agent'} or row['source_scope'] not in {'project', 'user', 'plugin'}:
        return False
    if any(type(row.get(key)) is not int or row[key] < 0 for key in ('turn', 'step')):
        return False
    for key in ('started', 'duration_ms', 'finished'):
        if key == 'finished' and key not in row:
            continue
        value = row.get(key)
        if type(value) not in (int, float) or value < 0 or isinstance(value, float) and not math.isfinite(value):
            return False
    return row.get('plugin_id') is None or isinstance(row['plugin_id'], str)


class ProjectHooks:
    def __init__(self, harness: Any) -> None:
        self.harness = harness
        self.home = harness.config.home
        self.lock = threading.RLock()
        self.trust = self._read_state('hook-trust.json', {})
        self.runs = self._read_state('hook-runs.json', [])[-100:]
        self.started_sessions: set[str] = set()
        # 宿主重启后，不能继续将进程标记为运行中。
        for run in self.runs:
            if run.get('status') == 'running':
                run.update(status='interrupted', reason=tr('python.hooks.fc11da0fa898'), finished=time.time())

    def _read_state(self, name: str, default: Any) -> Any:
        try:
            data = _read_json((self.home / name).read_text(encoding='utf-8'))
            valid = isinstance(data, type(default))
            if isinstance(default, list):
                valid = valid and all(_valid_run(row) for row in data)
            elif isinstance(default, dict):
                valid = valid and all(isinstance(key, str) and key and isinstance(value, str) and value for key, value in data.items())
            if not valid:
                raise ValueError(name)
            return data
        except FileNotFoundError:
            return default
        except (OSError, ValueError) as exc:
            raise PersistenceError(tr('python.audit.hook_state_invalid', path=str(self.home / name))) from exc

    def project_root(self, cwd: str | None) -> Path:
        origin = Path(cwd or self.harness.config.cwd or Path.cwd()).expanduser().resolve()
        for path in (origin, *origin.parents):
            if (path / '.harness' / 'hooks.json').exists() or (path / '.git').exists():
                return path
        return origin

    def _load(self, root: Path, *, user: bool = False) -> dict[str, Any]:
        root = root.resolve()
        path = root / 'hooks.json' if user else root / '.harness' / 'hooks.json'
        if not path.resolve().is_relative_to(root):
            raise ValueError(tr('python.hooks.6a95b0fafc40'))
        try:
            with path.open('rb') as handle:
                raw = handle.read(FILE_LIMIT + 1)
        except FileNotFoundError:
            return {'fingerprint': 'missing', 'document': {'hooks': {}}, 'rows': [], 'error': '', 'source': str(path)}
        if len(raw) > FILE_LIMIT:
            raise ValueError(tr('python.hooks.b7e04b3cbed0'))
        fingerprint = hashlib.sha256(raw).hexdigest()
        text = ''
        try:
            text = raw.decode('utf-8')
            document = _read_json(text)
            rows = validate_document(document)
            error = ''
        except (ValueError, UnicodeDecodeError) as exc:
            document, rows, error = {'hooks': {}}, [], str(exc)
        return {'fingerprint': fingerprint, 'document': document, 'rows': rows, 'error': error,
                'source': str(path), 'text': text}

    def snapshot(self, root: Path) -> dict[str, Any]:
        root = root.resolve()
        with self.lock:
            state = self._load(root)
            trusted = self.trust.get(str(root)) == state['fingerprint'] and state['fingerprint'] != 'missing'
            return {**state, 'root': str(root), 'trusted': trusted,
                    'runs': [dict(row) for row in reversed(self.runs) if row.get('root') == str(root)]}

    def user_snapshot(self) -> dict[str, Any]:
        """用户 hooks.json 与项目源复用解析器、信任文件及运行记录。"""
        with self.lock:
            state = self._load(self.home, user=True)
            return {**state, 'root': str(self.home.resolve()), 'scope': 'user',
                    'trusted': state['fingerprint'] != 'missing' and self.trust.get('user') == state['fingerprint'],
                    'runs': [dict(row) for row in reversed(self.runs) if row.get('source_scope') == 'user']}

    def save_user(self, document: Any, expected: str) -> dict[str, Any]:
        """显式保存用户源，不搬移或改写项目配置。"""
        validate_document(document)
        encoded = json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8')
        if len(encoded) + 1 > FILE_LIMIT:
            raise ValueError(tr('python.hooks.b7e04b3cbed0'))
        with self.lock:
            state = self.user_snapshot()
            if state['fingerprint'] != expected:
                raise HookConflict(tr('python.hooks.d75bd713aa25'))
            atomic_json(Path(state['source']), document)
            return self.user_snapshot()

    def set_user_trust(self, expected: str, enabled: bool) -> dict[str, Any]:
        """用户源的审核仍绑定当前定义指纹。"""
        if type(enabled) is not bool:
            raise ValueError(tr('python.hooks.3970f32abe83'))
        with self.lock:
            state = self.user_snapshot()
            if state['fingerprint'] != expected:
                raise HookConflict(tr('python.hooks.320cdc3f62ef'))
            if enabled and (state['error'] or state['fingerprint'] == 'missing'):
                raise ValueError(tr('python.hooks.437631d83c29'))
            trust = dict(self.trust)
            if enabled:
                trust['user'] = expected
            else:
                trust.pop('user', None)
            atomic_json(self.home / 'hook-trust.json', trust)
            self.trust = trust
            return self.user_snapshot()

    def _source_snapshot(self, row: dict[str, Any], root: Path) -> dict[str, Any]:
        """所有来源在执行前后通过同一路径复核。"""
        if row.get('plugin_id'):
            return self.plugin_snapshot(row['plugin_id'])
        return self.user_snapshot() if row.get('source_scope') == 'user' else self.snapshot(root)

    def plugin_definition_trusted(self, name: str, fingerprint: str) -> bool:
        """对本次已解析声明检查信任，不再次发现整个包目录。"""
        with self.lock:
            return self.trust.get('plugin:' + name) == fingerprint

    def plugin_snapshot(self, name: str) -> dict[str, Any]:
        """插件源读取同一包存储，信任记录仍使用统一 hook-trust.json。"""
        store = getattr(self.harness, 'packages', None)
        for spec, state in store.entries() if store is not None else ():
            if spec.name != name:
                continue
            encoded = json.dumps(spec.hooks, sort_keys=True, ensure_ascii=False).encode()
            fingerprint = hashlib.sha256(encoded).hexdigest()
            rows = []
            for index, document in enumerate(spec.hooks):
                rows.extend({**row, 'id': f'plugin:{name}:{index}:' + row['id'],
                             'plugin_id': name, 'plugin_root': str(spec.root),
                             'plugin_data': str(store.root / 'data' / name)} for row in validate_document(document))
            return {'name': name, 'rows': rows, 'fingerprint': fingerprint, 'enabled': state['enabled'],
                    'trusted': self.plugin_definition_trusted(name, fingerprint)}
        raise KeyError(name)

    def set_plugin_trust(self, name: str, expected: str, trusted: bool) -> dict[str, Any]:
        """审核当前插件定义；包更新后旧指纹自然失效。"""
        if type(trusted) is not bool:
            raise ValueError(tr('hook.feedback.ea64331bc0c0'))
        with self.lock:
            state = self.plugin_snapshot(name)
            if state['fingerprint'] != expected:
                raise HookConflict(tr('hook.feedback.6cf3b49b3027'))
            trust = dict(self.trust)
            if trusted:
                trust['plugin:' + name] = expected
            else:
                trust.pop('plugin:' + name, None)
            atomic_json(self.home / 'hook-trust.json', trust)
            self.trust = trust
            return self.plugin_snapshot(name)

    def save(self, root: Path, document: Any, expected: str) -> dict[str, Any]:
        root = root.resolve()
        validate_document(document)
        encoded = json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8')
        if len(encoded) + 1 > FILE_LIMIT:
            raise ValueError(tr('python.hooks.b7e04b3cbed0'))
        with self.lock:
            state = self._load(root)
            if state['fingerprint'] != expected:
                raise HookConflict(tr('python.hooks.d75bd713aa25'))
            atomic_json(Path(state['source']), document)
            return self.snapshot(root)

    def set_trust(self, root: Path, expected: str, enabled: bool) -> dict[str, Any]:
        root = root.resolve()
        if not isinstance(enabled, bool):
            raise ValueError(tr('python.hooks.3970f32abe83'))
        with self.lock:
            state = self._load(root)
            if state['fingerprint'] != expected:
                raise HookConflict(tr('python.hooks.320cdc3f62ef'))
            if enabled and (state['error'] or state['fingerprint'] == 'missing'):
                raise ValueError(tr('python.hooks.437631d83c29'))
            trust = dict(self.trust)
            if enabled:
                trust[str(root)] = expected
            else:
                trust.pop(str(root), None)
            atomic_json(self.home / 'hook-trust.json', trust)
            self.trust = trust
            return self.snapshot(root)

    def preview(self, document: Any, event: str, tool: str) -> dict[str, Any]:
        if event not in EVENTS or not isinstance(tool, str) or not tool.strip() or len(tool) > 256:
            raise ValueError(tr('python.hooks.b7fb561c8895'))
        rows = validate_document(document)
        names = self._tool_names(tool) if event in ('PreToolUse', 'PermissionRequest', 'PostToolUse', 'PostToolUseFailure') else (tool,)
        return {'event': event, 'tool': tool, 'matches': [row for row in rows if row['event'] == event and row['enabled'] and any(event_matches(event, row['matcher'], name) for name in names)],
                'executed': False}

    def _tool_names(self, tool: str) -> tuple[str, ...]:
        """规范名来自实际注册定义；远程或自定义同名工具不会被猜成内置工具。"""
        registry = getattr(self.harness, 'tools', None)
        if registry is None:
            return (tool,)
        try:
            definition = registry.get(tool)
        except ToolNotFoundError:
            return (tool,)
        return tuple(dict.fromkeys((*definition.hook_names, tool)))

    def _restricted(self, ctx: Any) -> bool:
        agent = ctx.services.get('_stop_agent')
        if agent is not None:
            agent._check_cancelled()
            if self.harness.plan.projection(ctx.services['session']).active:
                return True
            # 新的用户引导取代对旧拟定回复的检查。
            if agent.pending_input or ctx.services['session'].steering.pending(ctx.turn):
                return True
        return (ctx.cancelled or ctx.services.get('recovery_read_only') or ctx.services.get('task_read_only') or
                self.harness.plan.is_active_session_id(ctx.session_id) or
                self.harness.permissions.snapshot(ctx.session_id)['permission_policy'] == 'read_only')

    async def before(self, definition: Any, args: Any, ctx: Any) -> str | None:
        if self._restricted(ctx):
            return None
        feedback = await self._dispatch('PreToolUse', definition.schema.name, args, ctx)
        ctx.services.setdefault('_hook_feedback', {}).setdefault(ctx.call_id, []).extend(('PreToolUse', text) for text in feedback['contexts'])
        return feedback['denial']

    async def after(self, call: Any, ctx: Any, result: ToolResult) -> None:
        if self._restricted(ctx):
            return
        event = 'PostToolUseFailure' if result.is_error else 'PostToolUse'
        feedback = await self._dispatch(event, call.name, call.input, ctx, result)
        contexts = ctx.services.get('_hook_feedback', {}).pop(ctx.call_id, []) + [(event, text) for text in feedback['contexts']]
        if feedback['denial']:
            contexts.append((event, feedback['denial']))
        if event == 'PostToolUse' and (feedback['blocks'] or feedback.get('stopped')):
            # 已发生的副作用不能撤销，但原始输出不得继续绕过钩子的反馈。
            result.content = [TextBlock(text='\n'.join(feedback['blocks'] + feedback['contexts']) or feedback.get('stop_reason') or tr('hook.feedback.5f79be73b30f'))]
            result.data = None
            result.display = None
            if feedback['blocks']:
                result.is_error = True
            return
        for source, context in contexts:
            result.content.append(TextBlock(text=tr('python.hooks.e645b89cb458', p0=f'{source}', p1=f'{context}')))

    def _candidates(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        """统一汇集项目与启用插件声明，不创建第二份注册状态。"""
        user = self.user_snapshot()
        if user['error']:
            raise ValueError(user['error'])
        candidates = [{**row, 'source_scope': 'user', 'fingerprint': user['fingerprint'],
                       'id': 'user:' + row['id']} for row in user['rows']]
        candidates.extend(state['rows'])
        store = getattr(self.harness, 'packages', None)
        for spec, installed in store.entries() if store is not None else ():
            if installed['enabled'] and spec.hooks:
                plugin = self.plugin_snapshot(spec.name)
                candidates.extend({**row, 'fingerprint': plugin['fingerprint']} for row in plugin['rows'])
        return candidates

    async def lifecycle(self, agent: Any, session: Any, event: str, turn: int, step: int,
                        extra: dict[str, Any]) -> None:
        """由真实运行边界调用，共用工具钩子的分发、限制与证据。"""
        if event == 'SessionStart':
            if session.id in self.started_sessions:
                return
            self.started_sessions.add(session.id)
            extra = {'source': 'startup' if turn <= 1 else 'resume', **extra}
        state = self.snapshot(self.project_root(session.header.cwd))
        if not state['error'] and not any(row['event'] == event and row['enabled'] for row in self._candidates(state)):
            return
        ctx = ToolRunContext('lifecycle:' + uuid.uuid4().hex, session.id, session.header.cwd, turn, step,
                             services={'session': session, 'bus': self.harness.bus,
                                       'recovery_read_only': getattr(agent, 'recovery_read_only', False),
                                       'task_read_only': getattr(agent, 'task_read_only', False)})
        if self._restricted(ctx):
            return
        feedback = await self._dispatch(event, '', {}, ctx, extra=extra)
        for text in feedback['contexts'] if event not in ('SessionEnd', 'Interrupt') else ():
            await session.append('developer/message', {'text': text, 'source': 'hook:' + event}, surface_op=AppendOp())
        if event == 'UserPromptSubmit' and feedback['denial']:
            raise HarnessError(feedback['denial'], code='hooks/prompt-blocked')
        if feedback.get('stopped') and event in ('SessionStart', 'UserPromptSubmit', 'PreCompact', 'PostCompact'):
            raise HarnessError(feedback.get('stop_reason') or tr('hook.feedback.5f79be73b30f'), code='hooks/stopped')
        if event == 'SessionEnd':
            self.started_sessions.discard(session.id)

    async def permission(self, tool: Any, args: Any, ctx: Any) -> dict[str, Any]:
        """只在宿主确实需要审批时调用，拒绝优先，不创建持久授权。"""
        if self._restricted(ctx):
            return {'behavior': None}
        feedback = await self._dispatch('PermissionRequest', tool.schema.name, args, ctx)
        ctx.services.setdefault('_hook_feedback', {}).setdefault(ctx.call_id, []).extend(('PermissionRequest', text) for text in feedback['contexts'])
        if feedback['denial']:
            return {'behavior': 'deny', 'reason': feedback['denial']}
        return {'behavior': feedback.get('permission')}

    async def stop(self, agent: Any, session: Any, turn: int, step: int,
                   stop_hook_active: bool, last_assistant_message: str, *, event: str = 'Stop',
                   extra: dict[str, Any] | None = None) -> str | None:
        """检查根代理拟定的完成结果，不虚构工具调用。"""
        ctx = ToolRunContext('stop:' + uuid.uuid4().hex, session.id, session.header.cwd, turn, step,
            services={'session': session, 'bus': self.harness.bus, 'jobs': self.harness.jobs,
                'recovery_read_only': agent.recovery_read_only, '_stop_agent': agent,
                'task_read_only': agent.task_read_only,
                'request_id': getattr(agent, 'request_id', None), 'run_id': getattr(agent, 'run_id', None),
                'identity': getattr(agent, 'identity', None)})
        if self._restricted(ctx):
            return None
        feedback = await self._dispatch(event, '', {}, ctx, extra={
            'stop_hook_active': stop_hook_active, 'last_assistant_message': last_assistant_message[:8192], **(extra or {})})
        if self._restricted(ctx):
            return None
        if feedback.get('stopped'):
            return None
        if feedback['error']:
            raise HarnessError(tr('python.hooks.85bfaf8afdbf', error=feedback['error']), code='hooks/completion-failed')
        return '\n'.join(feedback['blocks'] + feedback['contexts']) if feedback['blocks'] else None

    async def _dispatch(self, event: str, tool: str, args: Any, ctx: Any, result: ToolResult | None = None,
                        *, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        root = self.project_root(ctx.cwd)
        state = self.snapshot(root)
        feedback: dict[str, Any] = {'denial': None, 'contexts': [], 'blocks': [], 'error': None, 'permission': None, 'stopped': False, 'stop_reason': ''}
        if state['error']:
            feedback['denial'] = tr('python.hooks.be43f6798b31', p0=f"{state['error']}")
            feedback['error'] = feedback['denial']
            return feedback
        candidates = self._candidates(state)
        values = extra or {}
        match_value = (values.get('agent_type', '') if event.startswith('Subagent') else values.get('trigger', '') if event.endswith('Compact')
                       else values.get('source', '') if event == 'SessionStart' else values.get('reason', 'other') if event == 'SessionEnd' else tool)
        names = self._tool_names(tool) if event in ('PreToolUse', 'PermissionRequest', 'PostToolUse', 'PostToolUseFailure') else (match_value,)
        rows = [row for row in candidates if row['event'] == event and row['enabled'] and any(event_matches(event, row['matcher'], name) for name in names)]
        for row in rows:
            if self._restricted(ctx):
                break
            owner = row.get('plugin_id')
            try:
                current = self._source_snapshot(row, root)
            except KeyError:
                current = {'trusted': False, 'fingerprint': ''}
            fingerprint = row.get('fingerprint', state['fingerprint'])
            reason = None
            if not current['trusted'] or current['fingerprint'] != fingerprint or owner and not current.get('enabled'):
                reason = tr('python.hooks.5db34c8f1858')
            if event in ('Stop', 'SubagentStop') and reason is None:
                self.harness.jobs.assert_ready(ctx.session_id)
            record = {'id': uuid.uuid4().hex, 'root': str(root), 'session_id': ctx.session_id,
                      'call_id': ctx.call_id, 'turn': ctx.turn, 'step': ctx.step,
                      'event': event, 'tool': tool, 'hook_id': row['id'], 'label': row['label'],
                      'command': row['command'], 'handler_type': row.get('type', 'command'), 'source_scope': row.get('source_scope', 'plugin' if owner else 'project'), 'plugin_id': owner, 'fingerprint': fingerprint,
                      'started': time.time(), 'status': 'skipped' if reason else 'running',
                      'reason': reason or '', 'stdout': '', 'stderr': '', 'duration_ms': 0}
            self._record(record)
            if row.get('type') in ('prompt', 'agent'):
                reason = tr('hook.feedback.f5184d45224c')
                record.update(status='skipped', reason=reason)
            if reason:
                self._record(record)
                await self._journal(ctx, record)
                feedback['contexts'].append(reason)
                continue
            payload = {'hook_event_name': event, 'session_id': ctx.session_id, 'cwd': ctx.cwd,
                       'project_dir': str(root), 'tool_name': names[0] if tool else '', 'tool_input': args,
                       'tool_use_id': ctx.call_id, 'turn_id': str(ctx.turn), 'turn': ctx.turn, 'step': ctx.step}
            if result is not None:
                payload['tool_response'] = {'is_error': result.is_error,
                                            'text': '\n'.join(block.text for block in result.content)[:8192]}
            if extra is not None:
                payload.update(extra)
            started = time.monotonic()
            try:
                await self._journal(ctx, record)
                output = await self._mcp_tool(row, payload, ctx) if row.get('type') == 'mcp_tool' else await self._command(row, payload, ctx, root)
                stdout_bytes = output['stdout'].encode('utf-8')
                stderr_bytes = output['stderr'].encode('utf-8')
                record.update({**output, 'stdout': utf8_prefix(stdout_bytes[:OUTPUT_LIMIT], truncated=True),
                               'stderr': utf8_prefix(stderr_bytes[:OUTPUT_LIMIT], truncated=True)})
                record['stdout_dropped'] = output.get('stdout_dropped', 0) + max(0, len(stdout_bytes) - OUTPUT_LIMIT)
                record['stderr_dropped'] = output.get('stderr_dropped', 0) + max(0, len(stderr_bytes) - OUTPUT_LIMIT)
                try:
                    latest = self._source_snapshot(row, root)
                except KeyError:
                    latest = {'trusted': False}
                if not latest['trusted'] or latest.get('fingerprint') != fingerprint or owner and not latest.get('enabled'):
                    record.update(status='skipped', reason=tr('hook.feedback.76009c24d392'))
                    feedback['contexts'].append(record['reason'])
                    continue
                decision = self._interpret(event, output)
                if 'updated_input' in decision:
                    from ..tools.schema import validate_or_raise
                    replacement = decision['updated_input']
                    try:
                        if names[0] in ('Bash', 'apply_patch') and not isinstance(replacement.get('command'), str):
                            raise ValueError(tr('hook.feedback.d4df80477a64'))
                        definition = self.harness.tools.get(tool)
                        validate_or_raise(replacement, dict(definition.schema.parameters), tool=tool)
                        if not isinstance(args, dict):
                            raise ValueError(tr('hook.feedback.37aef2f151a3'))
                    except Exception as exc:
                        decision.update(status='failed', reason=str(exc))
                    else:
                        # 逐处理器替换；下一处理器和后续权限审批读取同一份新参数。
                        args.clear()
                        args.update(replacement)
                if '_additional_context' in decision:
                    additional = context_preview(decision['_additional_context'], self.home, ctx.session_id,
                                                 row.get('additionalContextLimit', DEFAULT_CONTEXT_LIMIT) if row.get('type') == 'command' else DEFAULT_CONTEXT_LIMIT)
                    warning = context_preview(decision.get('_system_message', ''), self.home, ctx.session_id)
                    decision['context'] = '\n'.join(value for value in (warning, additional) if value)
                elif decision['context']:
                    decision['context'] = context_preview(decision['context'], self.home, ctx.session_id)
                if decision.get('permission') == 'allow' and feedback['permission'] is None:
                    feedback['permission'] = 'allow'
                if (event == 'PermissionRequest' and decision.get('permission') == 'allow' or
                        'updated_input' in decision and decision['status'] == 'passed'):
                    def still_trusted(owner=owner, fingerprint=fingerprint, row=row):
                        try:
                            current = self._source_snapshot(row, root)
                        except KeyError:
                            return tr('hook.feedback.2b4193c58854')
                        if not current['trusted'] or current['fingerprint'] != fingerprint or owner and not current.get('enabled'):
                            return tr('hook.feedback.6ca5ed772ac7')
                        return None
                    ctx.admission_checks.append(still_trusted)
                record.update(status=decision['status'], reason=decision['reason'])
                if decision['status'] == 'stopped':
                    feedback.update(stopped=True, stop_reason=decision['reason'])
                if decision['context']:
                    feedback['contexts'].append(decision['context'])
                if decision['status'] in {'blocked', 'failed', 'timed_out'}:
                    message = tr('hook.feedback.named_failure', label=row['label'], reason=decision['reason'])
                    if decision['status'] == 'blocked':
                        feedback['denial'] = message
                        feedback['blocks'].append(message)
                    else:
                        feedback['error'] = message
                        if event != 'PreToolUse':
                            feedback['denial'] = message
                        else:
                            feedback['contexts'].append(message)
            except asyncio.CancelledError:
                record.update(status='cancelled', reason=tr('python.hooks.074b128a64d9'))
                raise
            except Exception as exc:
                # 清理失败必须终止回合，不能授予执行权限。
                if isinstance(exc, ProcessCleanupError):
                    record.update(status='cleanup_failed', reason=str(exc))
                    raise
                record.update(status='failed', reason=str(exc))
                feedback['error'] = tr('hook.feedback.named_failure', label=row['label'], reason=str(exc))
                if event != 'PreToolUse':
                    feedback['denial'] = feedback['error']
                else:
                    feedback['contexts'].append(feedback['error'])
            finally:
                record.update(duration_ms=int((time.monotonic() - started) * 1000), finished=time.time())
                self._record(record)
                await self._journal(ctx, record)
            if feedback['denial'] and event == 'PreToolUse':
                break
        return feedback

    def _record(self, record: dict[str, Any]) -> None:
        with self.lock:
            runs = [row for row in self.runs if row['id'] != record['id']]
            runs.append(dict(record))
            runs = runs[-100:]
            try:
                atomic_json(self.home / 'hook-runs.json', runs)
            except OSError as exc:
                raise PersistenceError(tr('python.hooks.0b124996e74d')) from exc
            self.runs = runs

    async def _journal(self, ctx: Any, record: dict[str, Any]) -> None:
        session = ctx.services.get('session')
        if session:
            evidence = await session.append('note', {'kind': 'hook_run', **record})
            bus = ctx.services.get('bus', self.harness.bus)
            await bus.emit('hook/state', hook_summary(record, evidence.seq, active_turn=ctx.turn))

    @staticmethod
    def _interpret(event: str, output: dict[str, Any]) -> dict[str, Any]:
        decision = {'status': 'passed', 'reason': '', 'context': ''}
        if output.get('skipped'):
            return {**decision, 'status': 'skipped', 'reason': output['stderr']}
        if output['timed_out']:
            return {**decision, 'status': 'timed_out', 'reason': tr('python.hooks.bb1de4fdd8fa')}
        if output.get('encoding_errors'):
            issue = output['encoding_errors'][0]
            return {**decision, 'status': 'failed', 'reason': tr('encoding.failure', **{key: issue[key] for key in ('channel', 'encoding', 'offset')})}
        if output['exit_code'] == 2:
            return {**decision, 'status': 'blocked', 'reason': output['stderr'].strip() or tr('python.hooks.c14cf0dfafa9')}
        if output['exit_code'] != 0:
            return {**decision, 'status': 'failed', 'reason': output['stderr'].strip() or tr('python.hooks.f2a6da131eb4', p0=f"{output['exit_code']}")}
        if output['stdout_dropped']:
            return {**decision, 'status': 'failed', 'reason': tr('python.hooks.ecefd431eb37')}
        text = output['stdout'].strip()
        if not text:
            return decision
        try:
            data = _read_json(text)
        except _DuplicateKey:
            return {**decision, 'status': 'failed', 'reason': tr('python.hooks.6fbcdd0435fa')}
        except ValueError:
            if event in ('SessionStart', 'SubagentStart', 'UserPromptSubmit'):
                return {**decision, 'context': text, '_additional_context': text, '_system_message': ''}
            if event in ('PreToolUse', 'PermissionRequest', 'PostToolUse', 'PreCompact', 'PostCompact'):
                return decision
            return {**decision, 'status': 'failed', 'reason': tr('python.hooks.e53a23031eb7')}
        if not isinstance(data, dict) or set(data) - {'decision', 'reason', 'hookSpecificOutput', 'systemMessage', 'continue', 'stopReason', 'suppressOutput'}:
            return {**decision, 'status': 'failed', 'reason': tr('python.hooks.6fbcdd0435fa')}
        specific = data.get('hookSpecificOutput', {})
        specific_fields = {'hookEventName', 'permissionDecision', 'permissionDecisionReason', 'additionalContext', 'decision'}
        if event == 'PreToolUse':
            specific_fields.add('updatedInput')
        if not isinstance(specific, dict) or set(specific) - specific_fields:
            return {**decision, 'status': 'failed', 'reason': tr('python.hooks.122cab795b3d')}
        if specific.get('hookEventName', event) != event:
            return {**decision, 'status': 'failed', 'reason': tr('python.hooks.a503752345d2')}
        for key in ('continue', 'suppressOutput'):
            if key in data and type(data[key]) is not bool:
                return {**decision, 'status': 'failed', 'reason': tr('hook.feedback.boolean_invalid', field=key)}
        for value in [data.get('reason'), data.get('systemMessage'), data.get('stopReason'), specific.get('additionalContext'), specific.get('permissionDecisionReason')]:
            if value is not None and not isinstance(value, str):
                return {**decision, 'status': 'failed', 'reason': tr('python.hooks.dd1c470ea6fc')}
        if event in ('PreToolUse', 'PermissionRequest', 'Interrupt', 'SessionEnd') and any(key in data for key in ('continue', 'stopReason', 'suppressOutput')) or event == 'PostToolUse' and 'suppressOutput' in data:
            return {**decision, 'status': 'failed', 'reason': tr('hook.feedback.output_fields', event=event)}
        decision['_additional_context'] = specific.get('additionalContext') or ''
        decision['_system_message'] = data.get('systemMessage') or ''
        decision['context'] = '\n'.join(value for value in [data.get('systemMessage'), specific.get('additionalContext')] if value)
        if event == 'PermissionRequest':
            answer = specific.get('decision')
            if answer is None:
                return decision
            if not isinstance(answer, dict) or set(answer) - {'behavior', 'message'} or answer.get('behavior') not in ('allow', 'deny') or not isinstance(answer.get('message', ''), str):
                return {**decision, 'status': 'failed', 'reason': tr('hook.feedback.372cacc2b152')}
            if answer['behavior'] == 'deny':
                return {**decision, 'status': 'blocked', 'permission': 'deny', 'reason': answer.get('message') or tr('hook.feedback.0a5ca5ad2cac')}
            return {**decision, 'permission': 'allow'}
        if data.get('decision') not in (None, 'block'):
            return {**decision, 'status': 'failed', 'reason': tr('python.hooks.e04c6d059d2b')}
        if 'updatedInput' in specific and (specific.get('permissionDecision') != 'allow' or not isinstance(specific['updatedInput'], dict)):
            return {**decision, 'status': 'failed', 'reason': tr('hook.feedback.24fb88c86df3')}
        if event == 'PreToolUse' and specific.get('permissionDecision') == 'allow':
            if 'updatedInput' not in specific:
                return {**decision, 'status': 'failed', 'reason': tr('hook.feedback.eab15d78ccef')}
            if data.get('decision') == 'block':
                return {**decision, 'status': 'blocked', 'reason': (data.get('reason') or tr('hook.feedback.229c06d4cbf6'))[:8192]}
            return {**decision, 'updated_input': specific['updatedInput']}
        if specific.get('permissionDecision') not in (None, 'deny'):
            return {**decision, 'status': 'failed', 'reason': tr('python.hooks.e04c6d059d2b')}
        if event in ('Stop', 'SubagentStop') and ('permissionDecision' in specific or 'permissionDecisionReason' in specific):
            return {**decision, 'status': 'failed', 'reason': tr('python.hooks.a6c455ad1139')}
        if event in ('Stop', 'SubagentStop') and data.get('decision') == 'block' and not (data.get('reason') or '').strip():
            return {**decision, 'status': 'failed', 'reason': tr('python.hooks.de0e8754fbb1')}
        if data.get('continue') is False and event != 'SubagentStart':
            return {**decision, 'status': 'stopped', 'reason': data.get('stopReason') or data.get('reason') or tr('hook.feedback.5f79be73b30f')}
        if data.get('decision') == 'block' or specific.get('permissionDecision') == 'deny':
            decision.update(status='blocked', reason=(specific.get('permissionDecisionReason') or data.get('reason') or tr('python.hooks.7d6ea1aa0d15'))[:8192])
        return decision

    async def _mcp_tool(self, row: dict[str, Any], payload: dict[str, Any], ctx: Any) -> dict[str, Any]:
        """在已有 MCP 连接执行生命周期通知，不启动服务或递归触发钩子。"""
        from ..tools.builtin.mcp_client import public_tool_name, remote_tool_contract
        server = row['server']
        owner = row.get('plugin_id')
        if owner:
            from ..plugins.provider import connector_name
            spec = next((spec for spec, _ in self.harness.packages.entries() if spec.name == owner), None)
            if spec is not None and server in spec.servers:
                server = connector_name(owner, server)
        try:
            supervisor = self.harness.mcp.get(server)
            if not supervisor.connected:
                raise ValueError(tr('hook.feedback.7bdc00d886f8'))
            def expand(value):
                if isinstance(value, dict):
                    return {key: expand(item) for key, item in value.items()}
                if isinstance(value, list):
                    return [expand(item) for item in value]
                if not isinstance(value, str):
                    return value
                def lookup(field):
                    result = payload
                    for key in field.split('.'):
                        result = result.get(key) if isinstance(result, dict) else None
                    return result
                entire = re.fullmatch(r'\$\{([^}]+)\}', value)
                if entire:
                    return lookup(entire[1])
                def render(match):
                    found = lookup(match[1])
                    if found is None:
                        return ''
                    return found if isinstance(found, str) else json.dumps(found, ensure_ascii=False, separators=(',', ':'))
                return re.sub(r'\$\{([^}]+)\}', render, value)
            public_name = public_tool_name(server, row['tool'])
            current = supervisor.remote_tool(public_name)
            if current is None:
                raise ValueError(tr('hook.feedback.aaaadfc6a614'))
            expected_contract = remote_tool_contract(current)
            async with asyncio.timeout(row['timeout']):
                value = await supervisor.call(public_name, expand(row['input']), expected_contract=expected_contract)
            if value.get('isError'):
                raise ValueError(tr('hook.feedback.1224b95cf595'))
            text = '\n'.join(item.get('text', '') for item in value.get('content', []) if item.get('type') == 'text')
            encoded = text.encode('utf-8')
            return {'exit_code': 0, 'stdout': utf8_prefix(encoded[:CAPTURE_LIMIT], truncated=True), 'stderr': '', 'timed_out': False,
                    'stdout_dropped': max(0, len(encoded) - CAPTURE_LIMIT), 'stderr_dropped': 0}
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return {'exit_code': 0, 'stdout': '', 'stderr': str(exc), 'timed_out': False, 'skipped': True,
                    'stdout_dropped': 0, 'stderr_dropped': 0}

    async def _command(self, row: dict[str, Any], payload: dict[str, Any], ctx: Any, root: Path) -> dict[str, Any]:
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        if len(data) > 131072:
            raise ValueError(tr('python.hooks.78aacf83f0f5'))
        env = execution_env({'HARNESS_PROJECT_DIR': str(root), 'HARNESS_SESSION': ctx.session_id})
        if row.get('plugin_id'):
            Path(row['plugin_data']).mkdir(parents=True, exist_ok=True)
            env.update(PLUGIN_ROOT=row['plugin_root'], PLUGIN_DATA=row['plugin_data'],
                       CLAUDE_PLUGIN_ROOT=row['plugin_root'], CLAUDE_PLUGIN_DATA=row['plugin_data'])
        # 保留进程组首进程，直到归属清理完成；后代仍持有标准输出时，
        # Shell 可能已经退出。过早回收首进程会丢失取消边界，
        # 并可能导致其 PID 被复用。
        supervisor = "import os,pathlib,subprocess,sys,time,json\n" + WINDOWS_UTF8_SETUP + """
if sys.platform == 'win32':
    # 先把本监督进程放进 KILL_ON_JOB_CLOSE 的 Job，再启动命令：后代自创建起即属于
    # 该 Job，避免“先启动后挂靠”与中间壳层提前退出的竞态；监督进程被回收时 Job
    # 句柄关闭，后代（即使已在进程树上被重新挂靠）也一并终止，不遗留持有标准输入
    # 文件与工作目录的孤儿子进程。
    import ctypes
    from ctypes import wintypes as wt
    kernel = ctypes.windll.kernel32
    job = kernel.CreateJobObjectW(None, None)
    # JOBOBJECT_EXTENDED_LIMIT_INFORMATION；LimitFlags 位于偏移 16
    limits = ctypes.create_string_buffer(144)
    ctypes.cast(ctypes.byref(limits, 16), ctypes.POINTER(wt.DWORD)).contents.value = 0x2000  # KILL_ON_JOB_CLOSE
    if not (job and kernel.SetInformationJobObject(job, 9, limits, 144)
            and kernel.AssignProcessToJobObject(job, kernel.GetCurrentProcess())):
        kernel.CloseHandle(job)
        job = None
with open(sys.argv[2], 'rb') as source:
    child = subprocess.Popen(sys.argv[1], shell=sys.platform != 'win32',
                             executable=sys.argv[4] or None, stdin=source)
    code = child.wait()
pathlib.Path(sys.argv[3]).write_text(json.dumps({'exit_code': code}), encoding='utf-8')
while True:
    time.sleep(3600)
"""
        with tempfile.TemporaryDirectory(prefix='harness-hook-') as directory:
            source = Path(directory) / 'input.json'
            status = Path(directory) / 'status.json'
            source.write_bytes(data)
            async def create():
                executable, command_line = windows_shell_command(row['command'], env) if os.name == 'nt' else ('', row['command'])
                argv = [sys.executable, '-I', '-X', 'utf8', '-c', supervisor, command_line, str(source), str(status), executable]
                if self.harness.config.enable_owned_processes:
                    from ..tools.owned_process import spawn_owned
                    return await spawn_owned(argv, cwd=str(root), env=env)
                return await asyncio.create_subprocess_exec(
                    *argv, cwd=str(root), env=env, stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                    start_new_session=os.name != 'nt',
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            spawn = asyncio.create_task(create())
            proc = None
            captured = {'stdout': bytearray(), 'stderr': bytearray()}
            dropped = {'stdout': 0, 'stderr': 0}
            tasks = []

            async def settle_cleanup() -> None:
                nonlocal proc
                if proc is None:
                    proc = await spawn
                # 在文件结束或取消完成前，读取器始终拥有其管道。
                # 停止任务时，不能关闭仍在排空输出的 FileIO。
                await cleanup_process(proc, ctx, registry=self.harness.jobs, kind='hook', label=row['label'], close=False)

            cleanup_task = None

            async def clean() -> None:
                nonlocal cleanup_task
                if cleanup_task is None:
                    cleanup_task = asyncio.create_task(settle_cleanup())
                await join_cleanup(cleanup_task)

            async def drain(name: str) -> None:
                while chunk := await getattr(proc, name).read(8192):
                    limit = CAPTURE_LIMIT if name == 'stdout' else OUTPUT_LIMIT
                    keep = min(len(chunk), limit - len(captured[name]))
                    captured[name].extend(chunk[:keep])
                    dropped[name] += len(chunk) - keep

            async def wait_status() -> int:
                while True:
                    if proc.returncode is not None:
                        raise ProcessCleanupError(tr('python.hooks.acf6b50bd73b'), pid=proc.pid)
                    try:
                        return _exit_status(status)
                    except (FileNotFoundError, ValueError):
                        await asyncio.sleep(.02)

            timed_out = False
            code = None
            try:
                proc = await asyncio.shield(spawn)
                tasks = [asyncio.create_task(drain('stdout')), asyncio.create_task(drain('stderr'))]
                try:
                    code = await asyncio.wait_for(wait_status(), timeout=row['timeout'])
                except TimeoutError:
                    timed_out = True
                await clean()
                try:
                    await asyncio.wait_for(asyncio.gather(*tasks), timeout=1)
                except TimeoutError as exc:
                    raise ProcessCleanupError(tr('python.hooks.18e164acaa5b')) from exc
            except BaseException:
                await clean()
                raise
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                if proc is not None and getattr(proc, 'stop_owned', None) is not None and proc.cleanup_confirmed:
                    proc.close()
            decoded, encoding_errors = {}, []
            for channel in ('stdout', 'stderr'):
                decoder = TextDecoder()
                decoded[channel] = decoder.decode(captured[channel], final=not dropped[channel])
                if decoder.error:
                    encoding_errors.append({'channel': channel, **decoder.error})
            return {**decoded, 'encoding_errors': encoding_errors,
                    'stdout_dropped': dropped['stdout'], 'stderr_dropped': dropped['stderr'],
                    'exit_code': code, 'timed_out': timed_out}
