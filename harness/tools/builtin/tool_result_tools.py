"""只读分页取回当前会话的原始工具结果，包括已压缩的历史证据。"""
from __future__ import annotations
from harness.i18n import tr

from ...core.errors import ToolExecutionError
from ...core.jsonutil import to_jsonable
from ..registry import ToolDefinition


def read_tool_result(session, call_id: str, *, offset: int = 0, limit: int = 4096, query: str | None = None) -> dict:
    """按字符范围读取原件；不跨会话，不重跑原工具，不吞掉缺失证据。"""
    if not isinstance(call_id, str) or not call_id or type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 8192:
        raise ToolExecutionError(tr('tool.result.ad1c4d8d6e01'))
    if session is None:
        raise ToolExecutionError(tr('tool.result.5df09a2d8aaf'))
    if query is not None and (not isinstance(query, str) or not 1 <= len(query) <= min(256, limit)):
        raise ToolExecutionError(tr('tool.result.89692f6fcd28'))
    matches, digest = session.tool_results.lookup(call_id)
    if len(matches) != 1:
        raise ToolExecutionError(tr('tool.result.55ba8a63e2ec'))
    event, item = matches[0]
    if item is None or not isinstance(item.get('content'), str):
        raise ToolExecutionError(tr('tool.result.ec204a427e74'))
    text = item['content']
    if offset > len(text):
        raise ToolExecutionError(tr('tool.result.a9552fe1bce8'))
    match = None
    if query is not None:
        found = text.find(query, offset)
        match = found if found >= 0 else None
        if match is not None:
            offset = max(offset, match - min(256, (limit - len(query)) // 2))
    end = min(len(text), offset + limit)
    result = {'call_id': call_id, 'event_seq': event.seq, 'name': event.data.get('name'),
            'is_error': bool(item.get('is_error')), 'sha256': digest,
            'total_chars': len(text), 'offset': offset, 'next_offset': end if end < len(text) else None,
            'text': text[offset:end]}
    if query is not None:
        result['match_offset'] = match
        if match is None:
            result.update(text='', next_offset=None)
    return result


def build_tool_result_reader() -> ToolDefinition:
    """取回入口只使用宿主绑定的会话，模型不能传入另一会话身份。"""
    def execute(args, ctx):
        return to_jsonable(read_tool_result(ctx.services.get('session'), args['call_id'],
                           offset=args.get('offset', 0), limit=args.get('limit', 4096), query=args.get('query')))

    return ToolDefinition(schema={'name': 'read_tool_result', 'description':
        tr('tool.result.reader_description'), 'description_key': 'tool.result.reader_description',
        'parameters': {'type': 'object', 'properties': {
            'call_id': {'type': 'string', 'minLength': 1},
            'offset': {'type': 'integer', 'minimum': 0},
            'query': {'type': 'string', 'minLength': 1, 'maxLength': 256},
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 8192}},
            'required': ['call_id'], 'additionalProperties': False}}, execute=execute,
        effect='read', idempotent=True, concurrency_safe=True)
