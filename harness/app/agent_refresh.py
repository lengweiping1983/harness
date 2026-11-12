"""配置保存后同步已打开聊天，按真实文件路径隔离同名Agent。"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from ..core.errors import HarnessError


async def refresh_agent_sessions(harness: Any, changed_path: str | Path) -> dict[str, Any]:
    """刷新目录并推进受影响的选择版本，保留团队工作区选项与人格覆盖。"""
    harness.refresh_agents()
    changed = Path(changed_path).resolve()
    updated, errors = [], []
    if harness.agent_mount is None:
        return {'refreshed_sessions': updated, 'refresh_errors': errors}
    for session in harness.sessions.loaded_sessions():
        try:
            resolved = harness.agent_mount.resolve_active(session.id)
            if resolved is None:
                continue
            projection, agent = resolved
            catalog = {row.name: row for row in harness.agents_for(session).list_all()}
            dependencies = [agent] + [catalog[member.agent] for member in agent.members if member.agent in catalog]
            if not any(row.path and Path(row.path).resolve() == changed for row in dependencies):
                continue
            await harness.agent_mount.set(session, agent.name, team_worktrees=projection.team_worktrees,
                team_working_copy=projection.team_working_copy)
            await harness.bus.emit('agent/loaded', session.id, {'action': 'configuration-refresh',
                'name': agent.name, 'agent_type': agent.agent_type})
            updated.append(session.id)
        except (HarnessError, OSError) as exc:
            errors.append({'session_id': session.id, 'code': exc.code if isinstance(exc, HarnessError) else 'agent/refresh-io',
                'message': exc.message if isinstance(exc, HarnessError) else str(exc)})
    return {'refreshed_sessions': updated, 'refresh_errors': errors}
