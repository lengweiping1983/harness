"""本机设置页的 MCP 服务管理，密码不出现在常规状态响应中。"""
from __future__ import annotations

from harness.i18n import tr
from urllib.parse import urlsplit
import hashlib
from pathlib import Path
import re

from ...app.mcp_service import McpService
from ...app.mcp_errors import McpServiceError
from ..errors import BadRequest, ApiError
from ..responses import json_response
from ..router import Router


def build_router(ctx):
    """仅本机浏览器可启动服务或读取密码，拒绝跨站请求。"""
    router = Router()
    if not getattr(ctx, 'mcp_service', None):
        ctx.mcp_service = McpService(ctx.harness.config.home)

    def service(request):
        loopback = {'127.0.0.1', '::1', 'localhost'}
        authority = urlsplit('http://' + (request.header('Host') or ''))
        host = authority.hostname
        origin = request.header('Origin')
        source = urlsplit(origin or '')
        if request.client_ip not in loopback or host not in loopback or (origin and
            (source.scheme not in {'http', 'https'} or source.hostname != host or source.port != authority.port)):
            raise ApiError(tr('python.web.validation.mcpLocalOnly'), status=403, code='mcp/local-only')
        return ctx.mcp_service

    def response(data):
        result = json_response(data)
        result.headers['Cache-Control'] = 'no-store'
        return result

    def projects():
        """只列出界面已登记且仍存在的目录，不接受模型提交的新路径。"""
        if not hasattr(ctx, 'workspaces'):
            return []
        registry = ctx.workspaces()
        rows = []
        for record in registry.list():
            if not Path(record['path']).is_dir():
                continue
            name = record['name']
            identifier = name if re.fullmatch(r'[A-Za-z0-9_-]{1,64}', name) else 'p-' + hashlib.sha256(record['id'].encode()).hexdigest()[:12]
            rows.append({**record, 'project_id': identifier})
        return sorted(rows, key=lambda row: row.get('last_used_at', 0), reverse=True)

    @router.get('/api/v1/mcp-service')
    def status(request):
        manager = service(request)
        rows = projects()
        return response({**manager.snapshot(), 'workspaces': rows,
            'default_workspace_id': rows[0]['id'] if rows else ''})

    @router.post('/api/v1/mcp-service/start')
    def start(request):
        try:
            manager = service(request)
            payload = request.json()
            if not isinstance(payload, dict) or set(payload) - {'workspace_id', 'scope', 'tunnel'}:
                raise ValueError(tr('python.catalog.validation.registeredProject'))
            if 'workspace_id' in payload and (not isinstance(payload['workspace_id'], str) or not payload['workspace_id'].strip()):
                raise ValueError(tr('python.catalog.validation.registeredProject'))
            if payload.get('scope', 'all') == 'all' and not payload.get('workspace_id'):
                registry = ctx.workspaces()
                registry.ensure_storage()
                return response(manager.start_all(Path(ctx.harness.config.home) / 'workspaces.json', **({'tunnel': payload['tunnel']} if 'tunnel' in payload else {})))
            if payload.get('scope', 'selected') != 'selected':
                raise ValueError(tr('python.catalog.validation.projectScope'))
            rows = projects()
            identifier = payload.get('workspace_id')
            selected = next((row for row in rows if row['id'] == identifier), None) if identifier else next(iter(rows), None)
            if selected is None:
                raise ValueError(tr('python.catalog.validation.selectProject'))
            return response(manager.start(selected['path'], selected['project_id'], **({'tunnel': payload['tunnel']} if 'tunnel' in payload else {})))
        except McpServiceError as exc:
            raise BadRequest(str(exc), code='mcp/tunnel-config', details={'copy': {'key': exc.key, 'params': exc.values}}) from exc
        except ValueError as exc:
            raise BadRequest(str(exc), code='mcp/invalid-project') from exc

    @router.post('/api/v1/mcp-service/stop')
    def stop(request):
        return response(service(request).stop())

    @router.post('/api/v1/mcp-service/password')
    def password(request):
        try:
            return response({'password': service(request).password()})
        except (ValueError, OSError) as exc:
            raise BadRequest(str(exc), code='mcp/password-unavailable') from exc

    @router.post('/api/v1/mcp-service/companion')
    def companion(request):
        try:
            return response(service(request).configure_companion(request.json()))
        except McpServiceError as exc:
            raise BadRequest(str(exc), code='mcp/companion-config', details={'copy': {'key': exc.key, 'params': exc.values}}) from exc
        except (ValueError, OSError) as exc:
            raise BadRequest(str(exc), code='mcp/companion-config') from exc

    @router.post('/api/v1/mcp-service/companion/token')
    def companion_token(request):
        try:
            return response({'token': service(request).companion_token()})
        except McpServiceError as exc:
            raise BadRequest(str(exc), code='mcp/companion-unavailable', details={'copy': {'key': exc.key, 'params': exc.values}}) from exc
        except (ValueError, OSError) as exc:
            raise BadRequest(str(exc), code='mcp/companion-unavailable') from exc

    return router
