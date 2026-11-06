"""原工具回执与确切后台作业身份的共享判定，不消费输出或重放执行。"""
from collections.abc import Mapping

from ..session.tool_activity import confirmed_tool_result
from .superagent_common import _error


def _display(call):
    event = call.get('_result_event')
    value = event.data.get('display') if event is not None else None
    return value if isinstance(value, Mapping) else {}


def background_invocation(call):
    """同时识别显式后台请求与执行器超时后提升的后台作业。"""
    start = call.get('_start_event')
    arguments = start.data.get('input') if start is not None else None
    return (isinstance(arguments, Mapping) and arguments.get('run_in_background') is True
            or _display(call).get('background') is True
            # 可提升后台的命令若没有唯一规范回执，不能凭第一份前台结果排除后台副作用。
            or start is not None and start.data.get('name') in {'bash', 'pwsh', 'run_code'}
            and not confirmed_tool_result(call))


def background_status(registry, owner, call, *, archived=True):
    """明确未启动可结束等待；缺失、矛盾或未配对的副作用证据保持未知。"""
    unknown = {'confirmed': False, 'settled': False, 'status': 'unavailable'}
    if not confirmed_tool_result(call):
        return unknown
    display = _display(call)
    identity = display.get('job_id')
    not_started = display.get('not_executed') is True or display.get('execution_state') == 'not-started'
    if not_started:
        if call['_result']['is_error'] is True and not identity and display.get('background') is not True:
            return {'confirmed': True, 'settled': True, 'status': 'not-started'}
        return unknown
    if not isinstance(identity, str) or not identity:
        return unknown
    unknown = {**unknown, 'job_id': identity}
    live = registry._jobs.get(identity)
    if live is not None:
        if live.owner != owner:
            return unknown
        return {'confirmed': True, 'settled': live.is_settled and not live.cleanup_error,
                'job_id': identity, 'status': live.status,
                'stdout_offset': live.stdout.total, 'stderr_offset': live.stderr.total}
    if not archived or registry.archive is None:
        return unknown
    try:
        view = registry.archive.output(owner, identity, limit=1)['job']
    except LookupError:
        return unknown
    except (ValueError, TypeError, KeyError, RuntimeError) as exc:
        raise _error('corrupt') from exc
    if not isinstance(view, Mapping):
        raise _error('corrupt')
    settled = view.get('status') in {'completed', 'failed', 'killed'} and not view.get('cleanupError')
    return {**unknown, 'status': view.get('status'), 'confirmed': bool(settled), 'settled': bool(settled)}
