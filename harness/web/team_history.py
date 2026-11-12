"""Agent 团队日志的只读恢复投影，不自动重放未完成工作。"""
from __future__ import annotations
from typing import Any
from collections.abc import Mapping
from harness.i18n import tr


def latest_team_events(events):
    """按运行标识选取最新快照，历史分页中每个团队只占一个单元。"""
    latest = {}
    for event in events:
        if event.type == 'agent/team' and isinstance(event.data, Mapping) and isinstance(event.data.get('run_id'), str) and event.data['run_id']:
            latest[event.data['run_id']] = event
    return list(latest.values())


def team_event_view(event: Any, *, session_id: str, active_runs=()) -> dict[str, Any]:
    """运行不在当前进程时显示中断，保留落盘分工和已完成成员。"""
    row = event.to_dict()
    data = row['data']
    if not isinstance(data, dict) or not isinstance(data.get('run_id'), str):
        return row
    if data.get('phase') not in {'done', 'failed', 'interrupted'} and (session_id, data.get('run_id')) not in active_runs:
        data['phase'] = 'interrupted'
        data['recovered'] = True
        data['error'] = tr('agent.team.recovered')
        for member in data.get('members', []):
            if isinstance(member, dict) and member.get('status') in {'pending', 'running'}:
                member['status'] = 'interrupted'
    return row
