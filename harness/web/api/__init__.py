"""API 路由装配。

一个函数 :func:`build_router` 把各模块的路由合并成一张表，模块之间互不感知，
新增功能只需要加一个 ``xxx.py`` 并在这里 include 一行。
"""

from __future__ import annotations

from typing import Any

from ..router import Router
from . import (
    attachments,
    academy,
    codex_gateway,
    collaboration,
    access,
    catalog,
    directories,
    external_agents,
    interaction,
    plugins,
    plugin_packages,
    project_board,
    runtime,
    screenshot,
    voice,
    sessions,
    superagent,
    settings,
    mcp_service,
    workspace,
    workspaces,
    v1,
)


def build_router(ctx: Any) -> Router:
    """按模块合并全部 API 路由。"""
    router = Router()
    router.include(access.build_router(ctx))
    router.include(collaboration.build_router(ctx))
    router.include(attachments.build_router(ctx))
    router.include(codex_gateway.build_router(ctx))
    router.include(screenshot.build_router(ctx))
    router.include(voice.build_router(ctx))
    router.include(catalog.build_router(ctx))
    router.include(sessions.build_router(ctx))
    router.include(interaction.build_router(ctx))
    router.include(workspace.build_router(ctx))
    router.include(workspaces.build_router(ctx))
    router.include(project_board.build_router(ctx))
    router.include(directories.build_router(ctx))
    router.include(settings.build_router(ctx))
    router.include(mcp_service.build_router(ctx))
    router.include(external_agents.build_router(ctx))
    router.include(runtime.build_router(ctx))
    router.include(plugins.build_router(ctx))
    router.include(plugin_packages.build_router(ctx))
    router.include(v1.build_router(ctx))
    router.include(academy.build_router(ctx))
    router.include(superagent.build_router(ctx))
    return router


__all__ = ["build_router"]
