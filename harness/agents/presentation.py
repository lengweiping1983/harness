"""Agent界面名称独立于稳定的命令标识，用户原文保留。"""
from __future__ import annotations
from typing import Any


def agent_presentation(agent: Any) -> dict[str, str]:
    """读取定义中的名称与职业，不依赖宿主内置角色。"""
    result = {}
    for key in ('display_name', 'profession'):
        value = agent.metadata.get(key)
        if isinstance(value, str) and value.strip():
            result[key] = value.strip()
    result.setdefault('display_name', agent.name)
    result.setdefault('profession', '')
    return result


def team_recipient_snapshot(team: Any, registry: Any, recipients: list[str]) -> dict[str, Any]:
    """冻结发送时的收件人名称，历史展示不依赖当前团队配置。"""
    members = []
    for name in recipients:
        member = next((row for row in team.members if row.name == name), None)
        if member is None:
            continue
        referenced = registry.get(member.agent) if member.agent else None
        display_name = member.display_name or (agent_presentation(referenced)['display_name'] if referenced else name)
        members.append({'name': name, 'display_name': display_name, 'identity': team_member_identity(team.name, member, referenced)})
    return {'team': team.name, 'display_name': agent_presentation(team)['display_name'], 'members': members}


def team_member_identity(team_name: str, member: Any, referenced: Any = None) -> str:
    """配置指纹只用于确认历史发言者，不用显示名称猜测成员身份。"""
    import hashlib
    import json
    from .authoring import markdown
    data = [team_name, member.to_dict(), markdown(referenced) if referenced is not None else None]
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()
