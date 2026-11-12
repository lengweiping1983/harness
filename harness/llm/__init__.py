"""LLM 层：消息类型、wire 转换、传输与模型适配器。"""

from .adapter import DEFAULT_BASE_URL, DEFAULT_MODEL, OpenRouterAdapter
from .key_pool import ApiKeyPool, parse_key_list
from .transport import SseParser, Transport, UrllibTransport, parse_sse_stream
from .translate import build_request, message_to_wire, messages_to_wire, tool_schema_to_wire
from .types import (
    AssistantMessage,
    ContentBlock,
    DeveloperMessage,
    ImageBlock,
    LlmCallConfig,
    Message,
    ModelInfo,
    StreamEvent,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    TokenUsage,
    ToolResultBlock,
    ToolResultMessage,
    ToolSchema,
    ToolUseBlock,
    UserMessage,
)

__all__ = [
    "OpenRouterAdapter",
    "ApiKeyPool",
    "parse_key_list",
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL",
    "Transport",
    "UrllibTransport",
    "SseParser",
    "parse_sse_stream",
    "build_request",
    "message_to_wire",
    "messages_to_wire",
    "tool_schema_to_wire",
    "SystemMessage",
    "DeveloperMessage",
    "UserMessage",
    "AssistantMessage",
    "ToolResultMessage",
    "Message",
    "TextBlock",
    "ThinkingBlock",
    "ImageBlock",
    "ToolUseBlock",
    "ToolResultBlock",
    "ContentBlock",
    "ToolSchema",
    "LlmCallConfig",
    "TokenUsage",
    "ModelInfo",
    "StreamEvent",
]
