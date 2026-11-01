"""解析服务端冷却，超过自动等待上限时停止而不是提前重试。"""
from collections.abc import Mapping
from datetime import timezone
from email.utils import parsedate_to_datetime
import math
import time

from harness.i18n import tr
from ..core.errors import LlmError

MAX_RETRY_DELAY = 300.0


def parse_retry_after(value, *, now=None):
    """支持 Retry-After 的秒数和 HTTP 日期，无效值不参与恢复决策。"""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            seconds = date.timestamp() - (time.time() if now is None else now)
        except (ValueError, TypeError, OverflowError, OSError):
            return None
    return max(0.0, seconds) if math.isfinite(seconds) else None


def retry_delay(details, fallback):
    """本地延迟可延长服务端等待，不能缩短它。"""
    value = details.get('retry_after') if isinstance(details, Mapping) else None
    try:
        if type(value) in (int, float) and math.isfinite(value) and value >= 0:
            delay = max(fallback, value)
            if delay > MAX_RETRY_DELAY:
                raise LlmError(tr('python.retry.wait_limit', seconds=MAX_RETRY_DELAY),
                    code='llm/retry-delay-limit', transient=False, retry_after=value, route_retry=False)
            return delay
    except OverflowError:
        pass
    return fallback
