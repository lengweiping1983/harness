"""摘要调用的端点专用候选上下文，不包含工具执行入口。"""
from dataclasses import dataclass, replace
import inspect
from typing import Any

from .opencode import is_zen
from .types import LlmCallConfig, ToolSchema


def can_use_zen_compaction(adapter):
    """准备阶段保守识别候选路径，与真正构造请求使用同一判定。"""
    subscription = getattr(adapter, 'chatgpt', None)
    if subscription is not None and subscription.logged_in:
        return False
    pool = getattr(adapter, 'route_pool', None)
    possible = (any(is_zen(model['base_url']) and model.get('enabled', True) for model in pool.models)
                if pool is not None else is_zen(getattr(adapter, 'base_url', '')))
    if not possible:
        return False
    try:
        return 'zen_compaction' in inspect.signature(adapter.complete).parameters
    except (AttributeError, ValueError, TypeError):
        return False


@dataclass(frozen=True)
class ZenCompaction:
    """由 Agent 提供真实普通指令和工具，在最终端点确定后才启用。"""

    messages: tuple[Any, ...]
    tools: tuple[ToolSchema, ...]
    max_tokens: int = 524288

    def select(self, base_url: str, messages, config: LlmCallConfig, tools):
        """仅 Zen 改用普通请求结构，其他端点完整保留原始摘要请求。"""
        if not is_zen(base_url):
            return messages, config, tools
        extra = {key: value for key, value in config.extra_body.items()
                 if key not in {'max_tokens', 'max_completion_tokens'}}
        return self.messages, replace(config, max_tokens=self.max_tokens, extra_body=extra), self.tools
