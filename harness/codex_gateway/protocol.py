"""隔离网关的协议源；原生 Responses 扩展字段保持开放。"""
import json

from ..i18n import tr


PREFIX = '/api/v1/codex'


def decode_object(raw):
    """严格读取 UTF-8、唯一字段和有限数值，不补造缺失对象。"""
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError()
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError()

    result = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=invalid_constant)
    if not isinstance(result, dict):
        raise ValueError()
    return result


def openapi():
    """独立监听的地址不混入主 Web 服务的路由契约。"""
    native = {'type': 'object', 'required': ['model', 'input'], 'properties': {
        'model': {'type': 'string', 'minLength': 1, 'maxLength': 256},
        'input': {'oneOf': [{'type': 'string'}, {'type': 'array', 'items': {'type': 'object'}}]},
        'store': {'const': False}, 'background': {'const': False},
        'previous_response_id': {'type': 'null'}, 'conversation': {'type': 'null'}}, 'additionalProperties': True}
    error = {'description': tr('ui.codexGateway.protocolError'), 'content': {'application/json': {'schema': {
        'type': 'object', 'required': ['error'], 'properties': {'error': {'type': 'object',
            'required': ['code', 'message'], 'properties': {'code': {'type': 'string'}, 'message': {'type': 'string'}}}}}}}}
    paths = {}
    for suffix, compact in (('/responses', False), ('/responses/compact', True)):
        schema = {**native, 'required': native['required'] + ([] if compact else ['stream']),
                  'properties': {**native['properties'], **({} if compact else {'stream': {'const': True}})}}
        paths[PREFIX + suffix] = {'post': {'requestBody': {'required': True, 'content': {
            'application/json': {'schema': schema}}}, 'responses': {
                '200': {'description': tr('ui.codexGateway.protocolStream'), 'content': {
                    'application/json' if compact else 'text/event-stream': {'schema': {
                        'type': 'object' if compact else 'string'}}}}, 'default': error}}}
    paths[PREFIX + '/models'] = {'get': {'responses': {'200': {'description': tr('ui.codexGateway.protocolModels'),
        'content': {'application/json': {'schema': {'type': 'object', 'required': ['models'],
            'properties': {'models': {'type': 'array', 'items': {'type': 'object'}}}}}}}, 'default': error}}}
    return {'openapi': '3.1.0', 'info': {'title': tr('ui.codexGateway.providerName'), 'version': '1'},
            'security': [{'gateway': []}], 'paths': paths, 'components': {'securitySchemes': {
                'gateway': {'type': 'http', 'scheme': 'bearer'}}}}
