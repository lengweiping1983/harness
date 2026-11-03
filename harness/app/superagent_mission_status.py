"""从原运行和事件派生只读监督摘要；沉默时间不触发重放。"""
from __future__ import annotations

from ..session.tool_activity import confirmed_tool_result, tool_invocations
from ..session.turn_scope import turn_events
from .superagent_inbox import _error
from .superagent_mission_store import TERMINAL
from .superagent_checkpoints import work_heads


def run_progress(record, events):
    """只投影本次运行的元信息，参数、正文、工具输出和错误原文不进入状态。"""
    try:
        scoped = turn_events(events, run_id=record.id)
    except ValueError as exc:
        raise _error('corrupt') from exc
    calls = tool_invocations(scoped)
    started = [row for row in calls if row['_start_event'] is not None]
    completed = [row for row in started if confirmed_tool_result(row)]
    # 轮次结束或停止不证明工具写入已撤销；无确切结果保留为未确认，不能继续冒充活动。
    active = [row['name'] for row in started if row['status'] == 'running' and row['_result_event'] is None
              and record.status in ('running', 'stopping')]
    last = scoped[-1] if scoped else None
    return {'id': record.id, 'status': record.status, 'settled': record.settled,
            'turn': scoped[0].data['turn'] if scoped else None,
            'created_at': record.created_at, 'started_at': record.started_at, 'finished_at': record.finished_at,
            'last_activity': {'seq': last.seq, 'time': last.time, 'type': last.type} if last else None,
            'tools': {'started': len(started), 'completed': len(completed),
                      'unconfirmed': len(started) - len(completed),
                      'errors': sum(row['_result']['is_error'] for row in completed),
                      'active': [name[:128] for name in active[:16]],
                      'active_truncated': len(active) > 16 or any(len(name) > 128 for name in active[:16])}}


def dependencies(mission, job, jobs):
    """显示计划中的真实前置及是否已创建，不用缺失记录补造完成状态。"""
    heads = work_heads(jobs)
    if job['role'] == 'work':
        expected = [('work', job['node'], job['attempt'] - 1)] if job['attempt'] > 1 else [
            ('work', name, heads.get(name, {}).get('attempt', 1)) for name in job['dependencies']]
    elif job['role'] == 'integrate' and job['attempt'] == 1:
        expected = [('work', step['id'], heads.get(step['id'], {}).get('attempt', 1)) for step in mission['plan']['steps']]
    elif job['role'] == 'review':
        expected = [('integrate', 'goal', job['attempt'])]
    else:
        expected = []
    index = {(item['role'], item['node'], item['attempt']): item for item in jobs}
    result = []
    for key in expected:
        item = index.get(key)
        resolved = 'checkpointed' if job['role'] == 'work' and job['attempt'] > 1 else 'done'
        if item is None or item['state'] != resolved:
            result.append({'node': key[1], 'job_id': item['id'] if item else None,
                           'state': item['state'] if item else None})
    return result


def waiting_reason(mission, job, *, progress, unresolved, background, conversation_busy, at_capacity):
    """等待分类来自确定事实，任务终态不能继承复用会话的新等待。"""
    state = job['state']
    if state in ('done', 'blocked', 'checkpointed'):
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
