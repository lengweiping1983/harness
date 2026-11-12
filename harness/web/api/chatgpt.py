"""ChatGPT 订阅账号入口，HTTP 线程通过 WebContext 调度。"""
from __future__ import annotations

import asyncio

from ...i18n import tr

from ..errors import BadRequest
from ..responses import json_response
from ..router import Router


def build_router(ctx):
    router = Router()

    def account():
        return ctx.harness.chatgpt

    def execute(operation):
        async def run():
            try:
                return await asyncio.to_thread(operation)
            except (ValueError, OSError) as exc:
                message = str(exc) if isinstance(exc, ValueError) else tr('chatgpt.state_save_failed')
                raise BadRequest(message, code='chatgpt/failed') from None
        return json_response(ctx.call(run(), timeout=60))

    @router.get('/api/v1/chatgpt')
    def status(request):
        return json_response(account().status())

    @router.post('/api/v1/chatgpt/login')
    def login(request):
        return execute(account().login)

    @router.post('/api/v1/chatgpt/cancel')
    def cancel(request):
        def stop():
            account().cancel_login()
            return account().status()
        return execute(stop)

    @router.post('/api/v1/chatgpt/logout')
    def logout(request):
        return execute(account().logout)

    @router.post('/api/v1/chatgpt/models')
    def models(request):
        return execute(account().refresh_models)

    return router
