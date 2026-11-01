"""摘要内部计算的请求边界，不改变普通生成的恢复语义。"""
from contextvars import ContextVar
from typing import Any


active_compaction: ContextVar[Any] = ContextVar('active_compaction', default=None)
