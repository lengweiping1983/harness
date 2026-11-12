"""聚合插件管理 API；模型工具不暴露安装入口。"""
from __future__ import annotations

from harness.i18n import tr
import logging
from typing import Any

from ...core.errors import HarnessError
from ...plugins.packages import PackageConflict
from ..errors import BadRequest, Conflict, NotFound
from ..responses import json_response
from ..router import Router

logger = logging.getLogger(__name__)


def build_router(ctx: Any) -> Router:
    """HTTP 写请求统一调度到插件业务服务，读取反映实际后端状态。"""
    router = Router()
    service = ctx.harness.plugin_service

    def source(request: Any) -> str:
        body = request.json()
        if not isinstance(body, dict) or set(body) - {'path', 'digest'} or not isinstance(body.get('path'), str) or not body['path'].strip() or len(body['path']) > 4096:
            raise BadRequest(tr('python.web.validation.pluginPath'), code='plugin-package/bad-body')
        return body['path']

    def call(coroutine: Any) -> Any:
        try:
            return ctx.call(coroutine, timeout=120)
        except FileExistsError as exc:
            raise Conflict(str(exc), code='plugin-package/exists') from exc
        except KeyError as exc:
            raise NotFound(str(exc), code='plugin-package/not-found') from exc
        except PackageConflict as exc:
            raise Conflict(exc.message, code='plugin-package/changed', details=exc.details or None) from exc
        except HarnessError as exc:
            raise BadRequest(exc.message, code='plugin-package/invalid', details=exc.details or None) from exc
        except OSError as exc:
            key = 'ui.collaboration.error.plugin-package/io-error'
            logger.warning(tr(key), exc_info=True)
            raise BadRequest(tr(key), code='plugin-package/io-error',
                             details={'copy': {'key': key}, 'errno': exc.errno}) from exc

    @router.get('/api/v1/plugin-packages')
    def catalog(request: Any) -> Any:
        try:
            return json_response(service.catalog())
        except HarnessError as exc:
            raise BadRequest(exc.message, code='plugin-package/invalid', details=exc.details or None) from exc

    @router.get('/api/v1/plugin-marketplaces')
    def marketplaces(request: Any) -> Any:
        return json_response(call(service.marketplace_catalog()))

    @router.post('/api/v1/plugin-marketplaces')
    def register_marketplace(request: Any) -> Any:
        if not isinstance(request.json(), dict) or set(request.json()) != {'path'}:
            raise BadRequest(tr('python.web.validation.marketplacePath'), code='plugin-package/bad-body')
        return json_response(call(service.register_marketplace(source(request))))

    @router.delete('/api/v1/plugin-marketplaces')
    def unregister_marketplace(request: Any) -> Any:
        if not isinstance(request.json(), dict) or set(request.json()) != {'path'}:
            raise BadRequest(tr('python.web.validation.marketplacePath'), code='plugin-package/bad-body')
        return json_response(call(service.unregister_marketplace(source(request))))

    @router.post('/api/v1/plugin-marketplaces/preview')
    def preview_marketplace(request: Any) -> Any:
        import re
        body = request.json()
        if not isinstance(body, dict) or set(body) != {'path', 'name', 'fingerprint'} or any(not isinstance(body[key], str) or not body[key].strip() or len(body[key]) > 4096 for key in ('path', 'name')) or not isinstance(body['fingerprint'], str) or not re.fullmatch('[a-f0-9]{64}', body['fingerprint']):
            raise BadRequest(tr('python.web.validation.marketplaceReview'), code='plugin-package/bad-body')
        return json_response(call(service.preview_marketplace(body['path'], body['name'], body['fingerprint'])))

    @router.post('/api/v1/plugin-packages/preview')
    def preview(request: Any) -> Any:
        return json_response(call(service.preview(source(request))))

    def source_query(request: Any, *, file: bool = False) -> tuple[str, str]:
        path, name = request.get('path'), request.get('file', '')
        if not path or not path.strip() or len(path) > 4096 or file and (not name or len(name) > 4096):
            raise BadRequest(tr('python.web.validation.sourceFile'), code='plugin-package/bad-body')
        return path, name

    @router.get('/api/v1/plugin-packages/source')
    def source_files(request: Any) -> Any:
        path, _ = source_query(request)
        return json_response(call(service.source_files(path)))

    @router.get('/api/v1/plugin-packages/source/file')
    def source_file(request: Any) -> Any:
        path, name = source_query(request, file=True)
        return json_response(call(service.source_file(path, name)))

    def source_body(request: Any, *, save: bool = False) -> dict[str, Any]:
        import re
        body = request.json()
        keys = {'path', 'file', 'revision'} | ({'content', 'executable'} if save else set())
        if not isinstance(body, dict) or set(body) != keys or any(not isinstance(body[key], str) or not body[key].strip() or len(body[key]) > 4096 for key in ('path', 'file')) or not isinstance(body['revision'], str) or not re.fullmatch(r'(?:[a-f0-9]{64}|missing)', body['revision']):
            raise BadRequest(tr('python.web.validation.sourceRevision'), code='plugin-package/bad-body')
        if save and (not isinstance(body['content'], str) or len(body['content']) > 1024 * 1024 or type(body['executable']) is not bool):
            raise BadRequest(tr('python.web.validation.sourceContent'), code='plugin-package/bad-body')
        return body

    @router.patch('/api/v1/plugin-packages/source/file')
    def save_source(request: Any) -> Any:
        body = source_body(request, save=True)
        return json_response(call(service.save_source(body['path'], body['file'], body['content'], body['revision'], body['executable'])))

    @router.delete('/api/v1/plugin-packages/source/file')
    def remove_source(request: Any) -> Any:
        body = source_body(request)
        return json_response(call(service.remove_source(body['path'], body['file'], body['revision'])))

    @router.post('/api/v1/plugin-packages/create')
    def create(request: Any) -> Any:
        body = request.json()
        limits = {'path': 4096, 'name': 256, 'description': 4096, 'instructions': 64000}
        if not isinstance(body, dict) or set(body) != set(limits) or any(
            not isinstance(body[key], str) or not body[key].strip() or len(body[key]) > limit
            for key, limit in limits.items()
        ):
            raise BadRequest(tr('python.web.validation.sourceNew'), code='plugin-package/bad-body')
        return json_response(call(service.create_draft(body['path'], body['name'], body['description'], body['instructions'])))

    @router.get('/api/v1/plugin-packages/draft')
    def draft(request: Any) -> Any:
        parent, name = request.get('path'), request.get('name')
        if not parent or not parent.strip() or not name or not name.strip() or len(parent) > 4096 or len(name) > 256:
            raise BadRequest(tr('python.web.validation.sourceParent'), code='plugin-package/bad-body')
        return json_response(call(service.read_draft(parent, name)))

    @router.post('/api/v1/plugin-packages/install')
    def install(request: Any) -> Any:
        path = source(request)
        digest = request.json().get('digest')
        import re
        if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
            raise BadRequest(tr('python.web.validation.previewInstall'), code='plugin-package/preview-required')
        return json_response(call(service.install(path, digest)))

    @router.post('/api/v1/plugin-packages/{name}/enabled')
    def enabled(request: Any, name: str) -> Any:
        body = request.json()
        if not isinstance(body, dict) or set(body) != {'enabled'} or type(body['enabled']) is not bool:
            raise BadRequest(tr('python.web.validation.pluginEnabled'), code='plugin-package/bad-body')
        return json_response(call(service.set_enabled(name, body['enabled'])))

    @router.post('/api/v1/plugin-packages/{name}/update')
    def update(request: Any, name: str) -> Any:
        import re
        body = request.json()
        if not isinstance(body, dict) or set(body) != {'path', 'digest', 'current_digest'} or not isinstance(body['path'], str) or not body['path'].strip() or len(body['path']) > 4096 or any(
            not isinstance(body[key], str) or not re.fullmatch('[0-9a-f]{64}', body[key]) for key in ('digest', 'current_digest')
        ):
            raise BadRequest(tr('python.web.validation.previewUpdate'), code='plugin-package/preview-required')
        return json_response(call(service.update(name, body['path'], body['digest'], body['current_digest'])))

    @router.post('/api/v1/plugin-packages/{name}/hooks/trust')
    def hook_trust(request: Any, name: str) -> Any:
        from ...app.hooks import HookConflict
        body = request.json()
        if not isinstance(body, dict) or set(body) != {'fingerprint', 'trusted'} or not isinstance(body['fingerprint'], str) or type(body['trusted']) is not bool:
            raise BadRequest(tr('python.web.validation.sourceTrust'), code='hooks/bad-body')
        try:
            return json_response(call(service.set_hook_trust(name, body['fingerprint'], body['trusted'])))
        except HookConflict as exc:
            raise Conflict(str(exc), code='hooks/changed') from exc

    @router.post('/api/v1/plugin-packages/{name}/apps/{alias}/link')
    def link_app(request: Any, name: str, alias: str) -> Any:
        body = request.json()
        if not isinstance(body, dict) or set(body) != {'connector'} or not isinstance(body['connector'], str) or not body['connector']:
            raise BadRequest(tr('python.web.validation.connectorName'), code='plugin-package/bad-body')
        return json_response(call(service.link_app(name, alias, body['connector'])))

    @router.delete('/api/v1/plugin-packages/{name}')
    def uninstall(request: Any, name: str) -> Any:
        return json_response(call(service.uninstall(name)))

    return router
