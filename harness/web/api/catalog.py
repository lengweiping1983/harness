"""运行时目录类接口：状态、配置、工具、技能。

这些是"只读目录"，前端启动时拉一次就够；之后只在新建会话或用户手动刷新
时重取，因此全部走 ETag/``no-store`` 之外的最长缓存策略由调用方决定。
"""

from __future__ import annotations

from contextlib import nullcontext
from typing import Any

from ..responses import json_response
from ..errors import NotFound
from ...core.errors import SessionNotFoundError
from ..router import Router
from ..models import model_catalog
from ..views import catalog_status, skill_catalog, tool_catalog


def build_router(ctx: Any) -> Router:
    router = Router()

    @router.get("/api/v1/status")
    def status(request: Any) -> Any:
        """返回当前运行目录和实时连接状态。"""
        payload = catalog_status(ctx.harness)
        payload["live"] = {
            "sessions": len(ctx.harness.list_sessions()),
            "observers": ctx.hub.observer_count(),
            "loop_running": ctx.loop.is_running(),
        }
        return json_response(payload, accept_encoding=request.header("Accept-Encoding"))

    @router.get("/api/v1/health")
    def health(request: Any) -> Any:
        """极简存活探针：给进程管理器 / 部署脚本用，不做重活。"""
        return json_response({"ok": True, "loop": ctx.loop.is_running()})

    @router.get("/api/v1/config")
    def config(request: Any) -> Any:
        """不含密钥的配置快照。"""
        return json_response(
            {"config": ctx.harness.config.to_dict()},
            accept_encoding=request.header("Accept-Encoding"),
        )

    @router.get("/api/v1/models")
    def models(request: Any) -> Any:
        """模型目录：composer 的 model seat 与 ``/model`` 菜单共用这一份。

        这里同时把设置面板里的「模型可见性」合并进来：用户在设置里取消勾选的
        模型不会出现在选择器里，但**当前正在用的那个永远保留** —— 把正在用的
        模型从下拉框里抹掉，用户会发现关掉也关不掉，比显示出来更困惑。
        """
        store = ctx.__dict__.get("_ui_settings")
        with store.locked() if store is not None else nullcontext():
            payload = model_catalog(ctx.harness.config)
            visibility = store.snapshot().get("model_visibility") if store is not None else None
        if isinstance(visibility, dict):
            active = payload.get("active")
            for entry in payload.get("models", []):
                visible = bool(visibility.get(entry["id"], True))
                entry["visible"] = visible or entry["id"] == active
        else:
            for entry in payload.get("models", []):
                entry["visible"] = True
        return json_response(
            payload,
            accept_encoding=request.header("Accept-Encoding"),
        )

    @router.get("/api/v1/tools")
    def tools(request: Any) -> Any:
        return json_response(
            {"tools": tool_catalog(ctx.harness)},
            accept_encoding=request.header("Accept-Encoding"),
        )

    @router.get("/api/v1/skills")
    def skills(request: Any) -> Any:
        """可按聊天读取目录；未指定聊天时使用启动目录。"""
        session = None
        if request.get("session_id"):
            try:
                session = ctx.harness.get_session(request.get("session_id"))
            except SessionNotFoundError as exc:
                raise NotFound(str(exc), code="skill/session-not-found") from exc
        return json_response(
            {"skills": skill_catalog(ctx.harness, session)},
            accept_encoding=request.header("Accept-Encoding"),
        )

    return router


__all__ = ["build_router"]
