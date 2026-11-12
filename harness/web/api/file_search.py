"""会话所属的文件名查找，客户端不能替换为其他根目录。"""
from __future__ import annotations

from harness.i18n import tr

import asyncio
from pathlib import Path
from typing import Any

from ...app.file_search import FileSearchError, ProjectFileSearch
from ...core.errors import SessionNotFoundError
from ..errors import BadRequest, NotFound, ServiceUnavailable
from ..responses import json_response
from ..router import Router


def build_router(ctx: Any) -> Router:
    router = Router()
    searcher = ProjectFileSearch()

    @router.get('/api/v1/sessions/{session_id}/files/search')
    def search(request: Any, session_id: str) -> Any:
        query = request.get('q', '')
        limit = request.get('limit', '40')
        refresh = request.get('refresh', '0')
        if not isinstance(limit, str) or not limit.isascii() or not limit.isdecimal() or len(limit) > 3 or not 1 <= int(limit) <= 100 or refresh not in {'0', '1'}:
            raise BadRequest(tr('python.file_search.54aa03e58393'), code='files/invalid-search')
        if request.get('root') is not None:
            raise BadRequest(tr('python.file_search.e3208dac8e0e'), code='files/invalid-search')
        async def read():
            try:
                session = ctx.harness.get_session(session_id)
            except SessionNotFoundError as exc:
                raise NotFound(tr('python.file_search.19da31bd3666'), code='files/session-not-found') from exc
            root = Path(session.header.cwd or ctx.harness.config.cwd or Path.cwd())
            try:
                result = await asyncio.to_thread(searcher.search, root, query, int(limit), refresh=refresh == '1')
            except ValueError as exc:
                raise BadRequest(str(exc), code='files/invalid-search') from exc
            except FileSearchError as exc:
                raise ServiceUnavailable(str(exc), code='files/search-unavailable') from exc
            return {'session_id': session.id, **result}
        return json_response(ctx.call(read()))

    return router
