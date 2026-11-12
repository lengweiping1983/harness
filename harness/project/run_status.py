"""按任务合同绑定的正式运行读取轻量状态，不修改任务或启动执行。"""
from pathlib import Path
from harness.core.errors import HarnessError
from .board import TaskBoard


def project_runs(harness, root: Path):
    """检查会话、运行及项目三方身份，避免泄漏其他项目的执行内容。"""
    root = root.resolve()
    board = TaskBoard(root).read()
    rows = {}
    for task in board['tasks']:
        prefix = 'review' if task['verification_policy'] == 'artifact' and task['status'] in {'REVIEW', 'DONE'} and task.get('review_run_id') else 'execution'
        identifier = task.get(prefix + '_run_id')
        if not identifier:
            continue
        unavailable = {'available': False, 'reason': 'board/execution-unavailable'}
        record = harness.runtime.records.get(identifier)
        if record is None or record.session_id != task.get(prefix + '_session_id'):
            rows[task['id']] = unavailable
            continue
        try:
            session = harness.get_session(record.session_id)
            if not session.header.cwd or Path(session.header.cwd).resolve() != root:
                rows[task['id']] = unavailable
                continue
            control = harness.runtime.queue_control(session.id)
        except (HarnessError, OSError):
            rows[task['id']] = unavailable
            continue
        active = record.status in {'queued', 'running'} and record.task is not None and not record.task.done()
        active = active or prefix == 'review' and task['status'] == 'REVIEW' and not task.get('review_error') and identifier in harness.project_execution.reviews
        rows[task['id']] = {'available': True, 'run_id': record.id, 'session_id': session.id,
            'status': record.status, 'active': active,
            'paused': bool(control['paused']) if record.status == 'queued' else False,
            'waiting_reason': control.get('waiting_reason', '') if record.status == 'queued' else '',
            'started_at': record.started_at, 'finished_at': record.finished_at}
    return {'revision': board['revision'], 'runs': rows}
