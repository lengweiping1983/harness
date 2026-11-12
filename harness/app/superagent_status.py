"""统一账本的有界只读状态，不把最后一次运行投影视作进程存活证明。"""
from __future__ import annotations



def snapshot(store, *, runtime=None, limit=32, mission_id=None):
    with store.transaction() as db:
        counts_by_state = {row['state']:row['n'] for row in db.execute('SELECT state,count(*) n FROM missions GROUP BY state')}
        where, params = ('WHERE mission_id=?', [mission_id]) if mission_id is not None else ('', [])
        rows = db.execute('SELECT * FROM missions ' + where + " ORDER BY CASE "
            "WHEN state IN ('planning','running','integrating','reviewing','cancelling') THEN 0 "
            "WHEN state IN ('unknown','blocked') THEN 1 ELSE 2 END,created_at DESC,sequence DESC LIMIT ?", (*params,limit + 1)).fetchall()
        missions = []
        for row in rows[:limit]:
            mission = dict(row)
            jobs = db.execute('SELECT * FROM jobs WHERE mission_id=? ORDER BY node_id LIMIT 101', (row['mission_id'],)).fetchall()
            edges = [dict(edge) for edge in db.execute('SELECT * FROM job_dependencies WHERE mission_id=?', (row['mission_id'],))]
            items = []
            for job_row in jobs[:100]:
                job = dict(job_row)
                assignment = db.execute('SELECT * FROM assignments WHERE job_id=? ORDER BY attempt_no DESC LIMIT 1', (job['job_id'],)).fetchone()
                job['assignment'] = {k:v for k,v in dict(assignment).items() if k != 'report_json'} if assignment else None
                if assignment:
                    key = assignment['assignment_id']
                    job['run'] = (runtime or {}).get('active_runs', {}).get(key)
                unresolved = dependencies(job, [dict(item) for item in jobs], edges)
                progress = job.get('run')
                job['dependencies'] = unresolved
                job['waiting'] = waiting_reason(mission, {'state': assignment['state'] if assignment else job['state']},
                    progress=progress, unresolved=unresolved, background=bool((progress or {}).get('unsettled_background')))
                items.append(job)
            mission['jobs'], mission['jobs_has_more'] = items, len(jobs) > 100
            control = db.execute('SELECT * FROM mission_controls WHERE mission_id=?', (row['mission_id'],)).fetchone()
            mission['control'] = dict(control) if control else None
            missions.append(mission)
        counts = {row['state']: row['n'] for row in db.execute('SELECT state,count(*) n FROM outbox_messages GROUP BY state')}
        tickets = [dict(row) for row in db.execute('SELECT ticket_id,state,title FROM tickets ORDER BY updated_at DESC LIMIT ?', (limit,))]
    return {'instance_id': store.instance_id, 'runtime_state': (runtime or {}).get('runtime_state', 'unobserved'),
            'runtime_observation': runtime, 'missions': {'recent': missions, 'has_more': len(rows) > limit,
                'counts': counts_by_state, 'total':sum(counts_by_state.values())},
            'tickets': tickets, 'outbox': counts}


def dependencies(job, jobs, edges):
    """依赖清单来自冻结图；缺失 Job 明确保留，不能补造完成。"""
    index = {item['job_id']: item for item in jobs}
    result = []
    for edge in edges:
        if edge['job_id'] != job['job_id']:
            continue
        source = index.get(edge['depends_on_job_id'])
        if source is None or source['state'] != 'done':
            result.append({'job_id': edge['depends_on_job_id'], 'state': source['state'] if source else None})
    return result


def waiting_reason(mission, job, *, progress, unresolved, background, conversation_busy=False, at_capacity=False):
    """运行、收尾、依赖和未知结果分别表达；摘要不触发执行。"""
    state = job['state']
    if state in {'done', 'blocked', 'checkpointed', 'succeeded', 'failed', 'interrupted', 'cancelled', 'rejected'}:
        return None
    if state == 'unknown':
        return 'result-reconciliation'
    if state in {'accepted', 'running', 'stopping', 'capturing'} and progress:
        if progress['status'] in {'pending', 'queued'}:
            return 'run-queue'
        if progress['status'] in {'running', 'stopping'}:
            return 'run-stop' if progress['status'] == 'stopping' else 'run'
        if not progress['settled']:
            return 'run-cleanup'
        if background:
            return 'background-jobs'
    if mission['state'] in {'completed', 'blocked', 'cancelled', 'rejected', 'suppressed'}:
        return 'mission-halted'
    if state == 'capturing':
        return 'delivery-reconciliation'
    if state == 'submitting':
        return 'submission-reconciliation'
    if state == 'accepted':
        return 'failure-settlement' if progress and progress['status'] != 'completed' else 'delivery-capture'
    if unresolved:
        return 'dependencies'
    if state == 'ready' and conversation_busy:
        return 'conversation-busy'
    if at_capacity:
        return 'worker-capacity'
    return 'dispatch' if state == 'ready' else 'workspace-preparation'
