"""原生 Responses 字节流转发；不介入桌面端的工具执行循环。"""
from __future__ import annotations

import asyncio
import copy
import json
import os
import threading
import time
from contextlib import aclosing

from ..core.cleanup import join_cleanup, raise_cleanup_errors
from ..core.errors import LlmError
from ..llm.chatgpt_auth import BASE_URL
from ..llm.transport import SseParser, UrllibTransport
from .quota import QuotaError


def validate_request(body, *, compact=False):
    """只校验网关依赖的原生字段，不改写原始历史、工具和加密状态。"""
    if (not isinstance(body, dict) or not isinstance(body.get('model'), str)
            or not body['model'] or len(body['model']) > 256
            or not isinstance(body.get('input'), (str, list))
            or body.get('previous_response_id') is not None
            or body.get('conversation') is not None
            or body.get('background', False) is not False
            or body.get('store', False) is not False
            or not compact and body.get('stream') is not True):
        raise QuotaError('ui.codexGateway.invalidRequest')
    try:
        result = copy.deepcopy(body)
        # 订阅端只接受不存储的请求；不为跨账号续跑依赖服务端状态。
        if not compact:
            result['store'] = False
        raw = json.dumps(result, ensure_ascii=False, allow_nan=False).encode('utf-8')
    except (ValueError, UnicodeError, RecursionError):
        raise QuotaError('ui.codexGateway.invalidRequest') from None
    if len(raw) > 32 * 1024 * 1024:
        raise QuotaError('ui.codexGateway.requestLarge')
    return raw


class ResponsesProxy:
    """每个请求保留独立账号租约；已交付或结果未知时不自动重放。"""

    def __init__(self, pool, *, transport=None, heartbeat=15):
        self.pool = pool
        self.transport = transport or UrllibTransport(max_body_bytes=64 * 1024 * 1024)
        self.heartbeat = heartbeat
        self.log_path = pool.home / 'codex-gateway-requests.jsonl'
        self.log_lock = threading.Lock()
        self.audit_enabled = os.environ.get('HARNESS_GATEWAY_AUDIT') == '1'

    def _audit(self, event, **values):
        """把已获准请求写入本地日志，不写入上游授权头。"""
        if not self.audit_enabled:
            return
        record = {'time': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'event': event, **values}
        raw = json.dumps(record, ensure_ascii=False, separators=(',', ':'))
        if len(raw.encode('utf-8')) > 8 * 1024 * 1024:
            record['truncated'] = True
            record['body'] = '[request body omitted: audit record exceeded 8 MiB]'
            raw = json.dumps(record, ensure_ascii=False, separators=(',', ':'))
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_lock, self.log_path.open('a', encoding='utf-8') as stream:
            stream.write(raw + '\n')
            stream.flush()

    async def _forward(self, body, raw, *, compact=False):
        body = copy.deepcopy(body)
        body['model'] = body['model'].removeprefix('chatgpt/')
        raw = validate_request(body, compact=compact)
        while True:
            terminal_chunk = None
            received = False
            retry = False
            async with self.pool.acquire(body['model']) as (slot, epoch):
                self._audit('accepted', account_id=slot.id, model=body['model'], body=body)
                headers = await asyncio.to_thread(slot.account.headers, epoch)
                headers.update({'Content-Type': 'application/json',
                                'Accept': 'application/json' if compact else 'text/event-stream'})
                parser = SseParser()
                terminal = False
                endpoint = '/responses/compact' if compact else '/responses'
                stream = self.transport.post_stream(BASE_URL + endpoint, headers=headers, body=raw, timeout=300)
                try:
                    async with aclosing(stream):
                        async for chunk in stream:
                            received = True
                            if not compact:
                                for data in parser.feed(chunk):
                                    if data.strip() == '[DONE]':
                                        continue
                                    try:
                                        event = json.loads(data)
                                    except ValueError:
                                        raise QuotaError('ui.codexGateway.streamInvalid') from None
                                    if not isinstance(event, dict):
                                        raise QuotaError('ui.codexGateway.streamInvalid')
                                    terminal |= event.get('type') in {'response.completed', 'response.incomplete', 'response.failed', 'error'}
                            if terminal:
                                # 收尾确认后再交付终态，避免关闭失败被成功掩盖。
                                terminal_chunk = chunk
                                break
                            yield chunk
                except LlmError as exc:
                    status = exc.details.get('status')
                    if received or status not in (401, 429):
                        raise
                    # HTTP 明确拒绝且未读取任何成功响应字节，才允许重新准入。
                    self.pool.rejected(slot, epoch, status, exc.details.get('retry_after'))
                    retry = True
                if not compact and not retry:
                    parser.finish()
                    if not terminal:
                        raise QuotaError('ui.codexGateway.streamIncomplete')
            if retry:
                continue
            if terminal_chunk is not None:
                self._audit('terminal', model=body['model'])
                yield terminal_chunk
            return

    async def stream(self, body):
        raw = validate_request(body)
        source = self._forward(copy.deepcopy(body), raw)
        pending = None
        delivered = False
        errors = []
        try:
            while True:
                if pending is None:
                    pending = asyncio.create_task(anext(source))
                done, _ = await asyncio.wait((pending,), timeout=self.heartbeat)
                if not done:
                    # 注释不冒充模型进度或完成事件，客户端超时仍须独立验收。
                    yield b': harness-wait\n\n'
                    if self.pool.enabled:
                        await self.pool.refresh()
                    continue
                finished, pending = pending, None
                try:
                    chunk = finished.result()
                except StopAsyncIteration:
                    break
                delivered = True
                yield chunk
        except LlmError as exc:
            # 已明确收到 HTTP 拒绝也不在此处盲目重放；额度回查和下一请求重新准入。
            error = QuotaError('ui.codexGateway.upstreamPartial' if delivered else 'ui.codexGateway.upstreamError')
            error.__cause__ = exc
            errors.append(error)
        except BaseException as exc:
            errors.append(exc)
        finally:
            if pending is not None:
                if not pending.done():
                    pending.cancel()
                try:
                    result = await join_cleanup(asyncio.gather(pending, return_exceptions=True))
                    errors.extend(error for error in result if isinstance(error, BaseException)
                                  and not isinstance(error, (asyncio.CancelledError, StopAsyncIteration)))
                except BaseException as exc:
                    errors.append(exc)
            try:
                await join_cleanup(asyncio.create_task(source.aclose()))
            except BaseException as exc:
                errors.append(exc)
        raise_cleanup_errors(errors)

    async def compact(self, body):
        raw = validate_request(body, compact=True)
        content = bytearray()
        source = self._forward(copy.deepcopy(body), raw, compact=True)
        async with aclosing(source):
            async for chunk in source:
                content.extend(chunk)
                if len(content) > 32 * 1024 * 1024:
                    raise QuotaError('ui.codexGateway.responseLarge')
        try:
            result = json.loads(content.decode('utf-8'))
            if not isinstance(result, dict) or not isinstance(result.get('output'), list):
                raise ValueError()
            return result
        except (ValueError, UnicodeError):
            raise QuotaError('ui.codexGateway.streamInvalid') from None
