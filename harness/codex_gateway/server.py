"""隔离验收用的本机 Responses 服务；不加载 Harness 对话或执行工具。"""
from __future__ import annotations

import ipaddress
import json
from contextlib import aclosing, asynccontextmanager

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route
from starlette.middleware import Middleware

from ..llm.chatgpt_auth import BASE_URL, CLIENT_VERSION, ChatGPTAccountError
from ..core.errors import LlmError
from ..core.language import current_language
from .proxy import ResponsesProxy, validate_request
from .quota import QuotaError
from .protocol import PREFIX, decode_object


MAX_BODY = 32 * 1024 * 1024


class GatewayLanguage:
    """语言覆盖整个 ASGI 响应生命周期，包含之后消费的 SSE 生成器。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        language = current_language.get()
        if scope['type'] == 'http':
            header = dict(scope.get('headers', [])).get(b'accept-language', b'').decode('latin-1')
            for candidate in header.split(','):
                tag = candidate.split(';', 1)[0].strip().lower()
                if tag == 'en' or tag.startswith('en-'):
                    language = 'en'
                    break
                if tag == 'zh' or tag.startswith('zh-'):
                    language = 'zh-CN'
                    break
        with current_language.set(language):
            await self.app(scope, receive, send)


def create_app(pool, *, proxy=None):
    """单独的 ASGI 监听用于先验收链路，不更改现有 Web 服务的响应格式。"""
    proxy = proxy or ResponsesProxy(pool)

    def authorize(request):
        try:
            local = request.client is not None and ipaddress.ip_address(request.client.host).is_loopback
        except ValueError:
            local = False
        origin = request.headers.get('origin')
        if not local or origin is not None and origin != str(request.base_url).rstrip('/'):
            return JSONResponse({'error': {'code': 'gateway/local-only',
                                           'message': str(QuotaError('ui.codexGateway.localOnly'))}}, status_code=403)
        if not pool.authenticate(request.headers.get('authorization', '')):
            return JSONResponse({'error': {'code': 'gateway/unauthorized',
                                           'message': str(QuotaError('ui.codexGateway.unauthorized'))}}, status_code=401)
        return None

    async def body(request):
        content = bytearray()
        async for chunk in request.stream():
            if len(content) + len(chunk) > MAX_BODY:
                raise QuotaError('ui.codexGateway.requestLarge')
            content.extend(chunk)
        try:
            return decode_object(content)
        except (ValueError, UnicodeError, RecursionError):
            raise QuotaError('ui.codexGateway.invalidRequest') from None

    async def responses(request: Request):
        failure = authorize(request)
        if failure is not None:
            return failure
        compact = request.url.path.endswith('/compact')
        try:
            document = await body(request)
            validate_request(document, compact=compact)
        except QuotaError as exc:
            return JSONResponse({'error': {'code': 'gateway/request', 'message': str(exc)}},
                                status_code=413 if exc.key == 'ui.codexGateway.requestLarge' else 400)
        if compact:
            try:
                return JSONResponse(await proxy.compact(document))
            except (QuotaError, ChatGPTAccountError, LlmError):
                return JSONResponse({'error': {'code': 'gateway/compact',
                                               'message': str(QuotaError('ui.codexGateway.upstreamError'))}}, status_code=502)

        async def stream():
            try:
                async with aclosing(proxy.stream(document)) as source:
                    async for chunk in source:
                        yield chunk
            except (QuotaError, ChatGPTAccountError, LlmError, OSError) as exc:
                # 连接已开始，不能改写 HTTP 状态或发送第二份 HTTP 响应。
                payload = {'type': 'error', 'error': {'code': 'gateway/request-failed', 'message': str(exc)}}
                yield ('event: error\ndata: ' + json.dumps(payload, ensure_ascii=False) + '\n\n').encode('utf-8')

        return StreamingResponse(stream(), media_type='text/event-stream',
                                 headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})

    async def models(request: Request):
        failure = authorize(request)
        if failure is not None:
            return failure
        with pool.lock:
            slot = next((slot for slot, duplicate in pool._unique()
                         if slot.enabled and slot.account.logged_in and not duplicate), None)
            epoch = slot.account.epoch if slot is not None else None
        if slot is None:
            return JSONResponse({'error': {'code': 'gateway/no-account',
                                           'message': str(QuotaError('ui.codexGateway.noAccount'))}}, status_code=503)
        import asyncio
        try:
            def read():
                headers = slot.account.headers(epoch)
                return slot.account.request(BASE_URL + '/models?client_version=' + CLIENT_VERSION, headers=headers)
            from ..core.cleanup import join_cleanup
            return JSONResponse(await join_cleanup(asyncio.create_task(asyncio.to_thread(read))))
        except (OSError, ChatGPTAccountError):
            return JSONResponse({'error': {'code': 'gateway/models',
                                           'message': str(QuotaError('ui.codexGateway.upstreamError'))}}, status_code=502)

    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            await pool.close()

    return Starlette(middleware=[Middleware(GatewayLanguage)],
                     routes=[Route(PREFIX + '/responses', responses, methods=['POST']),
                             Route(PREFIX + '/responses/compact', responses, methods=['POST']),
                             Route(PREFIX + '/models', models, methods=['GET'])], lifespan=lifespan)
