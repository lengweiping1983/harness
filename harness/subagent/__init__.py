"""子代理系统：把独立 AgentLoop 组织成可观测、可中断的并发单元。"""

from __future__ import annotations

from .manager import (
    COMPLETED,
    FAILED,
    INTERRUPTED,
    PENDING,
    RUNNING,
    TERMINAL_STATES,
    SubagentManager,
)

__all__ = [
    "SubagentManager",
    "PENDING",
    "RUNNING",
    "COMPLETED",
    "FAILED",
    "INTERRUPTED",
    "TERMINAL_STATES",
]
