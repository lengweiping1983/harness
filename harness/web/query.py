"""运行查询的单一参数声明，同时供 HTTP 校验与 OpenAPI 使用。"""
from __future__ import annotations

import re
from typing import Any

from harness.i18n import tr
from harness.tools.schema import validate

from .errors import BadRequest
from .router import Request
from .superagent_protocol import READ_QUERY as SUPERAGENT_READ_QUERY


RUNTIME_QUERY = {'type': 'object', 'additionalProperties': False, 'properties': {
    # 负向结束检查兼容公开正则语法，也拒绝 $ 在尾换行前匹配的情况。
    'session_id': {'type': 'string', 'minLength': 1, 'pattern': r'^\S+(?![\s\S])'},
}}
TERMINAL_OUTPUT_QUERY = {'type': 'object', 'additionalProperties': False, 'properties': {
    **RUNTIME_QUERY['properties'],
    'offset': {'type': 'integer', 'minimum': 0, 'maximum': 2**53 - 1, 'default': 0},
    'count': {'type': 'integer', 'minimum': 1, 'maximum': 2000, 'default': 500},
    'tail': {'type': 'boolean', 'default': False},
}}
EVENT_CURSOR = {'type': 'integer', 'minimum': 0, 'maximum': 2**53 - 1, 'default': 0}
LIVE_EVENTS_QUERY = {'type': 'object', 'additionalProperties': False, 'properties': {'since': EVENT_CURSOR}}
SESSION_LIST_QUERY = {'type': 'object', 'additionalProperties': False, 'properties': {
    'limit': {'type': 'integer', 'minimum': 1, 'maximum': 1000},
}}
SESSION_ACTIVITY_QUERY = {'type': 'object', 'additionalProperties': False, 'properties': {
    'after': {'type': 'integer', 'minimum': 0, 'maximum': 2**53 - 1},
    'server_id': {'type': 'string', 'minLength': 1, 'maxLength': 128},
}}
QUERY_SCHEMAS = {
    '/api/v1/superagents/{connection_id}/read':SUPERAGENT_READ_QUERY,
    '/api/v1/sessions': SESSION_LIST_QUERY,
    '/api/v1/sessions/activity': SESSION_ACTIVITY_QUERY,
    '/api/v1/sessions/{session_id}/live-events': LIVE_EVENTS_QUERY,
    '/api/v1/runtime': RUNTIME_QUERY,
    '/api/v1/runtime/terminals/{terminal_id}/output': TERMINAL_OUTPUT_QUERY,
}


def read_query(request: Request, schema: dict[str, Any]) -> dict[str, Any]:
    """拒绝未知、重复和不符合声明的标量；只有缺省字段采用默认值。"""
    properties = schema['properties']
    result = {}

    def invalid(name):
        raise BadRequest(tr('web.query_invalid'), code='request/invalid-query', details={'parameter': name})

    for name, values in request.query.items():
        if name not in properties or len(values) != 1:
            invalid(name)
        raw = values[0]
        kind = properties[name]['type']
        if kind == 'integer':
            if re.fullmatch(r'[+-]?[0-9]+', raw) is None:
                invalid(name)
            try:
                value = int(raw)
            except ValueError:
                invalid(name)
        elif kind == 'boolean':
            if raw not in {'true', 'false'}:
                invalid(name)
            value = raw == 'true'
        elif kind == 'string':
            value = raw
        else:
            raise ValueError('Unsupported query parameter type: ' + kind)
        result[name] = value
    for name, declaration in properties.items():
        if name not in result and 'default' in declaration:
            result[name] = declaration['default']
    errors = validate(result, schema)
    if errors:
        raise BadRequest(tr('web.query_invalid'), code='request/invalid-query', details={'errors': errors})
    return result


def query_parameters(schema: dict[str, Any]) -> list[dict[str, Any]]:
    """公开契约直接引用实际校验声明，不再重复维护字段与边界。"""
    required = schema.get('required', [])
    return [{'name': name, 'in': 'query', 'required': name in required, 'schema': declaration}
            for name, declaration in schema['properties'].items()]


def event_cursor(value: Any = 0) -> int:
    """WebSocket 游标采用与增量 HTTP 查询相同的非负安全整数边界。"""
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise BadRequest(tr('web.query_invalid'), code='request/invalid-query', details={'parameter': 'since'})
    return read_query(Request('GET', '/ws', {'since': [str(value)]}, {}, b''), LIVE_EVENTS_QUERY)['since']
