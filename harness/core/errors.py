"""统一异常体系。

所有异常都继承 :class:`HarnessError`，方便上层统一捕获；
同时每个异常都带一个稳定的 ``code``，用于日志记录、UI 展示与测试断言。

设计要点：
1. 单一基类，避免上层写一长串 ``except (A, B, C)``；
2. ``code`` 是字符串枚举而非异常类，方便序列化到 JSON 传给前端；
3. ``to_dict()`` 让异常可以安全地跨进程（HTTP/WS）传输。
"""

from __future__ import annotations

from typing import Any


class HarnessError(Exception):
    """所有 harness 异常的基类。

    :param message: 面向人类的中文错误描述。
    :param code:    机器可读的错误码，如 ``"session/not-found"``。
    :param details: 附加的结构化上下文，例如出错的 session id、工具名等。
    """

    #: 子类可覆盖，给出该异常的默认错误码。
    code: str = "harness/error"

    def __init__(self, message: str, *, code: str | None = None, **details: Any) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        """转成可 JSON 序列化的字典，用于通过 HTTP/WS 发送给 UI。"""
        return {"code": self.code, "message": self.message, "details": self.details}

    def __str__(self) -> str:  # pragma: no cover - 仅用于日志可读性
        if not self.details:
            return f"[{self.code}] {self.message}"
        return f"[{self.code}] {self.message} {self.details}"


# --------------------------------------------------------------------------
# LLM（大模型调用）相关异常
# --------------------------------------------------------------------------
class LlmError(HarnessError):
    """调用大模型过程中出现的错误。

    ``transient`` 标记该错误是否值得重试（网络抖动、429、5xx 属于瞬时错误）。
    """

    code = "llm/error"

    def __init__(self, message: str, *, transient: bool = False, **details: Any) -> None:
        super().__init__(message, **details)
        self.transient = transient

    def to_dict(self) -> dict[str, Any]:
        """保留宿主确认的重试分类，供持久运行层决策。"""
        return {**super().to_dict(), "transient": self.transient}


class LlmRequestError(LlmError):
    """请求构造或发送失败（参数非法、连接失败等）。"""

    code = "llm/request"


class LlmResponseError(LlmError):
    """模型返回了无法解析或不合法的响应。"""

    code = "llm/response"


class LlmTimeoutError(LlmError):
    """请求超时。属于瞬时错误，可重试。"""

    code = "llm/timeout"

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message, transient=True, **details)


class LlmContextLengthError(LlmError):
    """端点拒绝：输入 + 请求的 max_tokens 超出上下文窗口。

    端点报错文本里带着它的**真实**窗口大小（``limit``），调用方应学习该值、
    收敛 max_tokens 后原地重试，而不是把回合打死。
    """

    code = "llm/context-length"

    def __init__(self, message: str, *, limit: int | None = None, **details: Any) -> None:
        super().__init__(message, transient=False, limit=limit, **details)


# --------------------------------------------------------------------------
# 会话相关异常
# --------------------------------------------------------------------------
class SessionError(HarnessError):
    """会话操作的基础异常。"""

    code = "session/error"


class SessionNotFoundError(SessionError):
    """按 id 查找会话失败。"""

    code = "session/not-found"


class SessionInvariantError(SessionError):
    """违反了会话日志的不变量（如事件类型未注册、序号不连续）。"""

    code = "session/invariant"


class PersistenceError(SessionError):
    """持久化提交失败；会话停止写入直到重新载入并检查数据。"""

    code = "session/persistence"


# --------------------------------------------------------------------------
# 工具相关异常
# --------------------------------------------------------------------------
class ToolError(HarnessError):
    """工具执行相关异常基类。"""

    code = "tool/error"


class ToolNotFoundError(ToolError):
    """模型调用了未注册的工具名。"""

    code = "tool/not-found"


class ToolArgsError(ToolError):
    """模型传入的工具参数不满足 JSON Schema。"""

    code = "tool/args"


class ToolExecutionError(ToolError):
    """工具执行体本身抛出异常。"""

    code = "tool/execution"


class ToolDeniedError(ToolError):
    """工具被权限策略拒绝。"""

    code = "tool/denied"


# --------------------------------------------------------------------------
# Skill 相关异常
# --------------------------------------------------------------------------
class SkillError(HarnessError):
    """技能加载相关异常基类。"""

    code = "skill/error"


class SkillNotFoundError(SkillError):
    """按名字查找技能失败。"""

    code = "skill/not-found"


class SkillParseError(SkillError):
    """SKILL.md 的 frontmatter 或正文解析失败。"""

    code = "skill/parse"


# --------------------------------------------------------------------------
# Agent（Agents）
# --------------------------------------------------------------------------
class AgentError(HarnessError):
    """Agent加载相关异常基类。"""

    code = "agent/error"


class AgentNotFoundError(AgentError):
    """按名字查找Agent失败。"""

    code = "agent/not-found"


class AgentParseError(AgentError):
    """AGENTS.md 的 frontmatter 或正文解析失败。"""

    code = "agent/parse"


# --------------------------------------------------------------------------
# 连接器（Connectors）
# --------------------------------------------------------------------------
class ConnectorError(HarnessError):
    """连接器管理相关异常基类。"""

    code = "connector/error"


# --------------------------------------------------------------------------
# 配置 / 参数
# --------------------------------------------------------------------------
class ConfigError(HarnessError):
    """配置非法。"""

    code = "config/error"


class ValidationError(HarnessError):
    """通用参数校验失败。"""

    code = "validation/error"


__all__ = [
    "HarnessError",
    "LlmError",
    "LlmRequestError",
    "LlmResponseError",
    "LlmTimeoutError",
    "SessionError",
    "SessionNotFoundError",
    "SessionInvariantError",
    "ToolError",
    "ToolNotFoundError",
    "ToolArgsError",
    "ToolExecutionError",
    "ToolDeniedError",
    "SkillError",
    "SkillNotFoundError",
    "SkillParseError",
    "AgentError",
    "AgentNotFoundError",
    "AgentParseError",
    "ConnectorError",
    "ConfigError",
    "ValidationError",
]
