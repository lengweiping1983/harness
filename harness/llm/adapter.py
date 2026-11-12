"""模型适配器：把内部消息 + 工具 + 配置变成一次流式生成。

这是 LLM 能力的唯一对外入口。
它做四件事：

1. **组装请求**：把系统提示、消息历史、工具 schema 组装成 wire 请求；
2. **流式生成**：消费 SSE，边收边产出 :class:`StreamEvent`；
3. **重试**：对瞬时错误（429/5xx/超时）按指数退避重试；
4. **工具调用续传**：若模型返回 ``tool_calls``，由调用方（agent 循环）
   执行工具并把结果追加进消息列表，再次调用本方法。

对测试友好：只要传入一个假的 :class:`~harness.llm.transport.Transport`，
就能完全离线地跑通整个 agent 循环。
"""

from __future__ import annotations

from harness.i18n import tr
from .stream_merge import merge_thinking_event

import asyncio
from contextlib import aclosing
from dataclasses import replace
import json
import inspect
import http.client
import urllib.error
import math
import random
import re
import time
import uuid
from typing import Any, AsyncIterator, Awaitable, Callable, Sequence

from ..core.cleanup import raise_cleanup_errors
from ..core.errors import LlmContextLengthError, LlmError, LlmResponseError, LlmTimeoutError
from .key_pool import ApiKeyPool
from .compaction import ZenCompaction
from .compaction_scope import active_compaction
from .opencode import is_zen, request_body as opencode_body, request_headers as opencode_headers
from .retry import retry_delay
from .failure_diagnostics import failure_diagnostics
from .request_limits import normalize_request
from .capabilities import CapabilityRegistry, positive
from .transport import Transport, UrllibTransport, network_failure, parse_sse_stream
from .translate import build_request
from .types import (
    AssistantMessage,
    ContentBlock,
    LlmCallConfig,
    StreamEvent,
    TextBlock,
    ThinkingBlock,
    TokenUsage,
    ToolSchema,
    ToolUseBlock,
)

# 默认的 OpenRouter 端点与模型
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"

#: SSE 错误分片里未携带状态码、但语义上明确属于上游瞬时故障的关键字
#: （如 "Upstream idle timeout exceeded"：流已建立、HTTP 200 已返回，
#: 端点只给一段错误文本）。这类故障续传/重试是安全的；
#: 内容审核、参数错误等不含这些词，不会被误标。
_TRANSIENT_ERROR_HINTS = (
    "idle timeout",
    "upstream",
    "overloaded",
    "temporarily unavailable",
    "connection reset",
)
#: 端点在 400 报错文本里自报真实上下文窗口：
#: 错误文本示例："This endpoint's maximum context length is 262144 tokens. ..."
_CONTEXT_LENGTH_PATTERN = re.compile(r"maximum context length is\s+([\d,]+)\s+tokens", re.IGNORECASE)
_OUTPUT_LENGTH_PATTERN = re.compile(
    r'(?:max_tokens|max_completion_tokens|maximum output tokens)\s*(?:must be (?:less than or equal to|at most)|cannot exceed|is|<=)\s*([\d,]+)', re.IGNORECASE)
_CORRUPTED_WORD = re.compile(r'[A-Za-z\u3400-\u9fff]\ufffd|\ufffd[A-Za-z\u3400-\u9fff]')
DEFAULT_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"


def _validate_generated_text(text: str, previous: str = '', *, field='content') -> None:
    """检测已经损坏的文字，包括被拆到多个 SSE 增量中的单词。

    引用中的替换符号是有效文本，只有紧邻单词的替换符才视为损坏。
    不猜测已经丢失的字符。
    """
    candidate = previous[-1:] + text
    match = _CORRUPTED_WORD.search(candidate)
    if match:
        exc = LlmResponseError(tr('python.adapter.b2eabd602577'), code='llm/text-corruption', transient=True)
        # 局部原文只进入请求诊断，不放入会被日志和界面序列化的 details。
        exc.text_diagnostic = {'field': field, 'position': candidate.index('\ufffd', match.start()),
                               'position_scope': 'delta_with_previous_character', 'codepoint': 'U+FFFD',
                               'context': candidate[max(0, match.start() - 39):match.start() + 41],
                               'input_probe': match.group()}
        raise exc


def _parse_context_length_limit(exc: LlmError) -> int | None:
    """从端点的上下文超长报错里解析出真实窗口大小（token）。"""
    match = _CONTEXT_LENGTH_PATTERN.search(str(exc))
    if match is None:
        return None
    try:
        return int(match.group(1).replace(",", ""))
    except ValueError:
        return None


class OpenRouterAdapter:
    """OpenRouter（OpenAI 兼容）模型适配器。

    :param api_key: OpenRouter API key。
    :param base_url: API 根地址，默认 ``https://openrouter.ai/api/v1``。
    :param model:    默认模型，默认 ``nvidia/nemotron-3-ultra-550b-a55b:free``。
    :param transport: 传输实现，测试时注入假实现。
    :param max_retries: 瞬时错误的最大重试次数。
    :param retry_base_delay: 指数退避的初始延迟（秒）。
    :param key_pool: 可选的 :class:`ApiKeyPool`。配置了多 key 池后，
        429 / 超时 / 5xx / 401 / 402 / 403 且尚未交付任何内容时，
        会把当前 key 标记为当日不可用并立即用下一把重试；
        轮换不消耗 ``max_retries`` 的退避预算，每次调用最多轮换两圈。
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        transport: Transport | None = None,
        max_retries: int = 3,
        retry_base_delay: float = 0.5,
        model_info=None,
        capabilities=None,
        context_window: int | None = None,
        network_timeout: float = 180.0,
        key_pool: ApiKeyPool | None = None,
        prompt_cache_mode: str = "auto",
    ) -> None:
        if not api_key:
            raise LlmError(tr('python.adapter.9eaadc183d73'))
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._transport_injected = transport is not None
        # Zen 沿用系统代理配置；其他端点保留现有 IPv4 优先直连行为。
        if transport is None and api_key == 'public' and self.base_url == 'https://opencode.ai/inference/openai/v1':
            from .opencode_local import configured_transport
            transport = configured_transport()
        self.transport = transport or UrllibTransport(max_body_bytes=None, prefer_ipv4=not is_zen(self.base_url))
        self.capabilities = capabilities or CapabilityRegistry(discover=transport is None)
        self.model_info = model_info
        self.context_window = positive(context_window)
        def duration(value, fallback):
            return min(value, 1_000_000_000) if type(value) in (int, float) and value > 0 and (type(value) is int or math.isfinite(value)) else fallback
        self.network_timeout = duration(network_timeout, 180.0)
        self.max_retries = max_retries if type(max_retries) is int and max_retries >= 0 else 3
        self.retry_base_delay = 0 if type(retry_base_delay) in (int, float) and retry_base_delay == 0 else duration(retry_base_delay, .5)
        self.key_pool = key_pool
        self.route_pool = None
        self.chatgpt = None
        self._chatgpt_endpoint = None
        if prompt_cache_mode not in {"auto", "off"}:
            raise ValueError(tr('python.adapter.2939d167a30b'))
        self.prompt_cache_mode = prompt_cache_mode

    supports_compaction_scope = True

    # -- 请求头 ----------------------------------------------------------
    def _headers(self, config: LlmCallConfig, api_key: str | None = None) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {api_key or self.api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        # OpenRouter 建议带上这些头部用于归因，非必需。
        headers.setdefault("HTTP-Referer", "https://github.com/harness")
        headers.setdefault("X-Title", "harness")
        headers.update(config.extra_headers)
        return headers

    def context_reasoning_mode(self):
        """与实际协议选择一致；路由内跨模型过滤仍保留保守上界。"""
        return 'responses' if self._chatgpt_endpoint is not None or (
            self.chatgpt is not None and self.chatgpt.logged_in) else 'chat'

    def estimate_context(self, messages):
        """容量预检只估算此协议实际会发送的思考字段。"""
        from .context_accounting import context_composition
        return context_composition(messages, reasoning=self.context_reasoning_mode())['estimated_tokens']

    def estimate_budget_context(self, messages, tools=()):
        """返回请求预留计划的输入上界。"""
        return self.request_budget_plan(messages, tools)[0]

    def request_budget_plan(self, messages, tools=()):
        """对全部启用线路取工作集上界，不租用线路，不预测最终选中槽位。"""
        tool_tokens = self.estimate_context(tools)
        tool_tokens += getattr(self.transport, 'context_tokens', 0)
        if self.route_pool is None or self.chatgpt is not None and self.chatgpt.logged_in:
            return self.estimate_context(messages) + tool_tokens, None
        with self.route_pool.lock:
            models = frozenset(model['model'] for model in self.route_pool.models if model['enabled'] and model['keys'])
        if not models:
            return self.estimate_context(messages) + tool_tokens, models
        relevant = {message.model for message in messages if isinstance(message, AssistantMessage)
                    and (message.reasoning_details or any(isinstance(block, ThinkingBlock) for block in message.content))}
        # 不匹配任何历史状态的线路投影相同，只需估算一次。
        targets = set(models & relevant)
        if models - relevant:
            targets.add(None)
        return max(self.estimate_context(_route_messages(messages, model)) for model in targets) + tool_tokens, models

    # -- 主入口 ----------------------------------------------------------
    async def stream(
        self,
        messages: Sequence[Any],
        *,
        config: LlmCallConfig | None = None,
        tools: Sequence[ToolSchema] | None = None,
        on_attempt: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        on_request: Callable[[dict[str, Any]], Awaitable[Any]] | None = None,
        max_retries: int | None = None,
        capacity_retries: int = 1,
        cache_session_id: str | None = None,
        zen_compaction: ZenCompaction | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """流式生成。

        :param messages: 内部消息列表（系统提示 + 历史 + 工具结果）。
        :param config:   调用配置；``None`` 时用适配器默认模型。
        :param tools:    本次可见的工具 schema。
        :param on_attempt: 可选的异步尝试状态观察器；不改变返回的流事件。
        :param max_retries: 单次调用的重试覆盖值，不修改共享适配器配置。
        :yields: :class:`StreamEvent` 流，最后一定是 ``done``。
        """
        scope = active_compaction.get()
        if scope is not None:
            max_retries, capacity_retries = 0, 0
        if max_retries is not None and (type(max_retries) is not int or max_retries < 0):
            raise ValueError(tr('python.adapter.8830cdc7cc4b'))
        if (config is None or not config.model) and self.model.startswith(('harness/', 'chatgpt/')):
            config = replace(config or LlmCallConfig(), model=self.model)
        if config and config.model.startswith('harness/'):
            from .provider_runtime import provider_stream
            async with aclosing(provider_stream(self, messages, config=config, tools=tools, on_attempt=on_attempt, on_request=on_request, cache_session_id=cache_session_id, zen_compaction=zen_compaction, capacity_retries=capacity_retries, max_retries=max_retries)) as response:
                async for event in response:
                    yield event
            return
        if config and config.model.startswith('chatgpt/') and getattr(self, 'subscription_pool', None) is not None:
            async with aclosing(self._subscription_stream(messages, config=config, tools=tools, on_attempt=on_attempt, on_request=on_request, cache_session_id=cache_session_id)) as response:
                async for event in response:
                    yield event
            return
        if self.chatgpt is not None and self.chatgpt.logged_in:
            try:
                info = self.chatgpt.resolve(config.model if config else None)
            except ValueError as exc:
                raise LlmError(str(exc), code='llm/chatgpt-model') from None
            selected = replace(config or LlmCallConfig(), provider='chatgpt', model=info['id'])
            worker = OpenRouterAdapter('chatgpt-subscription', base_url='https://chatgpt.com/backend-api/codex',
                model=info['id'], transport=self.transport if self._transport_injected else UrllibTransport(
                    max_body_bytes=None, prefer_ipv4=False), max_retries=self.max_retries,
                retry_base_delay=self.retry_base_delay,
                capabilities=CapabilityRegistry(discover=False),
                model_info={'context_window': info.get('context_window'), 'max_output_tokens': info.get('max_output'), 'supports_thinking': info['supports_reasoning']},
                context_window=self.context_window, network_timeout=self.network_timeout,
                prompt_cache_mode=self.prompt_cache_mode)
            worker._chatgpt_endpoint = self.chatgpt
            worker._chatgpt_epoch = self.chatgpt.epoch
            async def observed(data):
                if on_attempt:
                    await on_attempt({**data, 'model': info['id']})
            async with aclosing(worker.stream(messages, config=selected, tools=tools, on_attempt=observed,
                    on_request=on_request, max_retries=max_retries, capacity_retries=0,
                    cache_session_id=cache_session_id)) as response:
                async for event in response:
                    yield event
            return
        if config and config.model.startswith('chatgpt/') and self._chatgpt_endpoint is None:
            raise LlmError(tr('chatgpt.login_required'), code='llm/chatgpt-auth')
        if self.route_pool is not None:
            async with aclosing(self._routed_stream(messages, config=config, tools=tools, on_attempt=on_attempt, on_request=on_request, capacity_retries=capacity_retries, cache_session_id=cache_session_id, zen_compaction=zen_compaction, max_retries=max_retries)) as routed:
                async for event in routed:
                    yield event
            return
        retry_limit = self.max_retries if max_retries is None else max_retries
        config = config or LlmCallConfig(model=self.model)
        if not config.model:
            config = LlmCallConfig(
                provider=config.provider,
                model=self.model,
                temperature=config.temperature,
                max_tokens=config.max_tokens,
                top_p=config.top_p,
                reasoning_effort=config.reasoning_effort,
                extra_headers=config.extra_headers,
                extra_body=config.extra_body,
            )

        if zen_compaction is not None:
            messages, config, tools = zen_compaction.select(self.base_url, messages, config, tools)
        caps = await self.resolve_capabilities(config.model)
        body = build_request(list(messages), config=config, tools=list(tools or []), stream=True,
                             native_responses=self._chatgpt_endpoint is not None)
        if hasattr(self.transport, 'prepare_body'):
            body = self.transport.prepare_body(body)
        estimated_input_tokens = self.estimate_context(messages) + self.estimate_context(tools or [])
        estimated_input_tokens += getattr(self.transport, 'context_tokens', 0)
        try:
            body, parameters = normalize_request(body, caps, user_context=self.context_window,
                input_tokens=estimated_input_tokens)
        except LlmContextLengthError as exc:
            exc.details.update(model=config.model, base_url=self.base_url)
            raise
        from .prompt_cache import apply_prompt_cache
        body = apply_prompt_cache(body, self.base_url, mode=self.prompt_cache_mode,
                                  session_id=cache_session_id, headers=config.extra_headers)
        body = opencode_body(self.base_url, body)
        endpoint_headers = opencode_headers(self.base_url, cache_session_id)
        if hasattr(self.transport, 'request_headers'):
            endpoint_headers.update(self.transport.request_headers(cache_session_id))
        if self._chatgpt_endpoint is not None:
            from .chatgpt import responses_body
            body = responses_body(body)
        raw_body = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        url = f"{self.base_url}/responses" if self._chatgpt_endpoint is not None else f"{self.base_url}/chat/completions"

        pool = self.key_pool if self.key_pool is not None and self.key_pool.size > 1 else None
        # 轮换是跨凭证的故障转移，不占 max_retries 的退避预算；
        # 每次调用最多轮换两圈，保证所有 key 都挂时一定终止。
        rotation_budget = pool.size * 2 if pool is not None and scope is None else 0
        rotations = 0
        key_index, active_key = await pool.acurrent() if pool is not None else (0, self.api_key)

        def key_slot() -> dict[str, Any]:
            return {"api_key_slot": f"{key_index + 1}/{pool.size}"} if pool is not None else {}

        attempt = 0
        output_corrected = False
        while True:
            delivered = False
            if on_attempt is not None:
                await on_attempt({'phase': 'waiting', 'attempt': attempt + 1,
                                  'max_attempts': retry_limit + 1, 'elapsed_ms': 0, 'idle_ms': 0,
                                  'network_timeout_seconds': self.network_timeout,
                                  'network_bytes': 0, 'network_idle_ms': 0, 'progress_idle_ms': 0, **parameters,
                                  'retry_in_seconds': 0, **key_slot()})
            try:
                if scope is not None:
                    await scope.before_request()
                headers = {**endpoint_headers, **self._headers(config, active_key)}
                if self._chatgpt_endpoint is not None:
                    try:
                        headers = {**headers, **await asyncio.to_thread(self._chatgpt_endpoint.headers, self._chatgpt_epoch)}
                        self._chatgpt_secrets = [headers['Authorization'].removeprefix('Bearer ')]
                    except (ValueError, OSError):
                        raise LlmError(tr('llm.chatgpt.auth_unavailable'), code='llm/chatgpt-auth') from None
                stream_iter = self.transport.post_stream(
                    url,
                    headers=headers,
                    body=raw_body,
                    # 即使关闭生成时限，套接字无活动等待仍有上限；
                    # 持续有活动的流则可以无限运行。
                    timeout=self.network_timeout,
                )
                bounded = self._traced_consume(stream_iter, on_attempt=on_attempt, on_request=on_request,
                    body=raw_body, url=url, estimated_input_tokens=estimated_input_tokens)
                try:
                    async for event in bounded:
                        if hasattr(self.transport, 'restore_event'):
                            event = self.transport.restore_event(event, tools)
                        # 内容或工具片段交付调用方后，不得重放请求。
                        delivered = delivered or event.kind in {"start", "text", "thinking", "signature", "reasoning_details", "tool_use"}
                        yield event
                finally:
                    await bounded.aclose()
                return
            except LlmError as exc:
                output_match = _OUTPUT_LENGTH_PATTERN.search(str(exc)) if not delivered and exc.details.get('status') == 400 else None
                if output_match and not output_corrected and capacity_retries > 0:
                    limit = positive(int(output_match.group(1).replace(',', '')))
                    if limit:
                        self.capabilities.learn(self.base_url, config.model, max_output_tokens=limit)
                        caps = await self.resolve_capabilities(config.model)
                        retry_body = build_request(list(messages), config=config, tools=list(tools or []), stream=True,
                                                   native_responses=self._chatgpt_endpoint is not None)
                        if hasattr(self.transport, 'prepare_body'):
                            retry_body = self.transport.prepare_body(retry_body)
                        body, parameters = normalize_request(retry_body, caps, user_context=self.context_window,
                            input_tokens=estimated_input_tokens)
                        body = apply_prompt_cache(body, self.base_url, mode=self.prompt_cache_mode,
                                                  session_id=cache_session_id, headers=config.extra_headers)
                        body = opencode_body(self.base_url, body)
                        raw_body = json.dumps(body, ensure_ascii=False, allow_nan=False).encode('utf-8')
                        output_corrected = True
                        continue
                if output_match:
                    raise LlmResponseError(tr('python.adapter.bdc2aaf72db7'), code='llm/model-capacity',
                        status=400, model=config.model, base_url=self.base_url) from exc
                # 上下文超长（400）：端点在报错文本里自报真实窗口。尚未交付
                # 任何内容时分类为专门错误抛给上层——上层学习该窗口、收敛
                # max_tokens 后原地重试；换 key / 退避重试都救不了请求形状。
                context_limit = None if delivered else _parse_context_length_limit(exc)
                if context_limit is not None:
                    self.capabilities.learn(self.base_url, config.model, context_window=context_limit)
                    raise LlmContextLengthError(
                        str(exc), limit=context_limit,
                        status=exc.details.get("status"),
                        attempts=attempt + 1, partial_output=delivered, model=config.model, base_url=self.base_url,
                        capacity_corrections=int(output_corrected),
                    ) from exc
                # Key 轮换：429/超时/5xx（transient）或 401/402/403（key 本身失效），
                # 且还没交付任何内容时，标记当前 key 并在冷却结束后换下一把。
                if (pool is not None and not delivered and rotations < rotation_budget
                        and self._switchable_to_next_key(exc)):
                    # 凭据轮换不能绕过服务端冷却，即使轮换不消耗重试次数。
                    rotation_delay = retry_delay(exc.details, 0)
                    await pool.amark_failed(key_index)
                    rotations += 1
                    key_index, active_key = await pool.acurrent()
                    if on_attempt is not None:
                        await on_attempt({'phase': 'retrying', 'attempt': attempt + 1,
                                          'max_attempts': retry_limit + 1, 'elapsed_ms': 0, 'idle_ms': 0,
                                          'retry_in_seconds': rotation_delay, **key_slot()})
                    if rotation_delay:
                        await asyncio.sleep(rotation_delay)
                    continue
                if scope is not None and pool is not None and not delivered and self._switchable_to_next_key(exc):
                    await pool.arotate()
                # 已交付内容后的中途失败不能重放，但当前 key 的线路刚出过一次
                # 故障：401/402/403 说明 key 本身失效，当日拉黑；其余瞬时故障
                # （如上游空闲超时）只做软轮换——让上层的断点续传/下一回合
                # 自然落到下一把 key 的线路上，而不浪费当日额度。
                if pool is not None and delivered and self._switchable_to_next_key(exc):
                    if exc.details.get("status") in {401, 402, 403}:
                        await pool.amark_failed(key_index)
                    else:
                        await pool.arotate()
                # 只有瞬时错误才重试；参数错误等直接抛出。
                if delivered or not getattr(exc, "transient", False) or attempt >= retry_limit:
                    exc.details.update(attempts=attempt + 1, partial_output=delivered, **key_slot())
                    raise
                attempt += 1
                # 指数退避 + 抖动，避免多个并发请求同时重试打爆上游。
                delay = self.retry_base_delay * (2 ** (attempt - 1)) + random.uniform(0, self.retry_base_delay)
                delay = retry_delay(exc.details, delay)
                if on_attempt is not None:
                    await on_attempt({'phase': 'retrying', 'attempt': attempt,
                                      'max_attempts': retry_limit + 1, 'elapsed_ms': 0, 'idle_ms': 0,
                                      'retry_in_seconds': delay, **key_slot()})
                await asyncio.sleep(delay)

    async def _subscription_stream(self, messages, *, config, tools, on_attempt, on_request, cache_session_id):
        """Harness 使用多账号池，当前账号选择不依赖桌面网关开关。"""
        from .provider_runtime import subscription_stream
        async with aclosing(subscription_stream(self, messages, config=config, tools=tools, on_attempt=on_attempt,
                on_request=on_request, cache_session_id=cache_session_id)) as stream:
            async for event in stream:
                yield event

    async def _routed_stream(self, messages, *, config, tools, on_attempt, on_request=None, capacity_retries=1, cache_session_id=None, zen_compaction=None, max_retries=None):
        pool = self.route_pool
        call = config or LlmCallConfig(model=self.model)
        attempt = 0
        physical_attempts = 0
        # 单槽没有可故障转移的线路，沿用普通请求的有界瞬时重试。
        # 摘要由节点执行器统一重试，普通请求遍历所有凭据槽位。
        single_retries = (self.max_retries if max_retries is None else max_retries) if pool.size == 1 else 0
        if active_compaction.get() is not None:
            single_retries = 0
        # 按调用开始时的槽位数量设定上限，不能因凭据池自动恢复而无限循环。
        route_limit = 1 if active_compaction.get() is not None else max(1, pool.size)
        attempted = set()
        last_error = None
        while True:
            acquired = await pool.acquire(exclude=attempted)
            if acquired is None:
                # 配置实时移除尚未尝试的槽位时，保留最后的供应商错误。
                last_error.details.update(route_retry=False, route_exhausted=True)
                raise last_error
            model, key, identity, generation = acquired
            attempted.add(identity)
            attempt += 1
            delivered = False
            selected = replace(call, model=model['model'],
                               extra_headers={k: v for k, v in call.extra_headers.items() if k.lower() != 'authorization'},
                               extra_body={k: v for k, v in call.extra_body.items() if k != 'model'})
            # 不能跨模型传递供应商的不透明思考数据。
            wire_messages = _route_messages(messages, selected.model)
            try:
                worker = OpenRouterAdapter(key['value'], base_url=model['base_url'], model=model['model'],
                    transport=self.transport if self._transport_injected else None,
                    max_retries=single_retries, retry_base_delay=self.retry_base_delay,
                    capabilities=self.capabilities, model_info=model, context_window=self.context_window,
                    network_timeout=self.network_timeout,
                    prompt_cache_mode=self.prompt_cache_mode)
            except BaseException:
                # 资料校验或实例构造失败发生在请求开始前，也必须释放已取得的租约。
                pool.release(identity)
                raise
            worker._diagnostic_redactor = pool.redact
            slot = {'model': model['model'], 'model_id': model['id'], 'api_key_id': key['id'],
                    'api_key_slot': f"{model['keys'].index(key) + 1}/{len(model['keys'])}", 'cycle': pool.cycle}
            async def observed(data):
                nonlocal physical_attempts
                if data.get('phase') == 'waiting':
                    physical_attempts += 1
                if on_attempt:
                    await on_attempt({**data, **slot, 'attempt': physical_attempts or attempt})
            try:
                async with aclosing(worker.stream(wire_messages, config=selected, tools=tools, on_attempt=observed, on_request=on_request, capacity_retries=capacity_retries, cache_session_id=cache_session_id, zen_compaction=zen_compaction)) as response:
                    async for event in response:
                        delivered = delivered or event.kind in {'text', 'thinking', 'signature', 'reasoning_details', 'tool_use'}
                        if event.kind == 'done':
                            pool.succeed(identity, generation)
                        yield event
                return
            except LlmError as exc:
                last_error = exc
                # 上游错误可能回显凭据，不能原样传播。
                exc.message = pool.redact(exc.message)
                exc.args = (exc.message,)
                exc.details = pool.redact(exc.details)
                exc.__cause__ = None
                exc.__context__ = None
                exc.__suppress_context__ = True
                if exc.code in {'llm/stream-output-limit', 'llm/thinking-limit', 'llm/context-length', 'llm/model-capacity'}:
                    exc.details.update(model=model['model'], base_url=model['base_url'])
                    raise
                # 参数、TLS 信任及显式终态错误不能靠换凭据恢复，也不能污染槽位状态。
                if not self._switchable_to_next_key(exc) and exc.code != 'llm/invalid-tool-response':
                    exc.details.update(route_retry=False, partial_output=delivered, **slot)
                    raise
                # 冷却约束当前凭据的重试；尚未输出时允许改用独立凭据。
                # 单槽和部分输出的恢复仍遵守供应商等待时间。
                pool.fail(identity, generation)
                delay = retry_delay(exc.details, 0) if pool.size == 1 or delivered else 0
                exc.details.update(route_retry=True, partial_output=delivered, **slot)
                # 已交付内容不能原地重放；瞬时中断交给上层有界续接，
                # 单槽耗尽不能覆盖这个独立的恢复入口。摘要仍由节点执行器管理。
                if delivered and exc.transient and active_compaction.get() is None:
                    exc.details.update(route_attempts=physical_attempts or attempt)
                    raise
                if attempt >= route_limit:
                    exc.details.update(route_retry=False, route_exhausted=True, route_attempts=physical_attempts or attempt)
                    exc.message = tr('python.adapter.route_exhausted', p0=str(physical_attempts or attempt), p1=exc.message)
                    exc.args = (exc.message,)
                    raise
                if delivered:
                    raise
                delay = max(delay, max(0, pool.wait_until - time.monotonic()))
                if on_attempt:
                    await on_attempt({'phase': 'retrying', 'attempt': physical_attempts or attempt, 'max_attempts': route_limit,
                                      'retry_in_seconds': delay, **slot})
                if delay:
                    await asyncio.sleep(delay)
            finally:
                pool.release(identity)

    async def _traced_consume(self, raw_stream, *, on_attempt, on_request, body, url, estimated_input_tokens=None):
        trace_id = None
        status = 'interrupted'
        usage = None
        if on_request is not None:
            trace_id = await on_request({'phase': 'started', 'body': body.decode('utf-8'), 'url': url,
                'estimated_input_tokens': estimated_input_tokens})
        started = time.monotonic()
        first_event_ms = None
        event_count = 0
        terminal_reason = 'stream_ended'
        error_type = None
        diagnostic = None
        error_code = None
        error_details = {}
        def record_usage(snapshot):
            nonlocal usage
            usage = snapshot.to_dict()

        try:
            async with aclosing(self._bounded_consume(raw_stream, on_attempt=on_attempt, on_usage=record_usage)) as bounded:
                async for event in bounded:
                    if first_event_ms is None:
                        first_event_ms = max(0, round((time.monotonic() - started) * 1000))
                    event_count += 1
                    if event.kind == 'usage' and event.usage is not None:
                        usage = event.usage.to_dict()
                    if event.kind == 'done':
                        status = 'completed'
                        terminal_reason = 'done'
                    yield event
        except asyncio.CancelledError:
            if status != 'completed':
                terminal_reason = 'cancelled'
            raise
        except GeneratorExit:
            if status != 'completed':
                terminal_reason = 'consumer_closed'
            raise
        except BaseException as exc:
            if self._chatgpt_endpoint is not None and isinstance(exc, LlmError):
                with self._chatgpt_endpoint.lock:
                    credential = self._chatgpt_endpoint.credentials or {}
                    secrets = [credential.get('access'), credential.get('refresh'), *getattr(self, '_chatgpt_secrets', [])]
                    def redact(value):
                        if isinstance(value, str):
                            for secret in secrets:
                                if secret:
                                    value = value.replace(secret, '[redacted]')
                        elif isinstance(value, dict):
                            return {key: redact(item) for key, item in value.items()}
                        elif isinstance(value, (list, tuple)):
                            return [redact(item) for item in value]
                        return value
                    exc.message = redact(exc.message)
                    exc.details = redact(exc.details)
                    exc.args = (exc.message,)
                    exc.__cause__ = None
                    exc.__context__ = None
                    exc.__suppress_context__ = True
            status = 'failed'
            terminal_reason = 'error'
            error_type = type(exc).__name__[:80]
            error_code = getattr(exc, 'code', None)
            error_details = failure_diagnostics(exc)
            if hasattr(exc, 'text_diagnostic'):
                diagnostic = dict(exc.text_diagnostic)
                probe = diagnostic.pop('input_probe')
                def contains(value):
                    if isinstance(value, str):
                        return probe in value
                    if isinstance(value, list):
                        return any(contains(item) for item in value)
                    if isinstance(value, dict):
                        return any(contains(item) for item in value.values())
                    return False
                diagnostic['input_contains_same_fragment'] = contains(json.loads(body))
                redact = getattr(self, '_diagnostic_redactor', None)
                context = diagnostic['context']
                if redact is not None:
                    context = redact(context)
                else:
                    keys = [self.api_key]
                    if self.key_pool is not None:
                        keys.extend(self.key_pool.key_at(index) for index in range(self.key_pool.size))
                    for key in keys:
                        if isinstance(key, str) and key:
                            context = context.replace(key, '[redacted]')
                diagnostic['context'] = context[:80]
            raise
        finally:
            if trace_id is not None:
                await on_request({'phase': status, 'trace_id': trace_id, 'usage': usage,
                    'observation': {'duration_ms': max(0, round((time.monotonic() - started) * 1000)),
                        'first_event_ms': first_event_ms, 'event_count': event_count,
                        'terminal_reason': terminal_reason, 'error_type': error_type,
                        'error_code': error_code, 'error_details': error_details, 'text_diagnostic': diagnostic}})

    async def resolve_capabilities(self, model=None):
        if model and model.startswith('harness/'):
            entry = next((m for m in self.provider_connections.catalog() if m['id'] == model), None)
            if entry is None:
                raise LlmError(tr('models.model.missing'), code='llm/model-missing')
            return {'context_window': entry['context_window'] or None, 'max_output_tokens': entry['max_output'] or None, 'supports_thinking': entry['supports_reasoning'], 'supported_efforts': entry['reasoning_efforts'], 'supports_vision': 'image' in self.provider_connections.resolve(model)[2]['input'], 'confirmed': True, 'sources': ['configuration']}
        if model and model.startswith('chatgpt/') and getattr(self, 'subscription_pool', None) is not None:
            for slot, duplicate in self.subscription_pool._unique():
                if not duplicate and slot.enabled and slot.account.logged_in:
                    info = next((item for item in slot.account.status()['models'] if item['id'] == model), None)
                    if info:
                        return {'context_window': info.get('context_window'), 'max_output_tokens': info.get('max_output'), 'supports_thinking': info['supports_reasoning'], 'supported_efforts': info['reasoning_efforts'], 'confirmed': True, 'sources': ['chatgpt-account']}
            raise LlmError(tr('models.model.missing'), code='llm/model-missing')
        account = self.chatgpt or self._chatgpt_endpoint
        if account is not None and account.logged_in:
            info = account.resolve(model)
            return {'context_window': info.get('context_window'), 'max_output_tokens': info.get('max_output'),
                    'supports_thinking': info['supports_reasoning'], 'supported_efforts': info['reasoning_efforts'],
                    'confirmed': True, 'sources': ['chatgpt-account']}
        selected = model or self.model
        info = self.model_info
        manual = (info.to_dict() if hasattr(info, 'to_dict') else info) or {}
        if self.route_pool is not None:
            manual = self.route_pool.select()[0]
            selected = manual['model']
            url = manual['base_url']
        else:
            url = self.base_url
            if manual.get('model') and manual['model'] != selected:
                manual = {}
        return await self.capabilities.resolve(url, selected, manual)

    @staticmethod
    def _switchable_to_next_key(exc: LlmError) -> bool:
        """判断错误是否值得换 key：瞬时错误（429/超时/5xx）或 key 失效（401/402/403）。"""
        if getattr(exc, "transient", False):
            return True
        status = exc.details.get("status")
        return status in {401, 402, 403}

    async def _bounded_consume(self, raw_stream: AsyncIterator[bytes], *, on_attempt=None, on_usage=None) -> AsyncIterator[StreamEvent]:
        """监控网络无活动时长，进展仅用于观察，并保留流资源收尾。"""
        loop = asyncio.get_running_loop()
        last_progress = loop.time()
        last_report = None
        total = 0

        def progressed():
            nonlocal last_progress
            last_progress = loop.time()

        async def monitored():
            nonlocal total, last_report
            while True:
                try:
                    async with asyncio.timeout(self.network_timeout):
                        chunk = await anext(raw_stream)
                except StopAsyncIteration:
                    return
                except TimeoutError as exc:
                    raise LlmTimeoutError(tr('python.adapter.9432086171d5'),
                                          seconds=self.network_timeout, phase='network') from exc
                except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
                    raise network_failure(exc, phase='stream') from exc
                total += len(chunk)
                now = loop.time()
                if chunk and on_attempt and (last_report is None or now - last_report >= 1):
                    last_report = now
                    await on_attempt({'network_bytes': total, 'network_idle_ms': 0,
                                      'progress_idle_ms': int((now - last_progress) * 1000)})
                yield chunk

        source = monitored()
        wire = source
        if self._chatgpt_endpoint is not None:
            from .chatgpt import chat_chunks
            wire = chat_chunks(source)
        parsed = self._consume(wire, on_progress=progressed, on_usage=on_usage)
        try:
            while True:
                try:
                    event = await anext(parsed)
                except StopAsyncIteration:
                    return
                except (ValueError, TypeError, AttributeError, KeyError, IndexError) as exc:
                    raise LlmResponseError(tr('python.adapter.57f13cc8dd0e'), code='llm/invalid-response') from exc
                yield event
        finally:
            errors: list[BaseException] = []
            streams = [parsed, *([wire] if wire is not source else []), source, raw_stream]
            for owned in streams:
                try:
                    close = getattr(owned, 'aclose', None)
                    if close is not None:
                        await close()
                except BaseException as exc:
                    errors.append(exc)
            raise_cleanup_errors(errors)

    async def _consume(self, raw_stream: AsyncIterator[bytes], *, on_progress=None, on_usage=None) -> AsyncIterator[StreamEvent]:
        """消费一个 SSE 流，产出内部 :class:`StreamEvent`。

        内部维护：文本累加器、思考累加器、按 index 聚合的工具调用参数。
        """
        text_parts: list[str] = []
        thinking_parts: list[str] = []
        tool_calls: dict[int, dict[str, Any]] = {}
        usage: TokenUsage | None = None
        usage_fields: dict[str, Any] = {}
        stop_reason: str | None = None
        emitted_start = False
        saw_terminal = False

        def raw_event(data):
            nonlocal saw_terminal
            if data.strip() == '[DONE]':
                saw_terminal = True

        def capture_usage(raw: Any) -> TokenUsage | None:
            # 供应商报告的是请求用量的累计快照，不是词元增量。
            # 部分快照保留 message_start 中已提供的字段。
            if isinstance(raw, dict):
                usage_fields.update({key: value for key, value in raw.items() if value is not None})
                value = _parse_usage(usage_fields)
                if on_usage is not None:
                    on_usage(value)
                return value
            return usage

        async for chunk in parse_sse_stream(raw_stream, on_event=raw_event):
            # 归档即时记录累计快照，不把失败尝试的用量混入重试后的模型结果。
            raw_usage = chunk.get('usage') or (chunk.get('message') or {}).get('usage')
            if raw_usage:
                usage = capture_usage(raw_usage)
                if active_compaction.get() is not None:
                    yield StreamEvent(kind='usage', usage=usage)
            fragments = [choice.get("delta", {}) for choice in chunk.get("choices", [])]
            if chunk.get("type") == "content_block_delta":
                fragments.append(chunk.get("delta", {}))
            elif chunk.get('type') == 'content_block_start':
                fragments.append(chunk.get('content_block', {}))
            if on_progress and any(
                    any(fragment.get(key) for key in ('content', 'text', 'partial_json', 'thinking',
                        'reasoning', 'reasoning_content', 'reasoning_details', 'signature'))
                    or any(call.get('function', {}).get('arguments') or call.get('function', {}).get('name')
                           for call in fragment.get('tool_calls', []) or [])
                    for fragment in fragments):
                on_progress()
            # Anthropic 风格（部分供应商）的直接透传
            if "type" in chunk and chunk["type"] in (
                "message_start", "content_block_start", "content_block_delta", "message_delta", "message_stop",
            ):
                async for event in self._consume_anthropic(chunk, text_parts, thinking_parts, tool_calls):
                    if event.kind == "start":
                        emitted_start = True
                    yield event
                if chunk["type"] == "message_delta":
                    delta = chunk.get("delta", {})
                    if "stop_reason" in delta:
                        stop_reason = delta["stop_reason"]
                    if "usage" in chunk:
                        usage = capture_usage(chunk["usage"])
                if chunk["type"] == "message_start":
                    msg = chunk.get("message", {})
                    if "usage" in msg:
                        usage = capture_usage(msg["usage"])
                if chunk["type"] == "message_stop":
                    saw_terminal = True
                    break
                continue

            # OpenAI 兼容格式
            if "error" in chunk:
                err = chunk["error"]
                message = str(err.get("message", err) if isinstance(err, dict) else err)
                status = err.get("code") if isinstance(err, dict) else None
                try:
                    status_code = int(status) if status is not None else None
                except (TypeError, ValueError):
                    status_code = None
                # 部分供应商返回 HTTP 200 后，才通过 SSE 报告失败。
                # 只保留有界诊断文本，不保留供应商元数据。
                if "empty response" in message.lower() and (status_code is None or status_code in {408, 429} or status_code >= 500):
                    raise LlmResponseError(tr('python.adapter.6a7b138d632b'),
                                           code="llm/empty-response", transient=True,
                                           status=status_code, provider_message=message[:500])
                raise LlmResponseError(tr('python.adapter.5c8e9b813e09', p0=f'{message[:500]}'),
                                       transient=(status_code in {408, 429} or status_code >= 500) if status_code is not None
                                       else any(hint in message.lower() for hint in _TRANSIENT_ERROR_HINTS),
                                       status=status_code)

            # usage 常常作为独立分片上报（choices 为空数组），
            # 必须在判断 choices 之前处理，否则会被下面的 continue 跳过。
            if chunk.get("usage"):
                usage = capture_usage(chunk["usage"])
                # OpenAI 的最终用量块没有 choices；完成状态与计量已确定，
                # 缺失或延迟的 [DONE] 或 HTTP 流结束
                # 不能让已完成任务一直等待到超时。
                if stop_reason and chunk.get("choices") == []:
                    break

            if "choices" not in chunk:
                continue

            for choice in chunk["choices"]:
                delta = choice.get("delta", {})
                details = delta.get('reasoning_details') or []
                reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                if not reasoning and details:
                    reasoning = ''.join(detail.get('text') or detail.get('summary') or '' for detail in details)
                _validate_generated_text(reasoning or '', thinking_parts[-1] if thinking_parts else '',
                    field='reasoning_content' if delta.get('reasoning_content') else 'reasoning' if delta.get('reasoning') else 'reasoning_details')
                _validate_generated_text(delta.get('content') or '', text_parts[-1] if text_parts else '')
                for detail in details:
                    _validate_generated_text(detail.get('text') or detail.get('summary') or '', field='reasoning_details')
                # 首次拿到内容时发一个 start 事件
                if not emitted_start and (delta.get("content") or delta.get("reasoning_content") or delta.get("reasoning") or delta.get("reasoning_details") or delta.get("tool_calls")):
                    yield StreamEvent(kind="start")
                    emitted_start = True

                # 完整保留供应商原始序列，包括加密数据和签名，
                # 不修改其内容；明文只是界面投影。
                if details:
                    yield StreamEvent(kind='reasoning_details', reasoning_details=tuple(details))
                if reasoning:
                    thinking_parts.append(reasoning)
                    yield StreamEvent(kind="thinking", thinking=reasoning)

                # 普通文本
                content = delta.get("content")
                if content:
                    text_parts.append(content)
                    yield StreamEvent(kind="text", text=content)

                # 工具调用增量：按 index 聚合 name 与 arguments
                for tc in delta.get("tool_calls", []) or []:
                    idx = tc.get("index", 0)
                    entry = tool_calls.setdefault(idx, {"id": None, "name": "", "arguments": ""})
                    if tc.get("id"):
                        entry["id"] = tc["id"]
                    fn = tc.get("function", {})
                    if fn.get("name"):
                        entry["name"] += fn["name"]
                    if fn.get("arguments"):
                        entry["arguments"] += fn["arguments"]

                if choice.get("finish_reason"):
                    stop_reason = choice["finish_reason"]

        # -- 流结束，产出工具调用与 done ---------------------------------
        if not saw_terminal and not stop_reason:
            raise LlmResponseError(tr('python.adapter.afdf39518751'),
                                   code='llm/incomplete-response', transient=True)
        # 供应商截断的思考或空正文交给轮次续写，不在传输层主动终止。
        if not any(part.strip() for part in text_parts) and not tool_calls and stop_reason not in {'length', 'max_tokens'}:
            if thinking_parts or stop_reason in {"content_filter", "error"}:
                raise LlmResponseError(tr('python.adapter.51a7544c6a8c'), code="llm/no-answer")
            raise LlmResponseError(tr('python.adapter.6a7b138d632b'),
                                   code="llm/empty-response", transient=True)
        # 工具或文本响应必须保留 start 事件。
        if not emitted_start:
            yield StreamEvent(kind="start")

        for idx in sorted(tool_calls):
            entry = tool_calls[idx]
            raw_args = entry["arguments"] or "{}"
            try:
                args = json.loads(raw_args)
            except json.JSONDecodeError as exc:
                raise LlmResponseError(tr('python.adapter.8cbbe9299276', p0=f'{raw_args[:500]}'),
                                       code='llm/invalid-tool-response') from exc
            # 参数错误不能伪装成可执行字典；单模型和路由请求共用拒绝边界。
            if not isinstance(args, dict) or '__parse_error__' in args:
                raise LlmResponseError(tr('python.adapter.f4aba9b60fde'), code='llm/invalid-tool-response')
            yield StreamEvent(
                kind="tool_use",
                tool_use=ToolUseBlock(
                    id=entry["id"] or f"call_{uuid.uuid4().hex[:12]}",
                    name=entry["name"],
                    input=args,
                ),
            )

        if usage is not None:
            yield StreamEvent(kind="usage", usage=usage)
        yield StreamEvent(kind="done", stop_reason=stop_reason)

    async def _consume_anthropic(
        self,
        chunk: dict[str, Any],
        text_parts: list[str],
        thinking_parts: list[str],
        tool_calls: dict[int, dict[str, Any]],
    ) -> AsyncIterator[StreamEvent]:
        """处理 Anthropic 原生 SSE 事件（部分 OpenRouter 模型会返回这种格式）。"""
        ctype = chunk["type"]
        if ctype == "content_block_start":
            yield StreamEvent(kind="start")
            block = chunk.get("content_block", {})
            if block.get("type") == "tool_use":
                tool_calls[chunk.get("index", len(tool_calls))] = {
                    "id": block.get("id"),
                    "name": block.get("name", ""),
                    "arguments": "",
                }
            elif block.get("type") == "text":
                text = block.get("text", "")
                _validate_generated_text(text, text_parts[-1] if text_parts else '')
                if text:
                    text_parts.append(text)
                    yield StreamEvent(kind='text', text=text)
            elif block.get("type") == "thinking":
                _validate_generated_text(block.get('thinking') or '', thinking_parts[-1] if thinking_parts else '', field='thinking')
                thinking_parts.append(block.get('thinking') or '')
                yield StreamEvent(kind='thinking', thinking=block.get('thinking') or '')
        elif ctype == "content_block_delta":
            delta = chunk.get("delta", {})
            dtype = delta.get("type")
            if dtype == "text_delta":
                text = delta.get("text", "")
                _validate_generated_text(text, text_parts[-1] if text_parts else '')
                text_parts.append(text)
                yield StreamEvent(kind="text", text=text)
            elif dtype == "thinking_delta":
                thinking = delta.get("thinking", "")
                _validate_generated_text(thinking, thinking_parts[-1] if thinking_parts else '', field='thinking')
                thinking_parts.append(thinking)
                yield StreamEvent(kind="thinking", thinking=thinking)
            elif dtype == "signature_delta":
                yield StreamEvent(kind="signature", signature=delta.get("signature", ""))
            elif dtype == "input_json_delta":
                idx = chunk.get("index", 0)
                if idx in tool_calls:
                    tool_calls[idx]["arguments"] += delta.get("partial_json", "")
        # message_start / message_delta / message_stop 由调用方处理

    # -- 非流式便捷方法 --------------------------------------------------
    async def complete(
        self,
        messages: Sequence[Any],
        *,
        config: LlmCallConfig | None = None,
        tools: Sequence[ToolSchema] | None = None,
        on_event: Callable[[StreamEvent], None] | None = None,
        on_request: Callable[[dict[str, Any]], Awaitable[Any]] | None = None,
        cache_session_id: str | None = None,
        zen_compaction: ZenCompaction | None = None,
    ) -> AssistantMessage:
        """一次性拿到完整回复（内部仍然走流式，然后拼装成消息）。

        便于测试与非流式场景。on_event 可观察已收到的增量，包括中断前的用量。
        """
        content: list[ContentBlock] = []
        reasoning_details: list[dict[str, Any]] = []
        usage: TokenUsage | None = None
        stop_reason: str | None = None
        model = (config.model if config else self.model)
        request_messages = list(messages)
        recoveries = 0
        async def observed(data):
            nonlocal model
            if data.get('model'):
                model = data['model']
        while True:
            try:
                stream_options = {'config': config, 'tools': tools}
                signature = inspect.signature(self.stream)
                if zen_compaction is not None and 'zen_compaction' in signature.parameters:
                    stream_options['zen_compaction'] = zen_compaction
                if on_request is not None and 'on_request' in signature.parameters:
                    stream_options['on_request'] = on_request
                if cache_session_id is not None and 'cache_session_id' in signature.parameters:
                    stream_options['cache_session_id'] = cache_session_id
                if 'on_attempt' in signature.parameters or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in signature.parameters.values()):
                    stream_options['on_attempt'] = observed
                async with aclosing(self.stream(request_messages, **stream_options)) as response_stream:
                    async for event in response_stream:
                        if on_event is not None:
                            on_event(event)
                        if event.kind == "text":
                            # 相邻文本块合并，保持块数量精简
                            if content and isinstance(content[-1], TextBlock):
                                content[-1] = TextBlock(text=content[-1].text + event.text)
                            else:
                                content.append(TextBlock(text=event.text))
                        elif event.kind == "thinking":
                            merge_thinking_event(content, event)
                        elif event.kind == 'signature':
                            merge_thinking_event(content, event)
                        elif event.kind == 'reasoning_details':
                            reasoning_details.extend(event.reasoning_details)
                        elif event.kind == "tool_use" and event.tool_use is not None:
                            content.append(event.tool_use)
                        elif event.kind == "usage":
                            usage = event.usage
                        elif event.kind == "done":
                            stop_reason = event.stop_reason
                            break
                break
            except LlmError as exc:
                if active_compaction.get() is not None or not self.route_pool or not exc.details.get('route_retry'):
                    raise
                if recoveries >= self.max_retries:
                    exc.details.update(route_retry=False, request_recovery_exhausted=True,
                                       recoveries=recoveries, max_recoveries=self.max_retries)
                    raise
                # 部分响应的续接与槽位切换一样，不能缩短供应商要求的冷却。
                delay = retry_delay(exc.details, 0)
                if delay:
                    await asyncio.sleep(delay)
                recoveries += 1
                partial = ''.join(b.text for b in content if isinstance(b, TextBlock))
                content[:] = [b for b in content if isinstance(b, TextBlock)]
                reasoning_details.clear()
                usage = None
                stop_reason = None
                if partial.strip():
                    from .types import UserMessage
                    request_messages = [*messages, AssistantMessage(content=(TextBlock(partial),), model=model),
                                        UserMessage(tr('python.adapter.abc5fed70625'))]
                else:
                    request_messages = list(messages)
        return AssistantMessage(content=tuple(content), model=model, stop_reason=stop_reason, usage=usage,
                                reasoning_details=tuple(reasoning_details))


def _route_messages(messages, model):
    """预算和真实发送共用跨模型状态过滤，保留原消息及工具配对。"""
    return [replace(message, content=tuple(block for block in message.content if not isinstance(block, ThinkingBlock)), reasoning_details=())
            if isinstance(message, AssistantMessage) and (model is None or message.model != model) else message for message in messages]


def _parse_usage(raw: Any) -> TokenUsage:
    """解析 OpenAI 或 Anthropic 的完整累计用量快照。"""
    if not isinstance(raw, dict):
        return TokenUsage()
    details = raw.get("completion_tokens_details") or raw.get("output_tokens_details") or {}
    prompt_details = raw.get("prompt_tokens_details") or raw.get("input_tokens_details") or {}
    total_input = (raw.get("prompt_tokens") or 0) if "prompt_tokens" in raw else sum(
        raw.get(key, 0) or 0 for key in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
    reported = 'cached_tokens' in prompt_details or 'cache_read_input_tokens' in raw or 'prompt_cache_hit_tokens' in raw
    return TokenUsage(
        input_tokens=total_input,
        cache_reported_input_tokens=total_input if reported else 0,
        output_tokens=raw.get("completion_tokens", raw.get("output_tokens", 0)) or 0,
        reasoning_tokens=details.get("reasoning_tokens", details.get("thinking_tokens", 0)) or 0,
        cache_read_tokens=prompt_details.get("cached_tokens", raw.get("cache_read_input_tokens", raw.get("prompt_cache_hit_tokens", 0))) or 0,
    )


__all__ = ["OpenRouterAdapter", "DEFAULT_BASE_URL", "DEFAULT_MODEL"]
