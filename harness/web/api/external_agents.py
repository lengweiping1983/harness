"""独立 Agent 本机检测接口，只读且不启动进程。"""
from __future__ import annotations

import os
from typing import Any

from ...acp.runners import discover_runners
from ..responses import json_response
from ..router import Router


def build_router(ctx: Any) -> Router:
    """共享 CLI 预设，返回适配器存在性而非未经验证的登录状态。"""
    router = Router()

    @router.get('/api/v1/external-agents')
    def catalog(request: Any) -> Any:
        config = getattr(getattr(ctx, 'harness', None), 'config', None)
        return json_response({'runners': discover_runners(), 'platform': 'windows' if os.name == 'nt' else 'posix',
                              'delegation_enabled': bool(config and config.enable_shell and config.enable_fs)},
                             headers={'Cache-Control': 'no-store'})

    return router
