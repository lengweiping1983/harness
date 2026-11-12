"""Agent 实时状态和回合结果；业务调用方无需依赖循环实现。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..llm.types import TokenUsage, ToolUseBlock


@dataclass
class AgentStatus:
    """Agent 的实时状态（供 UI 展示）。"""

    session_id: str
    status: str = "idle"  # 状态：idle / thinking / streaming / tool / done / error / cancelled
    turn: int = 0
    step: int = 0
    current_tool: str | None = None
    last_error: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "status": self.status,
            "turn": self.turn,
            "step": self.step,
            "current_tool": self.current_tool,
            "last_error": self.last_error,
            "usage": {
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "reasoning_tokens": self.reasoning_tokens,
            },
        }


@dataclass
class TurnResult:
    """一次 ``prompt()`` 的结果。"""

    session_id: str
    turn: int
    reason: str
    text: str = ""
    thinking: str = ""
    tool_calls: list[ToolUseBlock] = field(default_factory=list)
    usage: TokenUsage | None = None
    error: str | None = None
    error_info: dict[str, Any] | None = field(default=None, kw_only=True)

    def to_dict(self) -> dict[str, Any]:
        data = {
            "session_id": self.session_id,
            "turn": self.turn,
            "reason": self.reason,
            "text": self.text,
            "thinking": self.thinking,
            "tool_calls": [c.to_dict() for c in self.tool_calls],
            "usage": self.usage.to_dict() if self.usage else None,
            "error": self.error,
        }
        if self.error_info is not None:
            data["error_info"] = self.error_info
        return data
