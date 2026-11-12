"""绑定聊天项目的长期记忆管理接口，不接受客户端替换根目录。"""
from __future__ import annotations

import sqlite3
from typing import Any
from harness.i18n import tr
from ...core.errors import HarnessError, SessionNotFoundError
from ..errors import BadRequest, Conflict, NotFound, ServiceUnavailable
from ..responses import json_response
from ..router import Router


def build_router(ctx: Any) -> Router:
    router = Router()

    def project(request, session_id):
        root, _, _ = session_context(request, session_id)
        scope = request.get('scope', 'project')
        service = guarded(lambda: ctx.harness.layered_memory.store(scope, session_id=session_id))
        return service, root

    def session_context(request, session_id):
        if request.get("root") is not None:
            raise BadRequest(tr("python.memory.invalid"), code="memory/invalid")
        async def read():
            try:
                session = ctx.harness.get_session(session_id)
            except SessionNotFoundError as exc:
                raise NotFound(tr("python.memory.session_missing"), code="memory/session-not-found") from exc
            latest = session.latest('user/message')
            query = latest.data.get('text', '') if latest is not None else ''
            return (session.header.cwd or str(ctx.workspace_root()),
                    query if isinstance(query, str) else '', latest.seq if latest is not None else None)
        context = ctx.call(read())
        if ctx.harness.memory is None:
            raise BadRequest(tr("python.memory.disabled"), code="memory/disabled")
        return context

    def guarded(operation):
        try:
            return operation()
        except HarnessError as exc:
            error = Conflict if exc.code in {"memory/changed", "memory/duplicate"} else BadRequest
            raise error(exc.message, code=exc.code) from exc
        except (OSError, sqlite3.Error) as exc:
            raise ServiceUnavailable(tr("python.memory.storage_error"), code="memory/storage-unavailable") from exc

    @router.get("/api/v1/sessions/{session_id}/memory")
    def memories(request, session_id):
        service, root = project(request, session_id)
        return json_response(guarded(lambda: {"memories": service.list(root, request.get("q", "")),
                                             "path": str(service.path(root))}))

    @router.get('/api/v1/sessions/{session_id}/memory/preview')
    def preview(request, session_id):
        root, query, source_seq = session_context(request, session_id)
        report = guarded(lambda: ctx.harness.layered_memory.preview(root, session_id=session_id, query=query))
        return json_response({'session_id': session_id, 'source_seq': source_seq, **report.explain()})

    @router.post("/api/v1/sessions/{session_id}/memory")
    def save(request, session_id):
        service, root = project(request, session_id)
        body = request.json()
        row = guarded(lambda: service.save(root, body.get("content"), topic=body.get("topic", "project"),
                                           pinned=body.get("pinned", False), evidence=body.get("evidence", ""),
                                           source="chat:" + session_id))
        return json_response({"memory": row}, status=201)

    @router.patch("/api/v1/sessions/{session_id}/memory/{memory_id}")
    def update(request, session_id, memory_id):
        service, root = project(request, session_id)
        body = request.json()
        row = guarded(lambda: service.save(root, body.get("content"), topic=body.get("topic", "project"),
                                           pinned=body.get("pinned", False), evidence=body.get("evidence", ""),
                                           source="chat:" + session_id, memory_id=memory_id,
                                           expected_revision=body.get("expected_revision")))
        return json_response({"memory": row})

    @router.delete("/api/v1/sessions/{session_id}/memory/{memory_id}")
    def delete(request, session_id, memory_id):
        service, root = project(request, session_id)
        guarded(lambda: service.delete(root, memory_id, request.json().get("expected_revision")))
        return json_response({"deleted": memory_id})

    @router.get("/api/v1/sessions/{session_id}/memory/export")
    def export(request, session_id):
        service, root = project(request, session_id)
        return json_response(guarded(lambda: service.export(root)))

    @router.post("/api/v1/sessions/{session_id}/memory/import")
    def import_memories(request, session_id):
        service, root = project(request, session_id)
        return json_response(guarded(lambda: service.import_data(root, request.json())))

    return router
