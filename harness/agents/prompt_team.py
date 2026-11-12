"""团队主会话的人格与原语说明；宿主负责身份、依赖、权限及验收。"""

from __future__ import annotations

from harness.i18n import tr

from .parser import AgentMember, AgentDefinition
from .registry import AgentRegistry
from .members import lead_member, lead_persona, member_model, referenced_agent

def lead_model(team: AgentDefinition, registry: AgentRegistry) -> str | None:
    """主理人的模型覆盖（提示词驱动下主理人就是会话主 agent）。

    解析顺序与引擎驱动的成员级覆盖一致：成员固定模型 → 引用Agent模型 →
    团队默认模型。明确引用失效时返回配置错误，不静默退回默认模型。
    """
    return member_model(lead_member(team), team, registry)


def member_duty(member: AgentMember, registry: AgentRegistry) -> str:
    """花名册里的一行职责：成员内联指令优先，回退到引用Agent的一句话描述。"""
    if member.prompt:
        return member.prompt
    if member.agent:
        try:
            return referenced_agent(member, registry).description
        except Exception:
            return ""
    return ""


def _roster_rows(team: AgentDefinition, registry: AgentRegistry) -> str:
    """渲染花名册：主理人据此知道有哪些成员、各自职责与依赖。"""
    rows: list[str] = []
    for member in team.members:
        if member.role == 'lead' and len(team.members) > 1:
            continue  # 主理人就是模型自己，不进派工花名册
        duty = member_duty(member, registry)
        depends = tr('agent.prompt_team.roster_depends', p0=', '.join(member.depends_on)) if member.depends_on else ''
        agent_ref = tr('agent.prompt_team.roster_agent', p0=member.agent) if member.agent else ''
        row = tr('agent.prompt_team.roster_row', p0=member.name, p1=member.role,
                 p2=f"{depends}{agent_ref}", p3=duty)
        rows.append(row)
    return "\n".join(rows)


def render_team_lead_section(team: AgentDefinition, registry: AgentRegistry) -> str:
    """渲染团队主理人、成员目录及通用协作原语。"""
    if not team.is_team or not team.members:
        return ""
    persona = lead_persona(team, registry)
    roster = _roster_rows(team, registry)
    return tr(
        'agent.prompt_team.section',
        p0=persona,
        p1=roster,
        p2=tr('agent.team.collaboration'),
    )


def render_activation_notice(team: AgentDefinition, registry: AgentRegistry, task: str = "") -> str:
    """召唤提示词驱动团队时的激活通知（工具结果 / 中途挂载用）。

    系统提示的人格片段要下一回合才生效，因此当次回合的工具结果里带上完整
    片段，主理人拿到结果即可开始编排，不必等下一轮。
    """
    section = render_team_lead_section(team, registry)
    return tr('agent.prompt_team.activated', p0=team.name, p1=section, p2=task)



__all__ = [
    "lead_model",
    "member_duty",
    "render_team_lead_section",
    "render_activation_notice",
]
