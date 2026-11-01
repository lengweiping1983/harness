"""共享浏览器的公开投影；身份授权与公开内容裁剪是独立边界。"""
from __future__ import annotations

from collections.abc import Mapping
from ..app.collaboration_errors import collaboration_error_message


def select(value, fields):
    return {key: value[key] for key in fields if key in value}


def header(value):
    """公开会话标识与标题，不向访客分发宿主路径及内部元数据。"""
    return select(value, ('id', 'title', 'summary', 'version', 'created_at', 'updated_at', 'messages', 'last_at'))


def message(value):
    """公共消息只保留正文、作者和持久位置，不包含推理、工具参数或执行结果。"""
    row = select(value, ('kind', 'role', 'text', 'source_seq', 'source_time', 'source', 'turn', 'requester',
                         'request_id', 'run_id', 'author', 'speaker', 'goal_id', 'partial', 'stop_reason'))
    if isinstance(value.get('content'), (list, tuple)):
        row['content'] = [select(block, ('type', 'text')) for block in value['content']
                          if isinstance(block, Mapping) and block.get('type') == 'text']
    return row


def detail(value):
    """历史分页仍使用原持久序号与游标；所有读取方式共用同一内容白名单。"""
    result = select(value, ('history', 'events_view', 'turn', 'interrupted', 'storage_error'))
    result['header'] = header(value.get('header', {}))
    result['messages'] = [message(row) for row in value.get('messages', ()) if row.get('role') in {'user', 'assistant'}]
    result['events'] = [row for item in value.get('events', ()) if (row := event(item)) is not None]
    result['turn_events'] = [row for item in value.get('turn_events', ()) if (row := event(item)) is not None]
    result.update(usage={}, todos=[], stats={}, timeline=[])
    if 'questions' in value:
        result['questions'] = value['questions']
    return result


def event(value):
    """落盘事件保留原位置；拒绝将私有 note 当作公共历史转发。"""
    kind = value.get('type')
    if kind not in {'user/message', 'assistant/message', 'turn/start', 'turn/end'}:
        return None
    payload = value.get('data', {})
    data = message(payload) if kind in {'user/message', 'assistant/message'} else select(payload, ('turn', 'kind'))
    if kind == 'turn/end' and isinstance(payload.get('reason'), Mapping):
        data['reason'] = select(payload['reason'], ('kind',))
    return {**select(value, ('type', 'seq', 'time')), 'data': data}


def frame(value):
    """WS 首帧、实时帧及重放使用同一投影，未知事件默认不公开。"""
    kind = value.get('type')
    common = select(value, ('type', 'seq', 'session_id', 'source_seq', 'source_time', 'turn'))
    if kind == 'pong':
        return {'type': 'pong', **select(value, ('t',))}
    if kind == 'ready':
        return {**select(value, ('type', 'session_id', 'last_seq', 'pending', 'paused', 'pause_reason', 'updated_at',
                               'waiting_reason', 'running', 'server_id', 'reset_required', 'oldest_seq')),
                'snapshot': detail(value.get('snapshot', {})),
                'draft': select(value.get('draft') or {}, ('text', 'turn', 'speaker')),
                'replay': [row for item in value.get('replay', ()) if (row := frame(item)) is not None]}
    if kind in {'user/message', 'assistant/message', 'assistant/partial'}:
        return {**common, **message(value)}
    if kind == 'assistant/delta' and value.get('delta_type') == 'text':
        return {**common, **select(value, ('delta_type', 'delta', 'request_id', 'speaker'))}
    if kind in {'queue_state', 'run/state', 'done', 'turn/start', 'turn/end', 'stopping', 'request/start', 'request/end', 'request/progress'}:
        return {**common, **select(value, ('id', 'status', 'pending', 'running', 'paused', 'pause_reason', 'waiting_reason',
                                         'updated_at', 'request_id', 'kind', 'started_at', 'finished_at'))}
    if kind in {'user_question/ask', 'user_question/answered', 'user_question/closed'}:
        return {**common, **select(value, ('question_id', 'questions', 'expires_at', 'timeout', 'closed'))}
    if kind == 'error':
        code = value.get('code') or 'run/error'
        return {**common, 'code': code, 'message': collaboration_error_message(code)}
    return {'type': 'shared/omitted', **select(value, ('seq', 'session_id'))} if type(value.get('seq')) is int else None
