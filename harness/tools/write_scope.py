"""核验文件写入目标与命令目录，不把路径检查冒充系统沙箱。"""
from __future__ import annotations

import inspect
from pathlib import Path

from harness.i18n import tr


def coordinator_tool_allowed(definition) -> bool:
    """隔离主理人只保留观察和现有任务控制入口，不推测远程工具副作用。"""
    controls = {'spawn_agent', 'followup_task', 'send_message', 'wait_agent', 'wait_agents', 'list_agents',
                'interrupt_agent', 'create_goal', 'update_goal', 'get_goal',
                'todo_write', 'todo_update', 'todo_bind', 'ask_user_question',
                'project_task_save', 'project_tasks_generate'}
    return definition.effect == 'read' or definition.schema.name in controls


def write_scope_denial(definition, args, ctx) -> str | None:
    """复核隔离主理人权限及成员工作树声明路径，不冒充系统沙箱。"""
    coordinator = ctx.services.get('isolated_team_coordinator')
    if callable(coordinator) and coordinator() and not coordinator_tool_allowed(definition):
        return tr('agent.team.coordinator_write_denied')
    scope = ctx.services.get('workspace_write_root')
    if scope is None or definition.write_paths is None and definition.execution_cwd is None:
        return None
    try:
        targets = declared_targets(definition, args, ctx)
        if not targets:
            return None
        if not isinstance(scope, (str, Path)):
            raise ValueError
        root = Path(scope)
        # 根目录已由宿主规范化；运行中被替换成链接时不能重新扩大授权。
        if not root.is_absolute() or not root.is_dir() or root.resolve() != root:
            raise ValueError
        for target, kind in targets:
            if not target.is_relative_to(root):
                return tr('agent.workspace.command_denied' if kind == 'execute' else 'agent.workspace.write_denied', path=str(target), root=str(root))
    except Exception:
        return tr('agent.workspace.write_invalid')
    return None


def declared_targets(definition, args, ctx) -> tuple[tuple[Path, str], ...]:
    """解析可信工具声明的目标，供路径授权和写入协调共同使用。"""
    def synchronous(callback):
        value = callback(args, ctx)
        if inspect.isawaitable(value):
            if inspect.iscoroutine(value):
                value.close()
            raise ValueError(tr('runtime.validation.41ec7c1059a9'))
        return value

    targets = []
    if definition.write_paths is not None:
        values = synchronous(definition.write_paths)
        if isinstance(values, (str, bytes, Path)):
            raise ValueError(tr('runtime.validation.a97bc4b3f952'))
        targets.extend((target, 'write') for target in values)
    if definition.execution_cwd is not None:
        directory = synchronous(definition.execution_cwd)
        if directory is not None:
            targets.append((directory, 'execute'))
    result = []
    for target, kind in targets:
        if not isinstance(target, (str, Path)) or not Path(target).is_absolute():
            raise ValueError(tr('runtime.validation.ee6fa88410f8'))
        result.append((Path(target).resolve(), kind))
    return tuple(result)
