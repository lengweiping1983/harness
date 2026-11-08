"""模型连接、密钥可用性与路由状态接口。"""
from __future__ import annotations

from typing import Any
from harness.i18n import tr
from ..router import Router
from ..responses import json_response
from ..errors import BadRequest, ServiceUnavailable


def build_router(ctx: Any) -> Router:
    """连接配置独立装配，保持当前公开地址与原子更新语义。"""
    if hasattr(getattr(ctx, 'harness', None), 'provider_connections'):
        from .provider_connections import build_router as provider_router
        return provider_router(ctx)
    router = Router()
    from . import chatgpt
    router.include(chatgpt.build_router(ctx))
    @router.patch("/api/v1/connection")
    def connection(request):
        from ...llm.routes import ModelRoutes, load_models
        body = request.json()
        if not isinstance(body, dict) or set(body) != {'models'} or not isinstance(body['models'], list) or not body['models']:
            raise BadRequest(tr('python.v1.68d34499de66'), code='connection/invalid')
        async def update():
            config = ctx.harness.config
            pool = ctx.harness.adapter.route_pool
            running = bool(ctx.harness.runtime.active)
            if running and pool is None:
                raise BadRequest(tr('python.v1.b95f8278ac5f'), code='connection/busy')
            models = body['models']
            try:
                if pool is None:
                    existing = load_models(config.home, config.model, config.base_url,
                        config.api_keys or ([config.api_key] if config.api_key else []))
                    pool = ModelRoutes(existing or models, config.home)
                pool.update(models, keys_only=running)
            except ValueError as exc:
                raise BadRequest(str(exc), code='connection/invalid') from exc
            except OSError as exc:
                raise ServiceUnavailable(tr('python.v1.b331480a0f8d'), code='connection/persist-failed') from exc
            first = next(m for m in pool.models if m['enabled'])
            pool.capabilities = ctx.harness.adapter.capabilities
            config.model_routes = pool.models
            config.model, config.base_url, config.api_key = first['model'], first['base_url'], first['keys'][0]['value']
            config.api_keys = [k['value'] for k in first['keys']]
            ctx.harness.adapter.route_pool = pool
            ctx.harness.adapter.model, ctx.harness.adapter.base_url, ctx.harness.adapter.api_key = config.model, config.base_url, config.api_key
            ctx.harness.adapter.key_pool = None
            ctx.harness.key_pool = None
            return {"config": config.to_dict(), 'routing': pool.snapshot()}
        return json_response(ctx.call(update()))

    @router.get('/api/v1/connection')
    def connection_status(request):
        account = getattr(ctx.harness, 'chatgpt', None)
        if account is not None and account.logged_in and request.get('source') != 'api':
            return json_response({'models': [], 'current': None, 'cycle': 1})
        pool = ctx.harness.adapter.route_pool
        if pool:
            async def read():
                registry = ctx.harness.adapter.capabilities
                if registry.discover:
                    import asyncio
                    from ...llm.capabilities import endpoint_key
                    models = {endpoint_key(model['base_url'], model['model']): model for model in pool.models}
                    # 状态请求拥有发现任务；失败与取消都先收尾，再返回调用方。
                    async with asyncio.TaskGroup() as tasks:
                        for model in models.values():
                            tasks.create_task(registry.resolve(model['base_url'], model['model'], model))
                return pool.snapshot()
            return json_response(ctx.call(read()))
        from ...llm.routes import ModelRoutes, load_models
        config = ctx.harness.config
        models = load_models(config.home, config.model, config.base_url, config.api_keys or ([config.api_key] if config.api_key else []))
        if models:
            for model in models:
                if not (config.home / 'connection-secrets.json').exists():
                    model.setdefault('context_window', config.model_context_window)
                    model.setdefault('max_output_tokens', config.model_max_output_tokens)
            pool = ModelRoutes(models)
            pool.capabilities = ctx.harness.adapter.capabilities
            return json_response(pool.snapshot())
        return json_response({'models': [], 'current': None, 'cycle': 1})

    @router.patch('/api/v1/connection/status')
    def connection_key_status(request):
        body = request.json()
        if not isinstance(body, dict) or set(body) != {'model_id', 'key_id', 'available'} or type(body['available']) is not bool:
            raise BadRequest(tr('python.v1.1656b864e38e'), code='connection/invalid')
        async def update():
            pool = ctx.harness.adapter.route_pool
            if pool is None:
                raise BadRequest(tr('python.v1.b2eb85045ff0'), code='connection/invalid')
            try:
                pool.set_available(body['model_id'], body['key_id'], body['available'])
            except ValueError as exc:
                raise BadRequest(str(exc), code='connection/invalid') from exc
            return pool.snapshot()
        return json_response(ctx.call(update()))

    @router.post('/api/v1/connection/reset')
    def connection_reset(request):
        async def update():
            pool = ctx.harness.adapter.route_pool
            if pool is None:
                raise BadRequest(tr('python.v1.b2eb85045ff0'), code='connection/invalid')
            pool.reset()
            return pool.snapshot()
        return json_response(ctx.call(update()))

    return router