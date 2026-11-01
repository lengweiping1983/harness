"""项目钩子配置与纯匹配预览，不提供脚本运行接口。"""
from __future__ import annotations

from harness.i18n import tr

from pathlib import Path
from collections.abc import Mapping
from typing import Any

from ...app.hooks import HookConflict
from ...core.errors import SessionNotFoundError
from ...session.hook_runs import hook_summary
from ..errors import BadRequest, Conflict, NotFound, ServiceUnavailable
from ..responses import json_response
from ..router import Router


def build_router(ctx: Any) -> Router:
    router = Router()

    def root_of(request: Any) -> Path:
        raw = request.get('root')
        try:
            root = Path(raw).expanduser().resolve() if raw else ctx.workspace_root()
        except (ValueError, OSError) as exc:
            raise BadRequest(tr('python.hooks.adbc40420511'), code='hooks/invalid-root') from exc
        registry = ctx.workspaces()
        registry.ensure_seeded()
        if root != ctx.workspace_root() and registry.get(str(root)) is None:
            # 已注册的嵌套项目可以继承钩子；采用与实际执行相同的
            # 有效项目根目录和 Git 边界。
            origins = [str(ctx.workspace_root()), *(item['path'] for item in registry.list())]
            if not any(ctx.harness.hooks.project_root(origin) == root for origin in origins):
                raise NotFound(tr('python.hooks.560d76d74cb6'), status=403, code='hooks/unknown-project')
        return root

    def invoke(request: Any, action: Any) -> Any:
        if request.get('scope') not in (None, '', 'project', 'user'):
            raise BadRequest(tr('python.web.validation.hookScope'), code='hooks/invalid-scope')
        root = ctx.workspace_root() if request.get('scope') == 'user' else root_of(request)
        try:
            return json_response(action(root))
        except HookConflict as exc:
            raise Conflict(str(exc), code='hooks/changed') from exc
        except ValueError as exc:
            raise BadRequest(str(exc), code='hooks/invalid') from exc
        except OSError as exc:
            raise ServiceUnavailable(tr('python.hooks.be9331b7fb50'), code='hooks/persist-failed') from exc

    @router.get('/api/v1/hooks')
    def snapshot(request: Any) -> Any:
        if request.get('scope') == 'user':
            return invoke(request, lambda root: ctx.harness.hooks.user_snapshot())
        return invoke(request, ctx.harness.hooks.snapshot)

    @router.post('/api/v1/hooks')
    def save(request: Any) -> Any:
        body = request.json()
        if request.get('scope') == 'user':
            return invoke(request, lambda root: ctx.harness.hooks.save_user(body.get('document'), body.get('expected_fingerprint')))
        return invoke(request, lambda root: ctx.harness.hooks.save(root, body.get('document'), body.get('expected_fingerprint')))

    @router.post('/api/v1/hooks/trust')
    def trust(request: Any) -> Any:
        body = request.json()
        if request.get('scope') == 'user':
            return invoke(request, lambda root: ctx.harness.hooks.set_user_trust(body.get('expected_fingerprint'), body.get('trusted')))
        return invoke(request, lambda root: ctx.harness.hooks.set_trust(root, body.get('expected_fingerprint'), body.get('trusted')))

    @router.post('/api/v1/hooks/preview')
    def preview(request: Any) -> Any:
        body = request.json()
        return invoke(request, lambda root: ctx.harness.hooks.preview(body.get('document'), body.get('event'), body.get('tool')))

    @router.get('/api/v1/sessions/{session_id}/hooks/{hook_id}')
    def detail(request: Any, session_id: str, hook_id: str) -> Any:
        raw = request.get('source_seq', '')
        if not isinstance(raw, str) or not raw.isascii() or not raw.isdecimal() or len(raw) > 16 or int(raw) > 2**53 - 1:
            raise BadRequest(tr('python.hooks.9d3dc1402c6f'), code='hooks/invalid-sequence')
        async def read():
            try:
                session = ctx.harness.get_session(session_id)
            except SessionNotFoundError as exc:
                raise NotFound(tr('python.hooks.19da31bd3666'), code='hooks/evidence-not-found') from exc
            event = session.event_at(int(raw))
            data = event.data if event else None
            if event is None or event.type != 'note' or not isinstance(data, Mapping) or data.get('kind') != 'hook_run' or data.get('id') != hook_id or data.get('session_id') != session_id:
                raise NotFound(tr('python.hooks.a022236310f3'), code='hooks/evidence-not-found')
            active = session.open_turn if session_id in ctx.harness.runtime.active else None
            summary = hook_summary(data, event.seq, active_turn=active)
            return {'hook': {**dict(data), **summary, 'reason': summary['reason'] if summary['status'] == 'interrupted' else data.get('reason', '')}}
        return json_response(ctx.call(read()))

    return router
