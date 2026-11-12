"""项目运行沿用显式 Agent 选择，不依赖任何内置团队或固定成员名。"""
from __future__ import annotations

from pathlib import Path

from ..agents.execution import resolve_team_execution
from .board import BoardError


def project_agent_selection(harness, root: Path, source_session_id: str | None, *, planning: bool = False):
    """启动准备前核对目标项目可见的选择；失效选择不能静默退回默认。"""
    if source_session_id is None or harness.agent_mount is None:
        return None
    projection = harness.agent_mount.active_projection(source_session_id)
    if projection is None:
        return None
    registry = harness.agents.for_project(root)
    agent = registry.get(projection.agent)
    if agent.is_team:
        agent = resolve_team_execution(agent, registry)
        for member in agent.members:
            if member.agent:
                registry.get(member.agent)
        external = any(member.runner for member in agent.members)
    else:
        external = bool(agent.metadata.get('runner'))
    if planning and external:
        raise BoardError('board/planning-read-only')
    return agent.name


async def bind_project_agent(harness, session, selection):
    """任务分支由项目运行管理，选择身份时不另建成员工作区。"""
    if selection is not None:
        await harness.set_agent_selection(session.id, selection,
            team_worktrees=False, team_working_copy=False)
