"""极简路由：``/api/v1/sessions/{id}/messages`` 形式的路径模板匹配。

路由、请求解析、响应构造由项目实现，按职责分层：
路由表 → 处理器 → :class:`~harness.web.http.Request` / :class:`Response`。
"""

from __future__ import annotations

from harness.i18n import tr

import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Callable

from .errors import BadRequest, NotFound

#: 处理器签名：``handler(request, **params) -> Response``
Handler = Callable[..., Any]


@dataclass(frozen=True, slots=True)
class Request:
    """一次 HTTP 请求的只读视图。"""

    method: str
    path: str
    query: dict[str, list[str]]
    headers: Any
    body: bytes
    params: dict[str, str] = field(default_factory=dict)
    client_ip: str | None = None
    subject: str | None = None
    visible_session_ids: frozenset[str] | None = None

    # -- 查询参数 --------------------------------------------------------
    def get(self, name: str, default: str | None = None) -> str | None:
        values = self.query.get(name)
        return values[0] if values else default

    def get_int(self, name: str, default: int | None = None) -> int | None:
        raw = self.get(name)
        if raw is None or raw == "":
            return default
        try:
            return int(raw)
        except ValueError:
            return default

    def get_bool(self, name: str, default: bool = False) -> bool:
        raw = self.get(name)
        if raw is None:
            return default
        return raw.strip().lower() in {"1", "true", "yes", "on"}

    def header(self, name: str, default: str = "") -> str:
        getter = getattr(self.headers, "get", None)
        if getter is not None:
            return getter(name, default) or default
        return default

    def json(self) -> dict[str, Any]:
        """解析 JSON 请求体；空体按 ``{}`` 处理。"""
        import json as _json

        if not self.body:
            return {}
        try:
            payload = _json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            details = {name: getattr(exc, name) for name in ('lineno', 'colno', 'pos', 'start', 'end') if hasattr(exc, name)}
            raise BadRequest(tr('ui.http.invalidJsonRequest'), code="bad-json", status=400,
                           details={**details, 'copy': {'key': 'ui.http.invalidJsonRequest'}}) from exc
        if not isinstance(payload, dict):
            raise BadRequest(tr('python.router.37840a0a4194'), code="bad-json", status=400)
        return payload


@dataclass(slots=True)
class Response:
    """一次 HTTP 响应。``body`` 始终是已编码的字节。"""

    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""

    def with_header(self, name: str, value: str) -> "Response":
        self.headers[name] = value
        return self


@dataclass(frozen=True, slots=True)
class Route:
    """一条路由：HTTP 方法 + 路径模板 + 处理器。"""

    method: str
    template: str
    handler: Handler
    segments: tuple[str, ...]

    def match(self, segments: tuple[str, ...]) -> dict[str, str] | None:
        if len(segments) != len(self.segments):
            return None
        params: dict[str, str] = {}
        for pattern, actual in zip(self.segments, segments):
            if pattern.startswith("{") and pattern.endswith("}"):
                name = pattern[1:-1]
                if not actual:
                    return None
                # 路径参数必须解码。项目 id 就是一个绝对路径（含盘符与分隔符），
                # 前端 encodeURIComponent 之后是 ``d%3A%2Fwork%2Fharness``；
                # 不 unquote 的话处理器拿到的是百分号串，重命名/删除项目会一律 404。
                params[name] = urllib.parse.unquote(actual)
            elif pattern != actual:
                return None
        return params


class Router:
    """方法 + 路径模板的路由表。

    同一路径允许注册多个方法；命中路径但方法不匹配时抛 405，
    路径本身不存在才抛 404 —— 前端据此区分"接口不存在"与"方法用错"。
    """

    def __init__(self) -> None:
        self._routes: list[Route] = []

    def add(self, method: str, template: str, handler: Handler) -> None:
        self._routes.append(
            Route(
                method=method.upper(),
                template=template,
                handler=handler,
                segments=tuple(part for part in template.strip("/").split("/") if part != ""),
            )
        )

    def route(self, method: str, template: str) -> Callable[[Handler], Handler]:
        """装饰器写法。"""

        def decorator(handler: Handler) -> Handler:
            self.add(method, template, handler)
            return handler

        return decorator

    def get(self, template: str) -> Callable[[Handler], Handler]:
        return self.route("GET", template)

    def post(self, template: str) -> Callable[[Handler], Handler]:
        return self.route("POST", template)

    def delete(self, template: str) -> Callable[[Handler], Handler]:
        return self.route("DELETE", template)

    def patch(self, template: str) -> Callable[[Handler], Handler]:
        return self.route("PATCH", template)

    def include(self, other: "Router") -> None:
        self._routes.extend(other._routes)

    def resolve(self, method: str, path: str) -> tuple[Handler, dict[str, str]]:
        """解析请求，返回 ``(处理器, 路径参数)``。"""
        segments = tuple(part for part in path.strip("/").split("/") if part != "")
        method = method.upper()
        path_matched = False
        allowed_methods: set[str] = set()
        for route in self._routes:
            params = route.match(segments)
            if params is None:
                continue
            path_matched = True
            allowed_methods.add(route.method)
            if route.method == method:
                return route.handler, params
        if path_matched:
            if "GET" in allowed_methods:
                allowed_methods.add("HEAD")
            allowed_methods.add("OPTIONS")
            raise NotFound(
                tr('python.router.76a986e2bcbc', p0=f'{method}'),
                code="method-not-allowed",
                status=405,
                details={"allow": sorted(allowed_methods)},
            )
        raise NotFound(tr('python.router.f94ec39b7259', p0=path[:200]), code="route/not-found")

    def templates(self) -> list[str]:
        return [f"{route.method} {route.template}" for route in self._routes]


def normalize_segments(path: str) -> tuple[str, ...]:
    """把路径切成小写无关的非空片段。"""
    return tuple(part for part in path.strip("/").split("/") if part != "")


__all__ = ["Request", "Response", "Route", "Router", "normalize_segments"]
