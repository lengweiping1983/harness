"""按原 Run 投影执行证据，不依赖旧目标或路由账本。"""
from __future__ import annotations

from ..session.tool_activity import confirmed_tool_result, tool_invocations
from ..session.turn_scope import turn_events
from .superagent_common import _error


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
