"""共享成员定义解析：不依赖任何具体团队编排或执行器。"""
from __future__ import annotations

from harness.i18n import tr
from ..core.errors import AgentError
from .parser import AgentMember, AgentDefinition
from .registry import AgentRegistry


def referenced_agent(member: AgentMember, registry: AgentRegistry) -> AgentDefinition:
    """解析成员引用的单Agent；明确引用不能悄悄退化。"""
    try:
        agent = registry.get(member.agent or '')
    except AgentError as exc:
        raise AgentError(tr('agent.team.reference_unavailable', member=member.name,
                             agent=member.agent), agent=member.agent) from exc
    if agent.is_team:
        raise AgentError(tr('agent.team.reference_is_team', member=member.name,
                             agent=member.agent), agent=member.agent)
    return agent


def lead_member(team: AgentDefinition) -> AgentMember:
    """选择显式 lead；未声明时使用首位成员。"""
    return next((member for member in team.members if member.role == 'lead'), team.members[0])


def member_skills(member: AgentMember, team: AgentDefinition, registry: AgentRegistry) -> tuple[str, ...] | None:
    """依次采用成员、引用Agent、团队的显式技能，否则继承默认。"""
    if member.skills is not None:
        return member.skills
    if member.agent:
        selected = referenced_agent(member, registry).skills
        if selected is not None:
            return selected
    return team.skills


def member_persona(member: AgentMember, team: AgentDefinition, registry: AgentRegistry, *, include_personality: bool = True, include_team_method: bool = True) -> str:
    """渲染成员角色说明（引用Agent正文 + 内联指令 + 人格）。"""
    parts: list[str] = []
    if member.agent:
        referenced = referenced_agent(member, registry)
        parts.append(f'<agent name="{referenced.name}">\n' + tr('python.team.7fa60cc65215', p0=referenced.name, p1=referenced.system_prompt).strip() + '\n</agent>')
        from .personality import render_agent, system_personality
        personality = render_agent(referenced)
        if personality and include_personality:
            parts.append(system_personality(personality))
    if member.prompt:
        parts.append(tr('python.team.73fad22c814f', p0=f'{member.prompt}').strip())
    if not parts:
        parts.append(tr('python.team.c86434105456', p0=f'{team.name}', p1=f'{member.name}', p2=f'{team.description}').strip())
    if include_team_method:
        parts.insert(0, tr('agent.team.expert_method'))
    header = f"<team_member name=\"{member.name}\" role=\"{member.role}\" team=\"{team.name}\">\n"
    footer = "\n</team_member>"
    return header + "\n\n".join(parts) + footer


def lead_persona(team: AgentDefinition, registry: AgentRegistry) -> str:
    """团队约定与协调者自身的Agent、人格及内联职责共同生效。"""
    header = f"<team_lead team=\"{team.name}\">\n"
    footer = "\n</team_lead>"
    body = team.system_prompt.strip() or (
        tr('python.team.e37133e90494', p0=f'{team.name}', p1=f'{team.description}')
    )
    from .personality import render_agent, system_personality
    personality = render_agent(team)
    if personality:
        body += "\n\n" + system_personality(personality)
    lead = lead_member(team)
    if lead.agent or lead.prompt:
        body += "\n\n" + member_persona(lead, team, registry, include_personality=not bool(personality), include_team_method=False)
    body += "\n\n" + tr('agent.team.expert_method')
    return header + body + footer


def member_model(member: AgentMember, team: AgentDefinition, registry: AgentRegistry) -> str | None:
    """成员模型覆盖的解析顺序。

    成员固定模型优先；明确选择聊天模型且留空时跟随父配置。
    其余情况按引用Agent模型、团队默认模型、父配置依次继承。
    """
    if member.runner:
        return None
    if member.model:
        return member.model
    if member.execution == 'model':
        return None
    if member.agent:
        referenced = referenced_agent(member, registry)
        if referenced.model:
            return referenced.model
    return team.model
