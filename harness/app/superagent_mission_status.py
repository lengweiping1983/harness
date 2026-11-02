"""从原运行和事件派生只读监督摘要；沉默时间不触发重放。"""
from __future__ import annotations

from ..session.tool_activity import confirmed_tool_result, tool_invocations
from ..session.turn_scope import turn_events
from .superagent_inbox import _error
from .superagent_mission_store import TERMINAL


def run_progress(record, events):
    """只投影本次运行的元信息，参数、正文、工具输出和错误原文不进入状态。"""
    try:
        scoped = turn_events(events, run_id=record.id)
    except ValueError as exc:
        raise _error('corrupt') from exc
    calls = tool_invocations(scoped)
    active = [row['name'] for row in calls if row['_start_event'] is not None and row['_result_event'] is None]
    last = scoped[-1] if scoped else None
    return {'id': record.id, 'status': record.status, 'settled': record.settled,
            'created_at': record.created_at, 'started_at': record.started_at, 'finished_at': record.finished_at,
            'last_activity': {'seq': last.seq, 'time': last.time, 'type': last.type} if last else None,
            'tools': {'started': sum(row['_start_event'] is not None for row in calls),
                      'completed': sum(confirmed_tool_result(row) for row in calls),
                      'errors': sum(confirmed_tool_result(row) and row['_result']['is_error'] for row in calls),
                      'active': [name[:128] for name in active[:16]],
                      'active_truncated': len(active) > 16 or any(len(name) > 128 for name in active[:16])}}


def dependencies(mission, job, jobs):
    """显示计划中的真实前置及是否已创建，不用缺失记录补造完成状态。"""
    if job['role'] == 'work':
        expected = [('work', name, 1) for name in job['dependencies']]
    elif job['role'] == 'integrate' and job['attempt'] == 1:
        expected = [('work', step['id'], 1) for step in mission['plan']['steps']]
    elif job['role'] == 'review':
        expected = [('integrate', 'goal', job['attempt'])]
    else:
        expected = []
    index = {(item['role'], item['node'], item['attempt']): item for item in jobs}
    result = []
    for key in expected:
        item = index.get(key)
        if item is None or item['state'] != 'done':
            result.append({'node': key[1], 'job_id': item['id'] if item else None,
                           'state': item['state'] if item else None})
    return result


def waiting_reason(mission, job, *, progress, unresolved, background, conversation_busy, at_capacity):
    """等待分类来自确定事实，任务终态不能继承复用会话的新等待。"""
    state = job['state']
    if state in ('done', 'blocked'):
        return None
    if state == 'unknown':
        return 'result-reconciliation'
    if state == 'accepted' and progress:
        if progress['status'] in ('pending', 'queued'):
            return 'run-queue'
        if progress['status'] in ('running', 'stopping'):
            return 'run-stop' if progress['status'] == 'stopping' else 'run'
        if not progress['settled']:
            return 'run-cleanup'
        if background:
            return 'background-jobs'
    if mission['state'] in TERMINAL:
        return 'mission-halted'
    if state in ('importing', 'capturing'):
        return 'delivery-reconciliation'
    if state == 'submitting':
        return 'submission-reconciliation'
    if state == 'accepted':
        if progress and progress['status'] != 'completed':
            return 'failure-settlement'
        return 'plan-validation' if job['role'] == 'plan' else 'delivery-capture'
    if unresolved:
        return 'dependencies'
    if state == 'ready' and conversation_busy:
        return 'conversation-busy'
    if job['role'] != 'plan' and at_capacity:
        return 'worker-capacity'
    return 'dispatch' if state == 'ready' else 'workspace-preparation'
