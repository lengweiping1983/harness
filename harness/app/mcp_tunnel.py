"""托管隧道的当前配置与私密参数边界。"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlsplit

from .mcp_errors import McpServiceError
from ..llm.routes import mask_api_key

DEFAULT_TUNNEL = {'mode': 'quick', 'public_url': '', 'origin_port': 12345, 'token': ''}


def _unique(pairs):
    """拒绝重复字段，不用后一个值覆盖身份或凭据。"""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate field')
        result[key] = value
    return result


def validate_tunnel(value, previous=None):
    """空令牌只复用原令牌，不把凭据放入公开回执。"""
    if (not isinstance(value, dict) or set(value) - set(DEFAULT_TUNNEL)
            or not {'mode', 'public_url', 'origin_port'} <= set(value)):
        raise McpServiceError('ui.mcpService.error.tunnelConfig')
    result = {**DEFAULT_TUNNEL, **value}
    if result['mode'] not in ('quick', 'named'):
        raise McpServiceError('ui.mcpService.error.tunnelConfig')
    port = result['origin_port']
    if type(port) is not int or not 1024 <= port <= 65535:
        raise McpServiceError('ui.mcpService.error.tunnelConfig')
    token, url = result['token'], result['public_url']
    if not isinstance(token, str) or len(token) > 8192 or any(char.isspace() for char in token):
        raise McpServiceError('ui.mcpService.error.tunnelConfig')
    if not isinstance(url, str) or len(url) > 2048:
        raise McpServiceError('ui.mcpService.error.tunnelConfig')
    try:
        token.encode('utf-8'); url.encode('utf-8')
    except UnicodeEncodeError as exc:
        raise McpServiceError('ui.mcpService.error.tunnelConfig') from exc
    if not token and previous:
        result['token'] = previous['token']
    if result['mode'] == 'named':
        try:
            parsed = urlsplit(url)
            valid = (parsed.scheme == 'https' and parsed.hostname and '.' in parsed.hostname
                     and not parsed.username and not parsed.password and not parsed.query
                     and not parsed.fragment and parsed.path in ('', '/') and parsed.port in (None, 443)
                     and not any(char.isspace() for char in url))
        except ValueError:
            valid = False
        if not valid or not result['token']:
            raise McpServiceError('ui.mcpService.error.tunnelConfig')
        result['public_url'] = url.rstrip('/')
    return result


def read_tunnel(path: Path):
    """损坏的当前配置保留原件，读取不执行迁移或写回。"""
    try:
        if not path.exists():
            return dict(DEFAULT_TUNNEL), None
        if path.stat().st_size > 16384:
            raise ValueError('configuration size')
        with path.open('rb') as stream:
            raw = stream.read(16385)
        if len(raw) > 16384:
            raise ValueError('configuration size')
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique)
        if not isinstance(value, dict) or set(value) != set(DEFAULT_TUNNEL):
            raise ValueError('configuration fields')
        return validate_tunnel(value), raw
    except (OSError, ValueError, RecursionError) as exc:
        raise McpServiceError('ui.mcpService.error.tunnelConfigFile') from exc


def public_tunnel(value):
    """只暴露配置摘要与统一遮罩，不返回完整令牌或私密文件路径。"""
    return {key: value[key] for key in ('mode', 'public_url', 'origin_port')} | {
        'has_token': bool(value['token']), 'token_mask': mask_api_key(value['token'])}
