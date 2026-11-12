"""Web 层：模块化的 HTTP + WebSocket 服务。

服务拆成可独立测试的几层，各层只做一件事：

* :mod:`harness.web.errors`      —— 统一异常（``ApiError`` 家族）
* :mod:`harness.web.wsproto`     —— WebSocket 帧编解码（无状态、可单测）
* :mod:`harness.web.router`      —— 路由表 + 请求/响应模型
* :mod:`harness.web.responses`   —— 响应构造（JSON / 文本 / 静态资源）
* :mod:`harness.web.hub`         —— 每会话的 WS 连接注册表 + 事件环形缓冲
* :mod:`harness.web.turns`       —— 回合排队（发送队列 + 取消）
* :mod:`harness.web.context`     —— 一次 ``serve()`` 的运行时上下文
* :mod:`harness.web.views`       —— 会话/事件的纯函数投影
* :mod:`harness.web.static_assets` —— 前端构建产物托管（含 SPA 回退与缓存）
* :mod:`harness.web.build_info`  —— 未构建前端时的构建与启动说明
* :mod:`harness.web.api`         —— 各业务模块的路由
* :mod:`harness.web.server`      —— 协议适配层（``Handler`` / ``WebSocketConnection``）

前端构建产物（``web/dist``）存在时自动托管；不存在时展示构建与启动说明。
"""

from __future__ import annotations

from .context import WebContext
from .errors import ApiError, BadRequest, Conflict, NotFound, ServiceUnavailable
from .hub import ConnectionHub
from .router import Request, Response, Router
from .server import Handler, WebSocketConnection, serve, stop
from .static_assets import StaticAssets

__all__ = [
    "ApiError",
    "BadRequest",
    "Conflict",
    "ConnectionHub",
    "Handler",
    "NotFound",
    "Request",
    "Response",
    "Router",
    "ServiceUnavailable",
    "StaticAssets",
    "WebContext",
    "WebSocketConnection",
    "serve",
    "stop",
]
