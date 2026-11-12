"""使用结构化成员标识派工，正文与引用不参与路由。"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from harness.i18n import tr
from ..core.errors import HarnessError
from ..agents.authoring import markdown


def prepare_team_route(harness: Any, session: Any, recipients: Any, *, expected_team: str | None = None, selection_revision: int | None = None, recipient_identities: Any = None) -> dict[str, Any]:
    """冻结目标成员与配置摘要，入队和执行时都验证当前配置。"""
    if not isinstance(recipients, list) or not recipients or len(recipients) > 8:
        raise HarnessError(tr('collaboration.invalid_recipients'), code='team/invalid-recipients')
    if any(not isinstance(value, str) or not value.strip() or value != value.strip() for value in recipients):
        raise HarnessError(tr('collaboration.invalid_recipients'), code='team/invalid-recipients')
    selected = harness.agent_mount.resolve_active(session.id) if harness.agent_mount else None
    if not selected or not selected[1].is_team:
        raise HarnessError(tr('collaboration.team_required'), code='team/selection-required')
    team = selected[1]
    if expected_team is not None and expected_team != team.name:
        raise HarnessError(tr('collaboration.configuration_changed'), code='team/configuration-changed')
    current_selection = harness.agent_mount_state(session)['revision']
    if selection_revision is not None and (type(selection_revision) is not int or selection_revision != current_selection):
        raise HarnessError(tr('collaboration.configuration_changed'), code='team/configuration-changed')
    names = {member.name for member in team.members}
    if any(value not in names for value in recipients):
        raise HarnessError(tr('collaboration.unknown_member'), code='team/unknown-member')
    ordered = list(dict.fromkeys(recipients))
    registry = harness.agents_for(session)
    from ..agents.presentation import team_member_identity
    identities = {member.name: team_member_identity(team.name, member, registry.get(member.agent) if member.agent else None)
                  for member in team.members if member.name in ordered}
    if recipient_identities is not None:
        if not isinstance(recipient_identities, dict) or set(recipient_identities) != set(ordered):
            raise HarnessError(tr('collaboration.invalid_recipients'), code='team/invalid-recipients')
        if recipient_identities != identities:
            raise HarnessError(tr('collaboration.configuration_changed'), code='team/configuration-changed')
    definitions = [markdown(team)]
    for member in team.members:
        if member.name in ordered and member.agent:
            definitions.append(markdown(registry.get(member.agent)))
    revision = hashlib.sha256(json.dumps(definitions, ensure_ascii=False).encode('utf-8')).hexdigest()
    return {'team': team.name, 'revision': revision, 'recipients': ordered, 'recipient_identities': identities, 'selection_revision': current_selection}


def validate_team_route(harness: Any, session: Any, route: Any) -> dict[str, Any]:
    """队列期间配置改变时明确拒绝，不将任务静默交给新身份。"""
    if (not isinstance(route, dict) or set(route) != {'team', 'revision', 'recipients', 'selection_revision', 'recipient_identities'}
            or type(route['selection_revision']) is not int or not isinstance(route['recipient_identities'], dict)):
        raise HarnessError(tr('collaboration.invalid_recipients'), code='team/invalid-recipients')
    current = prepare_team_route(harness, session, route['recipients'])
    if any(current[key] != value for key, value in route.items()):
        raise HarnessError(tr('collaboration.configuration_changed'), code='team/configuration-changed')
    return current
