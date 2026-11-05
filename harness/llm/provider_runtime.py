"""受监督的供应商运行时：有界消息、凭据提交握手和取消收尾。"""
from __future__ import annotations

import asyncio
from contextlib import aclosing, asynccontextmanager
import json
import os
import shutil
import time

from harness.i18n import tr
from ..core.cleanup import join_cleanup
from ..core.errors import LlmError, LlmTimeoutError
from ..core.jsonutil import to_jsonable
from ..tools.process_env import stdio_env
from .opencode_local import _start_process, _stop_stream_process
from .providers import RUNTIME, decode
from .key_pool_lock import state_lock
from .types import StreamEvent, TokenUsage, ToolUseBlock, ToolResultMessage, AssistantMessage
from .translate import messages_to_wire
from .request_limits import normalize_request


# 原生供应商有时在 HTTP 200 的流中发送失败事件；这些短语表示上游线路
# 的瞬时故障，不能因为外层状态码是 200 就把当前轮次判为永久失败。
_TRANSIENT_FAILURE_HINTS = (
    'idle timeout',
    'temporarily unavailable',
    'service unavailable',
    'gateway timeout',
    'overloaded',
    'connection reset',
    'connection aborted',
)


def _is_transient_failure(status: int, message: str) -> bool:
    """按状态码及受控供应商短语判断流式失败是否可以重试。"""
    if status in {408, 429} or status >= 500:
        return True
    if status >= 400:
        return False
    lowered = message.casefold()
    return any(hint in lowered for hint in _TRANSIENT_FAILURE_HINTS)


_NETWORK_CODES = {'ECONNRESET', 'ECONNABORTED', 'ECONNREFUSED', 'EPIPE', 'ETIMEDOUT',
                  'ENETUNREACH', 'EHOSTUNREACH', 'EAI_AGAIN', 'UND_ERR_SOCKET',
                  'UND_ERR_CONNECT_TIMEOUT', 'UND_ERR_HEADERS_TIMEOUT', 'UND_ERR_BODY_TIMEOUT'}
_TIMEOUT_TYPES = {'APIConnectionTimeoutError', 'TimeoutError'}
_TIMEOUT_CODES = {'ETIMEDOUT', 'UND_ERR_CONNECT_TIMEOUT', 'UND_ERR_HEADERS_TIMEOUT', 'UND_ERR_BODY_TIMEOUT'}


def _provider_failure(value, request):
    """仅用明确的传输身份恢复；供应商拒绝、协议损坏和凭据刷新保持原边界。"""
    status = value.get('status', 0)
    message = value.get('message', '')
    if type(status) is not int or not isinstance(message, str):
        raise ValueError
    details = {'status': status, 'phase': 'provider'}
    for name in ('error_type', 'error_code', 'provider_trace_id'):
        field = value.get(name)
        if field is not None:
            if not isinstance(field, str):
                raise ValueError
            details[name] = field
    secrets = [request.get('credential', {}).get(k) for k in ('key', 'access', 'refresh')]
    secrets.extend(request.get('connection', {}).get('auth', {}).get('env', {}).values())
    for secret in secrets:
        if secret:
            message = message.replace(secret, '[redacted]')
            for name in ('error_type', 'error_code', 'provider_trace_id'):
                if name in details:
                    details[name] = details[name].replace(secret, '[redacted]')
    for name in ('error_type', 'error_code', 'provider_trace_id'):
        if name in details:
            details[name] = details[name][:128]
    transport_failure = status < 400 and (details.get('error_code') in _NETWORK_CODES
        or not details.get('error_code') and details.get('error_type') in {'APIConnectionError', *_TIMEOUT_TYPES})
    if transport_failure and (details.get('error_code') in _TIMEOUT_CODES or details.get('error_type') in _TIMEOUT_TYPES):
        return LlmTimeoutError(tr('python.adapter.9432086171d5'), provider_message=message, **details)
    if status < 400 and details.get('error_code') == 'HARNESS_INCOMPLETE_STREAM':
        return LlmError(tr('models.request.failed', reason=message), code='llm/incomplete-response', transient=True, **details)
    if status == 409 and details.get('error_code') == 'MODEL_CALL_FAILED':
        return LlmError(tr('models.request.failed', reason=message), code='llm/provider-model-call-failed', **details)
    classified_connection = details.get('error_type') in {'APIConnectionError', *_TIMEOUT_TYPES} and details.get('error_code')
    return LlmError(tr('models.request.failed', reason=message), code='llm/request' if transport_failure else 'llm/provider',
        transient=transport_failure or not classified_connection and _is_transient_failure(status, message), **details)


@asynccontextmanager
async def credential_guard(store, connection_id):
    """跨进程串行刷新同一接口；取消获取仍先回收已取得的锁。"""
    guard = state_lock(store.home / ('provider-auth-' + connection_id))
    acquiring = asyncio.create_task(asyncio.to_thread(guard.__enter__))
    entered = False
    try:
        try:
            await asyncio.wait((acquiring,))
            acquiring.result()
            entered = True
        except asyncio.CancelledError:
            await join_cleanup(acquiring)
            entered = True
            raise
        yield
    finally:
        if entered or acquiring.done() and not acquiring.cancelled() and acquiring.exception() is None:
            await join_cleanup(asyncio.create_task(asyncio.to_thread(guard.__exit__, None, None, None)))


async def credential_bridge(store, connection_id, request, *, interact=None):
    """授权和刷新共用事务，读取锁内最新凭据，不重放已接受提交。"""
    async with credential_guard(store, connection_id):
        await asyncio.to_thread(store.reload)
        connection = next((c for p in store.providers for c in p['connections'] if c['id'] == connection_id), None)
        if connection is None or connection['auth']['type'] != 'oauth':
            raise ValueError(tr('models.config.changed'))
        before = connection['auth'].get('credential')
        if before and before.get('harness_refresh_unknown') and request['operation'] != 'login':
            raise LlmError(tr('models.auth.unknown'), code='llm/provider-auth-unknown')
        request = {**request, 'credential': before}
        async def commit(value):
            nonlocal before
            await asyncio.to_thread(store.credential, connection_id, value, before)
            before = value
        async with aclosing(bridge(request, commit=commit, interact=interact)) as stream:
            async for value in stream:
                yield value


async def bridge(request, *, commit=None, interact=None):
    """一次操作拥有一个进程；原始凭据只通过标准输入传输。"""
    node = os.environ.get('HARNESS_NODE') or shutil.which('node')
    if not node or not (RUNTIME / 'bridge.mjs').is_file():
        raise LlmError(tr('models.runtime.missing'), code='llm/provider-runtime')
    environment = stdio_env({'ELECTRON_RUN_AS_NODE': '1'})
    process = await _start_process(node, str(RUNTIME / 'bridge.mjs'), stop=_stop_stream_process,
        start_timeout=15, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL, env=environment, limit=8 * 1024 * 1024)
    async def send(value):
        process.stdin.write((json.dumps(value, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8'))
        await process.stdin.drain()
    pending_credential = False
    latest_credential = request.get('credential')
    request_started = False
    try:
        await send({'version': 1, **request})
        timeout = request.get('connection', {}).get('timeout_ms', 300000) / 1000
        while True:
            # 启动和授权有自己的等待边界；请求发送后严格监控网络无活动时间。
            read_timeout = timeout if request_started else max(timeout, 900 if request['operation'] == 'login' else 15)
            raw = await asyncio.wait_for(process.stdout.readline(), timeout=read_timeout)
            if not raw:
                break
            value = decode(raw)
            if not isinstance(value, dict) or value.get('type') not in {'credential', 'credential_pending', 'credential_unchanged', 'result', 'auth', 'prompt', 'event', 'failure', 'request', 'activity'}:
                raise ValueError
            if value['type'] == 'request':
                request_started = True
            if value['type'] == 'activity' and (type(value.get('network_bytes')) is not int or value['network_bytes'] < 0):
                raise ValueError
            if value['type'] == 'credential_pending':
                pending_credential = True
                continue
            if value['type'] == 'credential_unchanged':
                pending_credential = False
                continue
            if value['type'] == 'credential':
                if commit is None:
                    raise ValueError
                await commit(value['credential'])
                latest_credential = value['credential']
                pending_credential = False
                await send({'type': 'committed'})
            elif value['type'] == 'prompt':
                if interact is None:
                    raise ValueError
                answer = await interact(value)
                await send({'type': 'answer', 'value': answer})
            elif value['type'] == 'failure':
                failure = _provider_failure(value, request)
                if pending_credential or request['operation'] != 'stream':
                    failure.transient = False
                raise failure
            else:
                yield value
        returncode = await process.wait()
        if returncode != 0:
            raise LlmError(tr('models.runtime.failed'), code='llm/provider-runtime', phase='process', returncode=returncode)
    except TimeoutError as exc:
        if request['operation'] == 'stream' and request_started and not pending_credential:
            raise LlmTimeoutError(tr('python.adapter.9432086171d5'), seconds=timeout,
                                  phase='network', error_type=type(exc).__name__) from exc
        raise LlmError(tr('models.runtime.failed'), code='llm/provider-runtime', phase='bridge',
                       error_type=type(exc).__name__) from exc
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise LlmError(tr('models.runtime.failed'), code='llm/provider-runtime', phase='bridge',
                       error_type=type(exc).__name__) from exc
    finally:
        async def finish():
            nonlocal pending_credential, latest_credential
            async def settle():
                nonlocal pending_credential, latest_credential
                try:
                    await send({'type': 'cancel'})
                except (BrokenPipeError, ConnectionResetError):
                    pass
                while True:
                    raw = await process.stdout.readline()
                    if not raw:
                        break
                    value = decode(raw)
                    if value.get('type') == 'credential_pending':
                        pending_credential = True
                    elif value.get('type') == 'credential_unchanged':
                        pending_credential = False
                    elif value.get('type') == 'credential' and commit:
                        await commit(value['credential'])
                        latest_credential = value['credential']
                        pending_credential = False
                        await send({'type': 'committed'})
                await process.wait()
            try:
                await asyncio.wait_for(settle(), 15)
            except (TimeoutError, OSError, ValueError):
                pass
            finally:
                await _stop_stream_process(process)
                if pending_credential and commit and latest_credential:
                    await commit({**latest_credential, 'harness_refresh_unknown': True})
        await join_cleanup(asyncio.create_task(finish()))


async def provider_stream(adapter, messages, *, config, tools=None, on_attempt=None, on_request=None, cache_session_id=None, zen_compaction=None, capacity_retries=1, max_retries=None, **_):
    """固定模型身份，仅在所选接口内轮换凭据。"""
    store = getattr(adapter, 'provider_connections', None)
    if store is None:
        raise LlmError(tr('models.model.missing'), code='llm/model-missing')
    with store.lock:
        provider, connection, model = store.resolve(config.model)
        operation_revision = store.revision
        info = next(entry for entry in store.catalog() if entry['id'] == config.model)
    caps = {'context_window': info['context_window'] or None, 'max_output_tokens': info['max_output'] or None}
    wire_messages = messages_to_wire(messages)
    assistants = iter(message for message in messages if isinstance(message, AssistantMessage))
    for item in wire_messages:
        if item['role'] == 'assistant':
            item['model'] = next(assistants).model
    tool_errors = {result.tool_use_id: result.is_error for message in messages if isinstance(message, ToolResultMessage) for result in message.results}
    for item in wire_messages:
        if item['role'] == 'tool':
            item['is_error'] = tool_errors.get(item['tool_call_id'], False)
    budget_fields = {'max_tokens', 'max_completion_tokens', 'max_output_tokens'}
    from .capabilities import minimum
    requested = minimum(config.max_tokens, *(config.extra_body.get(name) for name in budget_fields))
    normalized, _adjustments = normalize_request({'max_tokens': requested}, caps, user_context=adapter.context_window, input_tokens=adapter.estimate_context(messages) + adapter.estimate_context(tools or []))
    from dataclasses import replace
    # 三种协议字段统一收敛，扩展正文不能在桥接合并时覆盖预算。
    config = replace(config, max_tokens=normalized.get('max_tokens'),
                     extra_body={name: value for name, value in config.extra_body.items() if name not in budget_fields})
    available = [k for k in connection['keys'] if k['enabled'] and not k['failed']]
    if connection['auth']['type'] == 'api_key' and not available:
        raise LlmError(tr('models.keys.exhausted'), code='llm/key-exhausted')
    if connection['auth']['type'] != 'api_key':
        available = [None]
    delivered = False
    for index, key in enumerate(available):
        lease = (connection['id'], key['id'] if key else '')
        with store.lock:
            store.leases[lease] = store.leases.get(lease, 0) + 1
        trace_id = None
        started = time.monotonic()
        completed = False
        usage = None
        count = 0
        first_event_ms = None
        failure = None
        try:
            if on_attempt:
                await on_attempt({'phase': 'waiting', 'model': model['model'], 'attempt': index + 1, 'max_attempts': len(available), 'api_key_slot': f'{index + 1}/{len(available)}'})
            if provider['type'] == 'kilo-free':
                from dataclasses import replace
                from .kilo_free import BASE_URL, KiloFreeAdapter
                levels = info['reasoning_efforts']
                extra_body = {**config.extra_body}
                if config.reasoning_effort == 'off' and 'off' in levels:
                    extra_body['reasoning'] = {'enabled': False}
                call = replace(config, model=model['model'], extra_headers={**connection['headers'], **config.extra_headers},
                               extra_body=extra_body, reasoning_effort=None if config.reasoning_effort == 'off' else config.reasoning_effort)
                worker = KiloFreeAdapter('anonymous', base_url=BASE_URL, model=model['model'],
                    transport=adapter.transport if adapter._transport_injected else None,
                    max_retries=connection['max_retries'], network_timeout=adapter.network_timeout,
                    context_window=adapter.context_window, prompt_cache_mode=adapter.prompt_cache_mode,
                    model_info={**caps, 'supports_thinking': bool(levels), 'supported_efforts': levels})
                async with aclosing(worker.stream(messages, config=call, tools=tools, on_attempt=on_attempt,
                        on_request=on_request, cache_session_id=cache_session_id, capacity_retries=capacity_retries,
                        max_retries=max_retries)) as stream:
                    async for event in stream:
                        yield event
                return
            if provider['type'] == 'opencode-local':
                from .opencode_local import BASE_URL
                if connection['base_url'] != BASE_URL or connection['auth']['type'] != 'opencode_local':
                    raise LlmError(tr('llm.opencode_local.scope_invalid'), code='llm/opencode-local-scope')
            if provider['type'] in {'opencode-local', 'opencode-zen', 'opencode'} and connection['base_url'] in {'https://opencode.ai/inference/openai/v1', 'https://opencode.ai/zen/v1'}:
                from dataclasses import replace
                from .adapter import OpenRouterAdapter
                transport = adapter.transport if adapter._transport_injected else None
                if provider['type'] == 'opencode-local':
                    from .opencode_local import configured_transport
                    if transport is None:
                        transport = configured_transport(required=True)
                worker = OpenRouterAdapter(key['value'] if key else 'public', base_url=connection['base_url'], model=model['model'],
                    transport=transport, max_retries=connection['max_retries'] if len(available) == 1 else 0,
                    capabilities=adapter.capabilities, model_info=model, network_timeout=adapter.network_timeout,
                    context_window=adapter.context_window, prompt_cache_mode=adapter.prompt_cache_mode)
                async with aclosing(worker.stream(messages, config=replace(config, model=model['model']), tools=tools,
                        on_attempt=on_attempt, on_request=on_request, cache_session_id=cache_session_id, zen_compaction=zen_compaction, capacity_retries=capacity_retries, max_retries=max_retries)) as stream:
                    async for event in stream:
                        delivered = delivered or event.kind in {'text', 'thinking', 'tool_use', 'signature', 'reasoning_details'}
                        yield event
                return
            credential = connection['auth'].get('credential') or {'type': 'api_key', 'env': connection['auth']['env'], **({'key': key['value']} if key else {})}
            async def commit(value):
                await asyncio.to_thread(store.credential, connection['id'], value)
            request = {'operation': 'stream', 'provider': provider['type'], 'connection': {**connection, 'timeout_ms': int(adapter.network_timeout * 1000), **({'max_retries': max_retries} if max_retries is not None else {})}, 'credential': credential,
                'model': model, 'messages': to_jsonable(wire_messages),
                'tools': [to_jsonable(t.to_dict()) for t in tools or ()], 'config': to_jsonable(config.to_dict()), 'session_id': cache_session_id}
            operation = credential_bridge(store, connection['id'], request) if connection['auth']['type'] == 'oauth' else bridge(request, commit=commit)
            async with aclosing(operation) as stream:
                async for value in stream:
                    if value['type'] == 'request' and on_request:
                        trace_id = await on_request({'phase': 'started', 'body': json.dumps(value['body'], ensure_ascii=False),
                            'url': value['base_url'], 'estimated_input_tokens': None})
                    if value['type'] == 'activity' and on_attempt:
                        await on_attempt({'network_bytes': value['network_bytes'], 'network_idle_ms': 0})
                    if value['type'] != 'event':
                        continue
                    raw = value['event']
                    if raw.get('kind') == 'usage':
                        raw['usage'] = TokenUsage(**raw['usage'])
                        usage = raw['usage'].to_dict()
                    if raw.get('kind') == 'tool_use':
                        arguments_error = raw.get('tool_use', {}).pop('arguments_error', None)
                        if not isinstance(raw.get('tool_use', {}).get('input'), dict):
                            raise LlmError(tr('models.runtime.failed'), code='llm/provider-tool-input')
                        if arguments_error is not None:
                            if not isinstance(arguments_error, str) or not arguments_error:
                                raise LlmError(tr('models.runtime.failed'), code='llm/provider-tool-input')
                            # 保留第三方解析诊断及收到的原文；工具参数校验会拒绝执行字符串。
                            raw['tool_use']['input'] = arguments_error
                        raw['tool_use'] = ToolUseBlock(**raw['tool_use'])
                    event = StreamEvent(**raw)
                    if first_event_ms is None:
                        first_event_ms = round((time.monotonic() - started) * 1000)
                    count += 1
                    delivered = delivered or event.kind in {'text', 'thinking', 'tool_use', 'signature', 'reasoning_details'}
                    if event.kind == 'done':
                        completed = True
                    yield event
            if not completed:
                raise LlmError(tr('models.runtime.failed'), code='llm/provider-stream')
            return
        except LlmError as exc:
            failure = exc
            status = exc.details.get('status', 0)
            if key and status in {401, 402, 403}:
                await asyncio.to_thread(store.fail_key, connection, key, operation_revision)
                operation_revision = store.revision
            if not delivered and key and status in {401, 402, 403} and index == len(available) - 1:
                raise LlmError(tr('models.keys.exhausted'), code='llm/key-exhausted') from exc
            if delivered or not key or status not in {401, 402, 403, 429} and not exc.transient or index == len(available) - 1:
                raise
        except GeneratorExit:
            raise
        except BaseException as exc:
            failure = exc
            raise
        finally:
            with store.lock:
                store.leases[lease] -= 1
                if not store.leases[lease]:
                    del store.leases[lease]
            if trace_id is not None:
                await on_request({'phase': 'completed' if completed else 'interrupted', 'trace_id': trace_id, 'usage': usage,
                    'observation': {'duration_ms': round((time.monotonic() - started) * 1000), 'event_count': count,
                        'first_event_ms': first_event_ms, 'terminal_reason': 'done' if completed else
                            'cancelled' if isinstance(failure, asyncio.CancelledError) else 'provider_error',
                        'error_type': type(failure).__name__ if failure else None,
                        'error_code': getattr(failure, 'code', None),
                        'error_details': dict(getattr(failure, 'details', {})), 'text_diagnostic': None}})


async def subscription_stream(adapter, messages, *, config, tools, on_attempt, on_request, cache_session_id):
    """只在支持当前模型的订阅账号间切换；已交付内容不重放。"""
    from dataclasses import replace
    from .adapter import OpenRouterAdapter
    from .capabilities import CapabilityRegistry
    from .transport import UrllibTransport
    from ..codex_gateway.quota import QuotaError
    pool = adapter.subscription_pool
    attempted = set()
    while True:
        delivered = False
        try:
            async with pool.acquire(config.model.removeprefix('chatgpt/'), exclude=attempted, harness=True) as (slot, epoch):
                attempted.add(slot.id)
                info = slot.account.resolve(config.model)
                worker = OpenRouterAdapter('chatgpt-subscription', base_url='https://chatgpt.com/backend-api/codex', model=info['id'],
                    transport=adapter.transport if adapter._transport_injected else UrllibTransport(max_body_bytes=None, prefer_ipv4=False),
                    max_retries=0, capabilities=CapabilityRegistry(discover=False),
                    model_info={'context_window': info.get('context_window'), 'max_output_tokens': info.get('max_output'), 'supports_thinking': info['supports_reasoning']},
                    context_window=adapter.context_window, network_timeout=adapter.network_timeout)
                worker._chatgpt_endpoint = slot.account
                worker._chatgpt_epoch = epoch
                try:
                    async with aclosing(worker.stream(messages, config=replace(config, provider='chatgpt'), tools=tools,
                            on_attempt=on_attempt, on_request=on_request, cache_session_id=cache_session_id)) as stream:
                        async for event in stream:
                            delivered = delivered or event.kind in {'text', 'thinking', 'signature', 'tool_use', 'reasoning_details'}
                            yield event
                    return
                except LlmError as error:
                    status = error.details.get('status', 0)
                    if status in {401, 403, 429}:
                        pool.rejected(slot, epoch, status)
                    if delivered or not pool.auto_rotate or status not in {401, 403, 429}:
                        raise
        except QuotaError as error:
            raise LlmError(str(error), code='llm/chatgpt-account') from None
