"""团队运行期限的共享校验，不改变成员身份或私有职责。"""
import math
from typing import Any


def member_wait_timeout(metadata: dict[str, Any], *, default: float = 600.0) -> float:
    """未配置时沿用默认值，显式期限允许一秒到二十四小时。"""
    if 'member_timeout_seconds' not in metadata:
        return default
    value = metadata['member_timeout_seconds']
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 1 <= value <= 86400 or not math.isfinite(value):
        raise ValueError('member_timeout_seconds')
    return float(value)


def reply_limit(metadata: dict[str, Any], key: str) -> int | None:
    """回复按Unicode字符计数；不把字符上限混同模型token预算。"""
    value = metadata.get(key)
    if value is None:
        return None
    if type(value) is not int or not 1 <= value <= 100000:
        raise ValueError(key)
    return value


def reply_feedback(text: str, limit: int | None) -> str | None:
    """只要求重写，不截断原文，也不自动重放任何操作。"""
    if limit is None or len(text) <= limit:
        return None
    from ..i18n import tr
    return tr('agent.team.reply_limit', actual=len(text), limit=limit)


def member_retry_limit(metadata: dict[str, Any]) -> int:
    """同一轮成员连续失败后的恢复上限，成功交付后重新计数。"""
    value = metadata.get('member_retry_limit', 2)
    if type(value) is not int or not 0 <= value <= 10:
        raise ValueError('member_retry_limit')
    return value
