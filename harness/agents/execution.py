"""在团队执行边界解析Agent引用，保证界面、导入及工具入口采用同一规则。"""
from __future__ import annotations

import dataclasses
from harness.i18n import tr
from ..core.errors import AgentError
from .parser import AgentDefinition
from .registry import AgentRegistry


def resolve_team_execution(team: AgentDefinition, registry: AgentRegistry, *, recipients: list[str] | None = None) -> AgentDefinition:
    """返回独立运行快照，不回写用户配置或注册表中的可复用Agent。"""
    members = []
    for member in team.members:
        if recipients is not None and member.name not in recipients:
            members.append(member)
            continue
        runner = member.runner
        if not runner and not member.model and member.execution != 'model' and member.agent:
            referenced = registry.get(member.agent)
            if referenced.is_team:
                raise AgentError(tr('agent.team.reference_is_team', member=member.name, agent=member.agent), agent=team.name)
            runner = referenced.metadata.get('runner')
        if runner and (member.role == 'lead' or member.model):
            raise AgentError(tr('agent.team.inherited_runner_lead', member=member.name), agent=team.name)
        if runner and member.skills:
            raise AgentError(tr('ui.agent.skills.externalHint'), agent=team.name)
        members.append(dataclasses.replace(member, runner=runner))
    return dataclasses.replace(team, members=tuple(members))
