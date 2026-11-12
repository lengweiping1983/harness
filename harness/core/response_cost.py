"""模型请求用量与任务预算结算，不依赖 Agent 循环或会话存储。"""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from math import ceil
from typing import Any

from ..llm.types import TokenUsage
from .jsonutil import to_jsonable


@dataclass
class ResponseCost:
    """按实际请求身份累计用量，重试、估算与供应商计量共用同一结算。"""
    input_tokens: int
    cjk: int = 0
    other: int = 0
    observed: bool = False
    usage: TokenUsage | None = None
    request_bytes: int = 0
    physical_costs: dict[str, int] = field(default_factory=dict)
    physical_usage: dict[str, TokenUsage] = field(default_factory=dict)
    _physical_start: tuple[int, int] = (0, 0)
    _physical_observed: bool = False
    _physical_input: int | None = None
    route_models: frozenset[str] | None = None

    def begin_physical(self, estimated_input_tokens=None):
        """每次实际传输单独建立输出边界，不清除逻辑请求已有证据。"""
        self._physical_start = (self.cjk, self.other)
        self._physical_observed = False
        self._physical_input = estimated_input_tokens if type(estimated_input_tokens) is int and estimated_input_tokens >= 0 else self.input_tokens

    def finish_physical(self, data):
        """按唯一归档身份结算，失败时已报告的用量也不能被后续请求覆盖。"""
        identity = data.get('trace_id')
        if not isinstance(identity, str) or identity in self.physical_costs:
            return
        raw = data.get('usage')
        usage = None
        if isinstance(raw, Mapping) and 'input_tokens' in raw and 'output_tokens' in raw and all(type(raw.get(key, 0)) is int and raw.get(key, 0) >= 0
                for key in ('input_tokens', 'output_tokens', 'reasoning_tokens', 'cache_read_tokens', 'cache_reported_input_tokens')):
            usage = TokenUsage(**{key: raw.get(key, 0) for key in (
                'input_tokens', 'output_tokens', 'reasoning_tokens', 'cache_read_tokens', 'cache_reported_input_tokens')})
        if usage is not None and usage.total_tokens > 0:
            self.physical_usage[identity] = usage
            tokens = usage.total_tokens
        else:
            before_cjk, before_other = self._physical_start
            input_tokens = self._physical_input if self._physical_input is not None else self.input_tokens
            tokens = input_tokens + ceil((self.cjk - before_cjk) / 1.5 + (self.other - before_other) / 4) if self._physical_observed else 0
        self.physical_costs[identity] = tokens

    def settlement(self, fallback=None):
        """优先使用物理计量；适配器仅提供完整响应时使用响应计量。"""
        return self.total if self.physical_costs or fallback is None else fallback

    def settlement_source(self, fallback_usage=None):
        """合计包含本地估算时，不能将整个金额标记为供应商报告。"""
        if self.physical_costs:
            estimated = any(tokens > 0 and identity not in self.physical_usage
                            for identity, tokens in self.physical_costs.items())
            return 'reported' if self.physical_usage and not estimated else 'estimated'
        usage = fallback_usage or self.usage
        return 'reported' if usage is not None and usage.total_tokens > 0 else 'estimated'

    def observe(self, event: Any) -> None:
        fragment = event.text if event.kind == 'text' else event.thinking if event.kind == 'thinking' else (
            json.dumps(to_jsonable(event.tool_use.input), ensure_ascii=False) if event.kind == 'tool_use' and event.tool_use else '')
        if event.kind == 'signature':
            fragment = event.signature or ''
        elif event.kind == 'reasoning_details':
            fragment = json.dumps(to_jsonable(tuple(
                {key: value for key, value in detail.items() if key not in {'text', 'summary'}}
                for detail in event.reasoning_details)), ensure_ascii=False)
        cjk = sum(1 for ch in fragment if '一' <= ch <= '鿿')
        self.cjk += cjk
        self.other += len(fragment) - cjk
        self.observed = self.observed or event.kind in {'start', 'text', 'thinking', 'signature', 'reasoning_details', 'tool_use', 'usage'}
        self._physical_observed = self._physical_observed or event.kind in {'start', 'text', 'thinking', 'signature', 'reasoning_details', 'tool_use', 'usage'}
        if event.kind == 'usage':
            self.usage = event.usage

    @property
    def output_tokens(self) -> float:
        return self.cjk / 1.5 + self.other / 4

    @property
    def total(self) -> int:
        if self.physical_costs:
            return sum(self.physical_costs.values())
        if self.usage is not None and self.usage.total_tokens > 0:
            return self.usage.total_tokens
        return self.input_tokens + ceil(self.output_tokens) if self.observed else 0
