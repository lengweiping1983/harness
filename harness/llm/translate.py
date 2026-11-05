"""消息类型 <-> OpenAI/Anthropic 风格 wire 格式的转换。

本项目统一使用 **Anthropic 风格的内容块** 作为内部表示，
只在真正发请求时转换成 OpenAI Chat Completions 的格式。这样换供应商时
只需要改 wire 层，内部消息模型不用动。

wire 格式（OpenRouter / OpenAI 兼容）：

* ``system`` 内容放进顶层 ``messages`` 的第一条 ``{"role": "system", ...}``；
* 用户/助手消息的 ``content`` 是字符串或多模态数组；
* 思考内容使用 ``reasoning_content`` 或原样保留的 ``reasoning_details``；
* 工具调用使用 ``tool_calls``，结果使用 ``role="tool"`` 消息。
"""

from __future__ import annotations

from harness.i18n import tr

from typing import Any

from ..core.jsonutil import to_jsonable
from .vision import omit_image_data, project_image_result, image_projection_plan, reused_image_result
from .types import (
    AssistantMessage,
    ContentBlock,
    DeveloperMessage,
    ImageBlock,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultMessage,
    ToolUseBlock,
    ToolSchema,
    UserMessage,
)


def tool_schema_to_wire(schema: ToolSchema) -> dict[str, Any]:
    """把内部 :class:`ToolSchema` 转成 OpenAI ``tools`` 数组里的一项。"""
    return {
        "type": "function",
        "function": {
            "name": schema.name,
            "description": schema.description,
            "parameters": dict(schema.parameters),
        },
    }


def _image_to_wire(block: ImageBlock) -> dict[str, Any]:
    """图片块 -> ``image_url`` 段。

    ``data:`` URI 直接透传；http(s) 地址也直接透传。
    """
    return {"type": "image_url", "image_url": {"url": block.url}}


def _content_blocks_to_wire(blocks: list[ContentBlock]) -> tuple[str | list[dict[str, Any]], list[dict[str, Any]]]:
    """把助手内容块拆成消息内容和 ``tool_calls``。

    助手消息里的 :class:`ToolUseBlock` 变成 ``tool_calls``；其他块拼成 content。
    """
    parts: list[dict[str, Any]] = []
    tool_calls: list[dict[str, Any]] = []
    for block in blocks:
        if isinstance(block, TextBlock):
            parts.append({"type": "text", "text": block.text})
        elif isinstance(block, ImageBlock):
            parts.append(_image_to_wire(block))
        elif isinstance(block, ToolUseBlock):
            tool_calls.append(
                {
                    "id": block.id,
                    "type": "function",
                    "function": {
                        "name": block.name,
                        "arguments": _dump_arguments(block.input),
                    },
                }
            )
    return parts or "", tool_calls


def _dump_arguments(value: Any) -> str:
    """把工具参数序列化为 JSON 字符串。"""
    import json

    return json.dumps(to_jsonable(value), ensure_ascii=False)


def message_to_wire(message: Any, *, native_responses: bool = False) -> list[dict[str, Any]]:
    """把一条内部消息转成 wire 格式的 0..N 条消息。

    工具结果消息可能展开成多条 ``role="tool"`` 消息，因此返回列表。
    """
    if isinstance(message, SystemMessage):
        return [{"role": "system", "content": message.text}]

    if isinstance(message, DeveloperMessage):
        # OpenAI 兼容接口没有独立的 developer 角色，这里降级为 system。
        return [{"role": "system", "content": message.text}]

    if isinstance(message, UserMessage):
        return [{"role": "user", "content": message.text}]

    if isinstance(message, AssistantMessage):
        content, tool_calls = _content_blocks_to_wire(list(message.content))
        wire: dict[str, Any] = {"role": "assistant", "content": content}
        if tool_calls:
            wire["tool_calls"] = tool_calls
        if message.reasoning_details:
            # Responses 原始状态只能交回对应协议；不能发给其他聊天端点。
            details = [detail for detail in message.reasoning_details
                       if native_responses or detail.get('type') != 'response.reasoning']
            if details:
                wire["reasoning_details"] = to_jsonable(details)
        else:
            thoughts = [block for block in message.content if isinstance(block, ThinkingBlock)]
            if any(block.signature is not None for block in thoughts):
                wire["reasoning_details"] = [
                    {"type": "reasoning.text", "text": block.thinking,
                     "signature": block.signature, "format": "anthropic-claude-v1", "index": index}
                    for index, block in enumerate(thoughts)
                ]
            elif message.thinking:
                wire["reasoning_content"] = message.thinking
        return [wire]

    if isinstance(message, ToolResultMessage):
        # 每个工具结果一条 role="tool" 消息，靠 tool_call_id 关联。
        out: list[dict[str, Any]] = []
        for result in message.results:
            content = result.content
            if not isinstance(content, str):
                import json

                content = json.dumps(content, ensure_ascii=False)
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": result.tool_use_id,
                    "content": content,
                }
            )
        return out

    raise TypeError(tr('python.translate.f81777363045', p0=f'{type(message).__name__}'))


def messages_to_wire(messages: list[Any], *, native_responses: bool = False) -> list[dict[str, Any]]:
    """把消息列表展开成 wire 消息列表。"""
    wire: list[dict[str, Any]] = []
    pending_calls: set[str] = set()
    pending_images: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    image_results, retained_results, repeated_images = image_projection_plan(messages)
    for message_index, message in enumerate(messages):
        awaiting_results = bool(pending_calls)
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, ToolUseBlock):
                    pending_calls.add(block.id)
        converted = message_to_wire(message, native_responses=native_responses)
        if isinstance(message, ToolResultMessage):
            for result_index, (result, item) in enumerate(zip(message.results, converted)):
                result_key = (message_index, result_index)
                pending_calls.discard(result.tool_use_id)
                if result_key not in image_results:
                    continue
                # Chat Completions 的 tool 消息保持文本；真正的图片在全部
                # tool_call_id 已回答后追加为 user 多模态数据，不能插到结果中间。
                if result_key not in retained_results:
                    item['content'] = (reused_image_result(result.content, repeated_images[result_key])
                                       if result_key in repeated_images else omit_image_data(result.content))
                    continue
                item['content'], urls = project_image_result(result.content)
                pending_images.extend({'type': 'image_url', 'image_url': {'url': url}} for url in urls)
        # 成员回复或持久引导可能在等待工具时落盘；请求中先闭合整批结果。
        if awaiting_results and not isinstance(message, ToolResultMessage):
            deferred.extend(converted)
        else:
            wire.extend(converted)
        if not pending_calls and deferred:
            wire.extend(deferred)
            deferred = []
        if pending_images and not pending_calls:
            wire.append({'role': 'user', 'content': [
                {'type': 'text', 'text': tr('python.translate.08eb190c31e7')},
                *pending_images,
            ]})
            pending_images = []
    wire.extend(deferred)
    return wire


def build_request(
    messages: list[Any],
    *,
    config,
    tools: list[ToolSchema] | None = None,
    stream: bool = True,
    native_responses: bool = False,
) -> dict[str, Any]:
    """组装一次完整的请求体。

    :param messages: 内部消息列表。
    :param config:   :class:`~harness.llm.types.LlmCallConfig`。
    :param tools:    本次请求可见的工具 schema。
    :param stream:   是否请求流式响应。
    """
    body: dict[str, Any] = {
        "model": config.model,
        "messages": messages_to_wire(messages, native_responses=native_responses),
        "stream": stream,
    }
    if config.temperature is not None:
        body["temperature"] = config.temperature
    if config.max_tokens is not None:
        body["max_tokens"] = config.max_tokens
    if config.top_p is not None:
        body["top_p"] = config.top_p
    if config.reasoning_effort is not None:
        # OpenRouter 通过 `reasoning` 对象传递思考预算/强度。
        body["reasoning"] = {"effort": config.reasoning_effort}
    if tools:
        body["tools"] = [tool_schema_to_wire(t) for t in tools]
        body["tool_choice"] = "auto"
    from .capabilities import minimum
    extra = dict(config.extra_body)
    limits = [body.get('max_tokens'), extra.pop('max_tokens', None), extra.pop('max_completion_tokens', None),
              extra.pop('max_output_tokens', None)]
    requested = minimum(*limits)
    body.pop('max_tokens', None)
    if requested is not None:
        body['max_tokens'] = requested
    for key, value in extra.items():
        if key not in {'model', 'messages', 'stream', 'tools', 'tool_choice'}:
            body[key] = value
    return body


__all__ = [
    "tool_schema_to_wire",
    "message_to_wire",
    "messages_to_wire",
    "build_request",
]
