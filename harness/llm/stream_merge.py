"""统一完成式与智能体循环的思考块边界和签名续接。"""
from __future__ import annotations

from .types import ContentBlock, StreamEvent, ThinkingBlock


def merge_thinking_event(content: list[ContentBlock], event: StreamEvent) -> None:
    """只合并未签名思考；签名分片追加到最后的思考块。"""
    if event.kind == 'thinking':
        if content and isinstance(content[-1], ThinkingBlock) and content[-1].signature is None:
            content[-1] = ThinkingBlock(content[-1].thinking + event.thinking,
                                        event.signature or content[-1].signature)
        else:
            content.append(ThinkingBlock(event.thinking, event.signature))
    elif event.kind == 'signature' and event.signature is not None:
        if content and isinstance(content[-1], ThinkingBlock):
            block = content[-1]
            content[-1] = ThinkingBlock(block.thinking, (block.signature or '') + event.signature)
        else:
            content.append(ThinkingBlock('', event.signature))
