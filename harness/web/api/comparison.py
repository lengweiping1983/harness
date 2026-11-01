"""会话工作区的只读比较 API。"""
import asyncio

from harness.app.comparison import ComparisonService
from harness.core.errors import HarnessError
from harness.i18n import tr
from ..errors import BadRequest, Conflict
from ..responses import json_response
from ..router import Router


def build_router(ctx):
    router = Router()

    def read(request, session_id, *, options=False):
        try:
            offset = int(request.get("offset", "0"))
        except (ValueError, TypeError) as exc:
            raise BadRequest(tr("comparison.invalid"), code="review/invalid") from exc

        async def query():
            session = ctx.harness.get_session(session_id)
            cwd = session.header.cwd or ctx.workspace_root()
            service = ComparisonService()
            if options:
                return await asyncio.to_thread(service.options, cwd, search=request.get("search", ""), offset=offset)
            return await asyncio.to_thread(service.compare, cwd, scope=request.get("scope", "uncommitted"),
                                           base=request.get("base", ""), target=request.get("target", ""),
                                           offset=offset, revision=request.get("revision", ""), path=request.get("path"))
        try:
            return json_response(ctx.call(query()))
        except HarnessError as exc:
            error = Conflict if exc.code == "review/changed" else BadRequest
            raise error(exc.message, code=exc.code) from exc
        except OSError as exc:
            raise Conflict(tr("comparison.changed"), code="review/changed") from exc

    @router.get("/api/v1/sessions/{session_id}/comparison")
    def comparison(request, session_id):
        return read(request, session_id)

    @router.get("/api/v1/sessions/{session_id}/comparison/options")
    def options(request, session_id):
        return read(request, session_id, options=True)

    return router
