"""OpenCode Zen 的端点专用请求映射，不改变消息和可执行工具。"""
from __future__ import annotations

import hashlib
import time
import uuid


ZEN_URL = 'https://opencode.ai/zen/v1'
# 使用 OpenCode 的倒序毫秒时间前缀；同一进程内会话映射保持稳定。
_SESSION_PREFIX = f'{(~(int(time.time() * 1000) * 0x1000 + 1)) & ((1 << 48) - 1):012x}'


def is_zen(base_url: str) -> bool:
    """仅匹配指定根地址，避免把专用头发送给其他站点。"""
    return base_url.rstrip('/') == ZEN_URL


def request_headers(base_url: str, session_id: str | None) -> dict[str, str]:
    """把 Harness 会话标识映射为稳定的请求关联头。"""
    if not is_zen(base_url):
        return {}
    identity = session_id or uuid.uuid4().hex
    # 时间前缀不能用任意哈希替代，尾部由会话摘要派生以跨凭据复用关联。
    session = 'ses_' + _SESSION_PREFIX + hashlib.sha256(identity.encode()).hexdigest()[:14]
    return {
        'User-Agent': 'opencode/latest/2.0.27/desktop',
        'x-opencode-client': 'desktop',
        'x-opencode-project': 'harness',
        'x-opencode-session': session,
        'x-opencode-session-id': session,
        'x-session-affinity': session,
        'X-Session-Id': session,
    }


def request_body(base_url: str, body: dict) -> dict:
    """转换 Zen 的输出预算和推理字段，保留真实提示词及工具定义。"""
    if not is_zen(base_url):
        return body
    result = dict(body)
    if 'max_tokens' in result:
        result['max_completion_tokens'] = result.pop('max_tokens')
    reasoning = result.get('reasoning')
    if isinstance(reasoning, dict) and set(reasoning) == {'effort'}:
        result['reasoning_effort'] = result.pop('reasoning')['effort']
    return result
