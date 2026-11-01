"""LLM 层的数据类型：消息、内容块、工具 schema、调用配置、用量统计。

它们是**纯数据**，不含任何网络逻辑，方便单独测试与替换后端。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from ..core.jsonutil import JsonValue, to_jsonable

# ---------------------------------------------------------------------------
# 内容块（ContentBlock）
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TextBlock:
    """纯文本内容块。"""

    text: str
    type: Literal["text"] = "text"

    def to_dict(self) -> dict[str, Any]:
        return {"type": "text", "text": self.text}


@dataclass(frozen=True, slots=True)
class ThinkingBlock:
    """模型思考内容块（reasoning）。

    ``signature`` 保存部分供应商返回的加密签名，回传时可以保持思维链连续。
    """

    thinking: str
    signature: str | None = None
    type: Literal["thinking"] = "thinking"

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"type": "thinking", "thinking": self.thinking}
        if self.signature is not None:
            data["signature"] = self.signature
        return data


@dataclass(frozen=True, slots=True)
class ImageBlock:
    """图片内容块。``url`` 可以是 ``data:`` URI 或 http(s) 地址。"""

    url: str
    mime_type: str = "image/png"
    type: Literal["image"] = "image"

    def to_dict(self) -> dict[str, Any]:
        return {"type": "image", "url": self.url, "mime_type": self.mime_type}


@dataclass(frozen=True, slots=True)
class ToolUseBlock:
    """模型请求调用工具的内容块。"""

    id: str
    name: str
    input: JsonValue
    type: Literal["tool_use"] = "tool_use"

    def to_dict(self) -> dict[str, Any]:
        # `input` 可能来自会话日志里被 deep_freeze 冻结的嵌套容器
        # （MappingProxyType / tuple），不能直接交给 json.dumps。
        # 这里统一过 to_jsonable 解冻成纯 JSON 值。
        return {
            "type": "tool_use",
            "id": self.id,
            "name": self.name,
            "input": to_jsonable(self.input),
        }


@dataclass(frozen=True, slots=True)
class ToolResultBlock:
    """工具执行结果内容块（回传给模型）。"""

    tool_use_id: str
    content: str | list[dict[str, Any]]
    is_error: bool = False
    type: Literal["tool_result"] = "tool_result"

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "tool_result",
            "tool_use_id": self.tool_use_id,
            "content": self.content,
            "is_error": self.is_error,
        }


ContentBlock = TextBlock | ThinkingBlock | ImageBlock | ToolUseBlock | ToolResultBlock

# ---------------------------------------------------------------------------
# 消息（Message）
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SystemMessage:
    """系统消息（系统提示词的载体）。"""

    text: str
    source: str = "system-prompt"
    context_parts: tuple[tuple[str, str], ...] = field(default=(), kw_only=True)
    type: Literal["system"] = "system"

    def to_dict(self) -> dict[str, Any]:
        return {"role": "system", "content": self.text, "source": self.source}


@dataclass(frozen=True, slots=True)
class DeveloperMessage:
    """开发者消息：部署期注入的持久指令，优先级介于 system 与 user 之间。"""

    text: str
    source: str = "developer"
    context_parts: tuple[tuple[str, str], ...] = field(default=(), kw_only=True)
    type: Literal["developer"] = "developer"

    def to_dict(self) -> dict[str, Any]:
        return {"role": "developer", "content": self.text, "source": self.source}


@dataclass(frozen=True, slots=True)
class UserMessage:
    """用户消息。"""

    text: str
    source: str = "user"
    context_spans: tuple[tuple[str, int, int], ...] = field(default=(), kw_only=True)
    type: Literal["user"] = "user"

    def to_dict(self) -> dict[str, Any]:
        return {"role": "user", "content": self.text, "source": self.source}


@dataclass(frozen=True, slots=True)
class AssistantMessage:
    """助手消息：可能同时包含思考、文本与工具调用。"""

    content: tuple[ContentBlock, ...] = ()
    model: str = ""
    stop_reason: str | None = None
    usage: "TokenUsage | None" = None
    type: Literal["assistant"] = "assistant"
    # 供应商不透明思考数据必须在工具续接和重放时保留。
    # 按原始顺序保存流式细节，不能重新生成签名。
    reasoning_details: tuple[Mapping[str, Any], ...] = field(default=(), kw_only=True)

    # -- 便捷访问器 ------------------------------------------------------
    @property
    def text(self) -> str:
        """拼接所有文本块（不含思考）。"""
        return "".join(b.text for b in self.content if isinstance(b, TextBlock))

    @property
    def thinking(self) -> str:
        """拼接所有思考块。"""
        return "".join(b.thinking for b in self.content if isinstance(b, ThinkingBlock))

    @property
    def tool_uses(self) -> tuple[ToolUseBlock, ...]:
        """返回本条消息中的全部工具调用。"""
        return tuple(b for b in self.content if isinstance(b, ToolUseBlock))

    @property
    def has_tool_use(self) -> bool:
        return bool(self.tool_uses)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "role": "assistant",
            "content": [b.to_dict() for b in self.content],
        }
        if self.model:
            data["model"] = self.model
        if self.stop_reason is not None:
            data["stop_reason"] = self.stop_reason
        if self.usage is not None:
            data["usage"] = self.usage.to_dict()
        if self.reasoning_details:
            data["reasoning_details"] = to_jsonable(self.reasoning_details)
        return data


@dataclass(frozen=True, slots=True)
class ToolResultMessage:
    """工具结果消息：把一次或多次工具执行结果回传给模型。"""

    results: tuple[ToolResultBlock, ...] = ()
    type: Literal["tool_result"] = "tool_result"

    @property
    def is_error(self) -> bool:
        return any(r.is_error for r in self.results)

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": r.tool_use_id, "content": r.content, "is_error": r.is_error}
                for r in self.results
            ],
            "_kind": "tool_result",
        }


Message = SystemMessage | DeveloperMessage | UserMessage | AssistantMessage | ToolResultMessage

# ---------------------------------------------------------------------------
# 工具 Schema
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SchemaResource:
    """说明模板中的显式资源参数，按目标语言解析而非保存已翻译文本。"""

    key: str
    values: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ToolSchema:
    """模型可见的工具描述。

    注意只有 ``name`` / ``description`` / ``parameters`` 三个字段会发给模型；
    执行相关的元数据在 :mod:`harness.tools` 中，不会泄漏到请求里。
    """

    name: str
    description: str
    parameters: Mapping[str, Any]  # JSON 模式
    #: 自有说明显式资源身份，不进入模型协议。
    description_key: str | None = field(default=None, kw_only=True)
    description_values: Mapping[str, Any] = field(default_factory=dict, kw_only=True)
    description_parts: tuple[SchemaResource, ...] = field(default=(), kw_only=True)

    #: 参数说明的精确路径到资源键，不遍历或猜测原文。
    parameter_keys: Mapping[tuple[str | int, ...], str | tuple[str, Mapping[str, Any]]] = field(default_factory=dict, kw_only=True)

    def resolve_description(self, language: str) -> ToolSchema:
        """按显式键解析说明，原文 schema 保持不变。"""
        if self.description_key is None and not self.description_parts and not self.parameter_keys:
            return self
        from copy import deepcopy
        from ..i18n import resources
        values = resources(language)

        def resource(item):
            text = values[item.key]
            return text.format_map(arguments(item.values)) if item.values else text

        def arguments(items):
            return {key: resource(value) if isinstance(value, SchemaResource) else value
                    for key, value in items.items()}

        parameters = deepcopy(dict(self.parameters))
        for path, key in self.parameter_keys.items():
            if not path or path[-1] not in ('description', 'title'):
                raise ValueError(values['schema.resource.invalid_path'])
            parent = parameters
            for component in path[:-1]:
                parent = parent[component]
            if not isinstance(parent[path[-1]], str):
                raise TypeError(values['schema.resource.invalid_target'])
            resource_key, supplied_values = (key, {}) if isinstance(key, str) else key
            resolve_values = arguments(supplied_values)
            parent[path[-1]] = values[resource_key].format_map(resolve_values) if resolve_values else values[resource_key]
        description = values[self.description_key] if self.description_key else self.description
        if self.description_key and self.description_values:
            description = description.format_map(arguments(self.description_values))
        if self.description_parts:
            description = ''.join(resource(part) for part in self.description_parts)
        return replace(self, description=description,
                       parameters=parameters)


    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": dict(self.parameters),
        }


# ---------------------------------------------------------------------------
# 调用配置与用量
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LlmCallConfig:
    """一次模型调用的配置快照。

    会话日志记录的是"某次请求用了什么配置"（EpochHeader），而不是全局设置，
    这样重放历史时可以复现当时的请求。
    """

    provider: str = "openrouter"
    model: str = ""
    temperature: float | None = None
    max_tokens: int | None = None
    top_p: float | None = None
    reasoning_effort: str | None = None
    extra_headers: Mapping[str, str] = field(default_factory=dict)
    extra_body: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """转成 JSON 友好字典，``None`` 值被省略（保持事件体最小）。"""
        data: dict[str, Any] = {"provider": self.provider, "model": self.model}
        for key in ("temperature", "max_tokens", "top_p", "reasoning_effort"):
            value = getattr(self, key)
            if value is not None:
                data[key] = value
        if self.extra_headers:
            data["extra_headers"] = dict(self.extra_headers)
        if self.extra_body:
            data["extra_body"] = dict(self.extra_body)
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LlmCallConfig":
        return cls(
            provider=data.get("provider", "openrouter"),
            model=data.get("model", ""),
            temperature=data.get("temperature"),
            max_tokens=data.get("max_tokens"),
            top_p=data.get("top_p"),
            reasoning_effort=data.get("reasoning_effort"),
            extra_headers=dict(data.get("extra_headers", {})),
            extra_body=dict(data.get("extra_body", {})),
        )

    def merged(self, **updates: Any) -> "LlmCallConfig":
        """返回一个覆盖了指定字段的新配置（未提供的字段继承，显式 ``None`` 清空）。"""
        return replace(self, **updates)


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """token 用量统计。"""

    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cache_read_tokens: int = 0
    cache_reported_input_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }
        if self.reasoning_tokens:
            data["reasoning_tokens"] = self.reasoning_tokens
        if self.cache_read_tokens:
            data["cache_read_tokens"] = self.cache_read_tokens
        if self.cache_reported_input_tokens:
            data["cache_reported_input_tokens"] = self.cache_reported_input_tokens
        return data

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_reported_input_tokens=self.cache_reported_input_tokens + other.cache_reported_input_tokens,
        )


@dataclass(frozen=True, slots=True)
class ModelInfo:
    """模型元信息：上下文窗口、能力标记。"""

    provider: str
    model: str
    context_window: int | None = None
    max_output_tokens: int | None = None
    supports_tools: bool = True
    supports_thinking: bool = True
    supports_vision: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "context_window": self.context_window,
            "max_output_tokens": self.max_output_tokens,
            "supports_tools": self.supports_tools,
            "supports_thinking": self.supports_thinking,
            "supports_vision": self.supports_vision,
        }


# ---------------------------------------------------------------------------
# 流式事件
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StreamEvent:
    """流式生成过程中的一个增量事件。

    ``kind`` 取值：

    * ``"start"``      —— 一次响应开始
    * ``"text"``       —— 新增文本片段
    * ``"thinking"``   —— 新增思考片段
    * ``"signature"``  —— 思考签名（部分供应商在思考结束时给出）
    * ``"reasoning_details"`` —— 原样保留的结构化思考续传数据
    * ``"tool_use"``   —— 一个完整的工具调用
    * ``"usage"``      —— 用量统计
    * ``"error"``      —— 生成过程中出错
    * ``"done"``       —— 响应结束；完整工具调用与用量应在此前发送。
      消费者收到后关闭流，不再等 EOF 或消费后续事件。
    """

    kind: str
    text: str = ""
    thinking: str = ""
    signature: str | None = None
    tool_use: ToolUseBlock | None = None
    usage: TokenUsage | None = None
    stop_reason: str | None = None
    error: str | None = None
    reasoning_details: tuple[Mapping[str, Any], ...] = field(default=(), kw_only=True)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"kind": self.kind}
        if self.text:
            data["text"] = self.text
        if self.thinking:
            data["thinking"] = self.thinking
        if self.signature:
            data["signature"] = self.signature
        if self.tool_use is not None:
            data["tool_use"] = self.tool_use.to_dict()
        if self.usage is not None:
            data["usage"] = self.usage.to_dict()
        if self.stop_reason is not None:
            data["stop_reason"] = self.stop_reason
        if self.error is not None:
            data["error"] = self.error
        if self.reasoning_details:
            data["reasoning_details"] = to_jsonable(self.reasoning_details)
        return data


__all__ = [
    "TextBlock",
    "ThinkingBlock",
    "ImageBlock",
    "ToolUseBlock",
    "ToolResultBlock",
    "ContentBlock",
    "SystemMessage",
    "DeveloperMessage",
    "UserMessage",
    "AssistantMessage",
    "ToolResultMessage",
    "Message",
    "ToolSchema",
    "LlmCallConfig",
    "TokenUsage",
    "ModelInfo",
    "StreamEvent",
]
