"""订阅网关账号管理接口，页面只接收公开状态，不返回令牌。"""
from __future__ import annotations

import asyncio
from typing import Any

from ...codex_gateway.quota import QuotaError
from ...i18n import tr
from ..errors import BadRequest
from ..responses import json_response
from ..router import Router


def build_router(ctx: Any) -> Router:
    router = Router()
    pool = getattr(ctx.harness, 'codex_gateway', None)

    def require_pool():
        if pool is None:
            raise BadRequest('Codex gateway is unavailable', code='codex-gateway/unavailable')
        return pool

    def run(operation):
        async def task():
            try:
                return await asyncio.to_thread(operation)
            except (QuotaError, ValueError, OSError) as exc:
                raise BadRequest(str(exc), code='codex-gateway/operation') from None
        return json_response(ctx.call(task(), timeout=65))

    @router.get('/api/v1/codex-gateway')
    def status(request):
        return json_response(ctx.call(require_pool().refresh(force=True), timeout=65))

    @router.patch('/api/v1/codex-gateway')
    def configure(request):
        body = request.json()
        if set(body) != {'enabled'} or type(body['enabled']) is not bool:
            raise BadRequest('Invalid gateway state', code='codex-gateway/invalid')
        return run(lambda: require_pool().configure(body['enabled']))

    @router.patch('/api/v1/codex-gateway/selection')
    def select(request):
        body = request.json()
        if not isinstance(body, dict) or set(body) != {'current_account', 'auto_rotate'}:
            raise BadRequest(tr('models.config.invalid'), code='codex-gateway/invalid')
        return run(lambda: require_pool().select(body['current_account'], body['auto_rotate']))

    @router.get('/api/v1/codex-gateway/desktop')
    def desktop_status(request):
        return json_response(ctx.harness.codex_desktop.status())

    @router.patch('/api/v1/codex-gateway/desktop')
    def desktop_configure(request):
        body = request.json()
        if set(body) != {'enabled'} or type(body['enabled']) is not bool:
            raise BadRequest('Invalid desktop gateway state', code='codex-gateway/invalid')
        return run(lambda: ctx.harness.codex_desktop.set_enabled(body['enabled']))

    @router.post('/api/v1/codex-gateway/accounts')
    def create(request):
        return run(lambda: {'account': require_pool().create(), **require_pool().status()})

    @router.post('/api/v1/codex-gateway/accounts/{account_id}/login')
    def login(request, account_id):
        return run(lambda: require_pool().login(account_id))

    @router.post('/api/v1/codex-gateway/accounts/{account_id}/cancel')
    def cancel(request, account_id):
        return run(lambda: require_pool().cancel_login(account_id))

    @router.patch('/api/v1/codex-gateway/accounts/{account_id}')
    def update(request, account_id):
        body = request.json()
        if set(body) != {'enabled'} or type(body['enabled']) is not bool:
            raise BadRequest('Invalid account state', code='codex-gateway/invalid')
        return run(lambda: require_pool().set_enabled(account_id, body['enabled']))

    @router.delete('/api/v1/codex-gateway/accounts/{account_id}')
    def remove(request, account_id):
        return run(lambda: require_pool().remove(account_id))

    return router
