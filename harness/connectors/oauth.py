"""OAuth 2.1 客户端（对齐 MCP 授权规范与 GitHub 远程 MCP）。

当前实现的协议入口（本地回归不代表供应商在线验收）：

* RFC 9728 —— 受保护资源元数据发现
  （``{origin}/.well-known/oauth-protected-resource{path}``，
  或从 401 的 ``WWW-Authenticate: resource_metadata=...`` 提取）；
* RFC 8414 —— 授权服务器元数据发现
  （``{origin}/.well-known/oauth-authorization-server{path}``）；
* RFC 8628 —— 设备授权许可：服务器声明设备端点时优先使用，
  用户通过服务器提供的验证地址与用户码授权；
* OAuth 2.1 授权码 + PKCE（S256）—— 设备端点不可用时的回退，
  环回地址临时端口接收重定向；
* 刷新令牌许可 —— access_token 过期时静默续期。

本客户端通过显式环境变量读取 ``client_id``，不实现动态客户端注册。
具体查找顺序见 :func:`resolve_client_id`。元数据发现、设备轮询、
回调与刷新均保留第三方协议字段；供应商当前支持范围需单独在线验收。
"""

from __future__ import annotations

from harness.i18n import tr

import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

from ..core.errors import ConnectorError
from ..core.cleanup import join_cleanup, raise_cleanup_errors
from ..core.language import current_language

logger = logging.getLogger(__name__)

#: 单次 HTTP 请求超时（秒）
HTTP_TIMEOUT = 30.0
#: 等待用户完成浏览器/设备授权的总时限（秒）
DEFAULT_FLOW_TIMEOUT = 600.0
#: access_token 视为过期的安全余量（秒）
EXPIRY_SKEW = 60.0


@dataclass(frozen=True, slots=True)
class _EnvironmentNames:
    """变量名保持原文，列表连接词按读取语言解析。"""

    names: tuple[str, ...]


def _error_values(values: dict[str, Any]) -> dict[str, Any]:
    """统一解析显式错误参数，不按文本猜测资源。"""
    rendered = {}
    for name, value in values.items():
        if isinstance(value, OAuthError):
            value = value.localized_text()
        elif isinstance(value, _EnvironmentNames):
            value = tr('common.or').join(value.names)
        elif isinstance(value, Exception):
            value = str(value)
        rendered[name] = value
    return rendered


class OAuthError(ConnectorError):
    """OAuth 流程失败（发现、授权、交换、刷新）。"""

    code = "connector/oauth"

    @classmethod
    def resource(cls, key: str, *, code: str | None = None, **values):
        """保留来源声明的资源身份，诊断快照仍使用发生时语言。"""
        rendered = _error_values(values)
        error = cls(tr(key, **rendered), code=code)
        error.copy = (key, dict(values))
        return error

    def localized_text(self) -> str:
        """只重新解析明确声明的自有资源，外部异常正文保持原样。"""
        copy = getattr(self, 'copy', None)
        if copy is None:
            return str(self)
        key, values = copy
        rendered = _error_values(values)
        return f"[{self.code}] {tr(key, **rendered)}"


@dataclass(frozen=True, slots=True)
class ProtectedResource:
    """RFC 9728 受保护资源元数据（只取需要的字段）。"""

    resource: str
    authorization_servers: tuple[str, ...]
    scopes_supported: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AuthorizationServer:
    """RFC 8414 授权服务器元数据（只取需要的字段）。"""

    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    device_authorization_endpoint: str | None = None
    registration_endpoint: str | None = None


@dataclass(slots=True)
class PendingAuthorization:
    """一次进行中的授权（供 UI 展示与轮询）。"""

    mode: str  # 授权流程："device" 或 "code"
    #: 设备流：用户码与验证地址；授权码流：完整授权 URL
    user_code: str | None = None
    verification_uri: str | None = None
    authorize_url: str | None = None
    started_at: float = field(default_factory=time.time)


@dataclass(frozen=True, slots=True)
class TokenSet:
    """授权成果：访问令牌 + 可选刷新令牌。"""

    access_token: str
    token_type: str = "Bearer"
    refresh_token: str | None = None
    expires_at: float | None = None
    scope: str = ""

    def expired(self, *, now: float | None = None) -> bool:
        if self.expires_at is None:
            return False
        return (now if now is not None else time.time()) >= self.expires_at - EXPIRY_SKEW

    def to_dict(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token,
            "token_type": self.token_type,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "scope": self.scope,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TokenSet":
        return cls(
            access_token=str(data.get("access_token", "")),
            token_type=str(data.get("token_type", "Bearer") or "Bearer"),
            refresh_token=data.get("refresh_token") or None,
            expires_at=float(data["expires_at"]) if data.get("expires_at") else None,
            scope=str(data.get("scope", "")),
        )


# ---------------------------------------------------------------------------
# HTTP（urllib，表单编码 POST + JSON）
# ---------------------------------------------------------------------------
def _http_json(
    url: str,
    *,
    method: str = "GET",
    form: dict[str, str] | None = None,
    timeout: float = HTTP_TIMEOUT,
    tolerate_error_body: bool = False,
) -> dict[str, Any]:
    """发请求并解析 JSON 响应。

    GitHub 的 OAuth 端点默认返回表单编码，带 ``Accept: application/json``
    后返回 JSON——这里恒带该头。

    ``tolerate_error_body=True`` 时，4xx/5xx 若带 JSON 错误体也原样返回
    （RFC 8628 设备流轮询用 400 + ``authorization_pending`` 表达“继续等”），
    调用方自己按 ``error`` 字段处理。

    :raises OAuthError: 网络错误、响应非 JSON，或（默认）非 2xx。
    """
    body = urllib.parse.urlencode(form).encode() if form is not None else None
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "harness-oauth/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode('utf-8')
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode('utf-8')
        finally:
            exc.close()
        if tolerate_error_body:
            try:
                payload = json.loads(detail)
            except ValueError:
                payload = {}
            if isinstance(payload, dict) and "error" in payload:
                return payload
        raise OAuthError.resource('python.oauth.a4a4d6931dc7', p0=f'{exc.code}', p1=detail[:300], p2=f'{url}') from exc
    except OSError as exc:
        raise OAuthError.resource('python.oauth.ca748ae3eb55', p0=f'{exc}', p1=f'{url}') from exc
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise OAuthError.resource('python.oauth.21f5f27cfa3a', p0=f'{raw[:200]}', p1=f'{url}') from exc
    if not isinstance(payload, dict):
        raise OAuthError.resource('python.oauth.72ce0fbf9d91', p0=f'{url}')
    return payload


# ---------------------------------------------------------------------------
# 发现
# ---------------------------------------------------------------------------
def discover_protected_resource(resource_url: str) -> ProtectedResource:
    """RFC 9728：发现受保护资源的授权服务器。

    先试 well-known 地址（``{origin}/.well-known/oauth-protected-resource{path}``），
    失败则对资源发一次探测请求，从 401 的 ``WWW-Authenticate`` 里取
    ``resource_metadata`` 再抓元数据。

    :raises OAuthError: 资源未发布授权元数据。
    """
    parsed = urllib.parse.urlsplit(resource_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    well_known = origin + "/.well-known/oauth-protected-resource" + parsed.path
    candidates = [well_known]
    candidates.append(_resource_metadata_from_401(resource_url))
    last_error: Exception | None = None
    for url in dict.fromkeys(u for u in candidates if u):
        try:
            payload = _http_json(url)
        except OAuthError as exc:
            last_error = exc
            continue
        servers = payload.get("authorization_servers")
        if isinstance(servers, list) and servers:
            return ProtectedResource(
                resource=str(payload.get("resource") or resource_url),
                authorization_servers=tuple(str(s) for s in servers),
                scopes_supported=tuple(str(s) for s in payload.get("scopes_supported") or ()),
            )
        last_error = OAuthError.resource('python.oauth.04b5ee301947', p0=f'{url}')
    raise OAuthError.resource('python.oauth.6e8bb946769b', p0=f'{resource_url}', p1=last_error, code="connector/oauth-discovery")


def _resource_metadata_from_401(resource_url: str) -> str | None:
    """对资源发探测请求，从 401 响应头提取 resource_metadata 地址。"""
    request = urllib.request.Request(resource_url, method="POST", data=b"{}")
    request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT):
            return None  # 未要求授权：没有元数据可挖
    except urllib.error.HTTPError as exc:
        try:
            header = exc.headers.get("WWW-Authenticate", "") if exc.headers else ""
            match = re.search(r'resource_metadata="([^"]+)"', header)
            return match.group(1) if match else None
        finally:
            exc.close()
    except OSError:
        return None


def discover_authorization_server(issuer: str) -> AuthorizationServer:
    """RFC 8414：发现授权服务器端点。

    对带路径的 issuer 先尝试路径后缀形式，例如：
    ``https://github.com/.well-known/oauth-authorization-server/login/oauth``；
    再回退到 origin 根形式。

    :raises OAuthError: 元数据不可用。
    """
    parsed = urllib.parse.urlsplit(issuer)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    path = parsed.path.rstrip("/")
    candidates = [
        f"{origin}/.well-known/oauth-authorization-server{path}",
        f"{origin}{path}/.well-known/oauth-authorization-server",
        f"{origin}/.well-known/oauth-authorization-server",
    ]
    last_error: Exception | None = None
    for url in dict.fromkeys(candidates):
        try:
            payload = _http_json(url)
        except OAuthError as exc:
            last_error = exc
            continue
        if payload.get("authorization_endpoint") and payload.get("token_endpoint"):
            return AuthorizationServer(
                issuer=str(payload.get("issuer") or issuer),
                authorization_endpoint=str(payload["authorization_endpoint"]),
                token_endpoint=str(payload["token_endpoint"]),
                device_authorization_endpoint=payload.get("device_authorization_endpoint") or None,
                registration_endpoint=payload.get("registration_endpoint") or None,
            )
        last_error = OAuthError.resource('python.oauth.4e68b39cfc43', p0=f'{url}')
    raise OAuthError.resource('python.oauth.2abc37f65921', p0=f'{issuer}', p1=last_error, code="connector/oauth-discovery")


# ---------------------------------------------------------------------------
# 客户端标识：client_id
# ---------------------------------------------------------------------------
def resolve_client_id(connector_name: str, metadata: dict[str, Any]) -> str:
    """解析 OAuth client_id（GitHub 不支持动态注册，必须由用户提供）。

    查找顺序：

    1. 连接器 ``metadata.client_id_env`` 指定的环境变量
       （github 预设为 ``GITHUB_CLIENT_ID``）；
    2. 通用约定 ``HARNESS_OAUTH_CLIENT_ID_<NAME>``（名字大写、``-``→``_``）。

    :raises OAuthError: 未配置（消息里给出创建 OAuth App 的指引）。
    """
    env_names = []
    hinted = metadata.get("client_id_env")
    if isinstance(hinted, str) and hinted:
        env_names.append(hinted)
    env_names.append("HARNESS_OAUTH_CLIENT_ID_" + re.sub(r"[^A-Za-z0-9]", "_", connector_name).upper())
    for env_name in dict.fromkeys(env_names):
        value = os.environ.get(env_name, "").strip()
        if value:
            return value
    raise OAuthError.resource('python.oauth.e728da5d3340', p0=repr(connector_name),
        p1=_EnvironmentNames(tuple(dict.fromkeys(env_names))), code="connector/oauth-no-client")


# ---------------------------------------------------------------------------
# 设备授权流（RFC 8628，GitHub 首选）
# ---------------------------------------------------------------------------
async def _http_work(operation, *args, _stop: threading.Event | None = None, **kwargs):
    """等待已开始的HTTP线程收尾，取消不重放或丢弃原读取。"""
    work = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
    try:
        await asyncio.wait((work,))
        return work.result()
    except asyncio.CancelledError as cancelled:
        if _stop is not None:
            _stop.set()
        try:
            await join_cleanup(work)
        except asyncio.CancelledError:
            raise cancelled
        except Exception as failure:
            raise_cleanup_errors([cancelled, failure])
        raise


async def _run_device_flow(
    server: AuthorizationServer,
    client_id: str,
    scopes: list[str],
    *,
    on_pending: Callable[[PendingAuthorization], None],
    open_browser: bool,
    timeout: float,
) -> TokenSet:
    """设备授权：异步等待轮询，同步网络请求在线程里执行。"""
    if not server.device_authorization_endpoint:
        raise OAuthError.resource('python.oauth.77387febd78a', code="connector/oauth-unsupported")
    form = {"client_id": client_id}
    if scopes:
        form["scope"] = " ".join(scopes)
    payload = await _http_work(
        _http_json, server.device_authorization_endpoint, method="POST", form=form,
    )
    device_code = str(payload.get("device_code") or "")
    if not device_code:
        raise OAuthError.resource('python.oauth.718792cc1c22', p0=f'{payload}')
    interval = float(payload.get("interval") or 5)
    verification_uri = str(payload.get("verification_uri") or "")
    on_pending(
        PendingAuthorization(
            mode="device",
            user_code=str(payload.get("user_code") or ""),
            verification_uri=verification_uri,
        )
    )
    if open_browser and verification_uri:
        await _http_work(webbrowser.open, verification_uri)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        await asyncio.sleep(max(interval, 1.0))
        if time.monotonic() >= deadline:
            break
        result = await _http_work(
            _http_json, server.token_endpoint,
            method="POST",
            form={
                "client_id": client_id,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
            # RFC 8628：authorization_pending 等错误以 400 + JSON 返回，
            # 属于轮询的正常中间态，不能当作硬失败
            tolerate_error_body=True,
        )
        error = result.get("error")
        if not error:
            return _token_set_from_response(result)
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval += 5
            continue
        raise OAuthError.resource('python.oauth.623cb0868ca3', p0=f'{error}', p1=f"{result.get('error_description', '')}", code="connector/oauth-denied" if error == "access_denied" else "connector/oauth")
    raise OAuthError.resource('python.oauth.6d44988e734c', code="connector/oauth-timeout")


# ---------------------------------------------------------------------------
# 授权码 + PKCE 流（回退）
# ---------------------------------------------------------------------------
def _run_code_flow(
    server: AuthorizationServer,
    client_id: str,
    scopes: list[str],
    *,
    resource: str,
    on_pending: Callable[[PendingAuthorization], None],
    open_browser: bool,
    timeout: float,
    cancelled: threading.Event | None = None,
) -> TokenSet:
    """授权码 + PKCE：环回临时端口收重定向（阻塞，请在线程里跑）。"""
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    state = secrets.token_urlsafe(24)
    received: dict[str, str] = {}
    done = threading.Event()
    callback_lock = threading.Lock()
    language = current_language.get()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_: object) -> None:
            return

        def do_GET(self) -> None:
            # HTTP 工作线程不继承 ContextVar，绑定本次授权发起时的语言。
            with current_language.set(language):
                self.callback()

        def callback(self) -> None:
            parsed = urllib.parse.urlsplit(self.path)
            query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
            status, body = 404, b""
            if parsed.path == '/callback':
                valid = (query.get('state') == [state]
                    and all(len(values) == 1 for values in query.values())
                    and (('code' in query) != ('error' in query))
                    and bool(query.get('code', query.get('error', ['']))[0]))
                if not valid:
                    status, body = 400, tr('oauth.callback_invalid').encode()
                else:
                    with callback_lock:
                        if done.is_set():
                            status, body = 409, tr('oauth.callback_consumed').encode()
                        else:
                            received.update({key: values[0] for key, values in query.items()})
                            done.set()
                            status, body = 200, tr('python.oauth.7908660018e2').encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        redirect_uri = f"http://127.0.0.1:{port}/callback"
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        if scopes:
            params["scope"] = " ".join(scopes)
        if resource:
            params["resource"] = resource  # 资源指示参数遵循 RFC 8707
        authorize_url = server.authorization_endpoint + "?" + urllib.parse.urlencode(params)
        on_pending(PendingAuthorization(mode="code", authorize_url=authorize_url))
        if open_browser:
            webbrowser.open(authorize_url)
        deadline = time.monotonic() + timeout
        while not done.is_set():
            if cancelled is not None and cancelled.is_set():
                raise asyncio.CancelledError()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise OAuthError.resource('python.oauth.142e1d117565', code="connector/oauth-timeout")
            done.wait(min(remaining, 0.1))
        if cancelled is not None and cancelled.is_set():
            raise asyncio.CancelledError()
        if "error" in received:
            raise OAuthError.resource('python.oauth.999ca6f2e74c', p0=f"{received['error']}", p1=f"{received.get('error_description', '')}", code="connector/oauth-denied")
        if received.get("state") != state:
            raise OAuthError.resource('python.oauth.b0d7580a5532', code="connector/oauth-state")
        result = _http_json(
            server.token_endpoint,
            method="POST",
            form={
                "grant_type": "authorization_code",
                "client_id": client_id,
                "code": received["code"],
                "redirect_uri": redirect_uri,
                "code_verifier": verifier,
            },
        )
        return _token_set_from_response(result)
    finally:
        httpd.shutdown()
        httpd.server_close()


def _token_set_from_response(result: dict[str, Any]) -> TokenSet:
    access_token = str(result.get("access_token") or "")
    if not access_token:
        raise OAuthError.resource('python.oauth.9ff12b75df78', p0=f'{result}')
    expires_in = result.get("expires_in")
    return TokenSet(
        access_token=access_token,
        token_type=str(result.get("token_type") or "Bearer"),
        refresh_token=result.get("refresh_token") or None,
        expires_at=time.time() + float(expires_in) if expires_in else None,
        scope=str(result.get("scope") or ""),
    )


# ---------------------------------------------------------------------------
# 门面
# ---------------------------------------------------------------------------
async def run_authorization(
    resource_url: str,
    connector_name: str,
    metadata: dict[str, Any],
    *,
    scopes: list[str] | None = None,
    on_pending: Callable[[PendingAuthorization], None] = lambda pending: None,
    open_browser: bool = True,
    timeout: float = DEFAULT_FLOW_TIMEOUT,
) -> tuple[TokenSet, str, str]:
    """完整授权：发现 → 设备流（优先）或授权码流 → 返回 ``(令牌, client_id, issuer)``。

    阻塞部分在 ``asyncio.to_thread`` 里执行，不卡事件循环。``issuer``
    来自授权服务器元数据，刷新令牌时需要它。
    """
    resource, server = await _http_work(_discover_all, resource_url)
    client_id = resolve_client_id(connector_name, metadata)
    effective_scopes = scopes if scopes is not None else list(resource.scopes_supported)

    if server.device_authorization_endpoint:
        tokens = await _run_device_flow(
            server, client_id, effective_scopes,
            on_pending=on_pending, open_browser=open_browser, timeout=timeout,
        )
    else:
        cancelled = threading.Event()
        tokens = await _http_work(
            _run_code_flow, server, client_id, effective_scopes, _stop=cancelled,
            resource=resource.resource, on_pending=on_pending,
            open_browser=open_browser, timeout=timeout, cancelled=cancelled,
        )
    return tokens, client_id, server.issuer


def _discover_all(resource_url: str) -> tuple[ProtectedResource, AuthorizationServer]:
    resource = discover_protected_resource(resource_url)
    if not resource.authorization_servers:
        raise OAuthError.resource('python.oauth.aec7f65c90c8', p0=f'{resource_url}', code="connector/oauth-discovery")
    server = discover_authorization_server(resource.authorization_servers[0])
    return resource, server


async def refresh_token_set(
    issuer: str,
    client_id: str,
    tokens: TokenSet,
) -> TokenSet:
    """用刷新令牌换新的访问令牌。

    :raises OAuthError: 刷新失败（调用方应要求重新授权）。
    """
    if not tokens.refresh_token:
        raise OAuthError.resource('python.oauth.7cc64b8e223d', code="connector/oauth-no-refresh")
    server = await _http_work(discover_authorization_server, issuer)

    def blocking() -> TokenSet:
        return _token_set_from_response(
            _http_json(
                server.token_endpoint,
                method="POST",
                form={
                    "grant_type": "refresh_token",
                    "client_id": client_id,
                    "refresh_token": tokens.refresh_token or "",
                },
            )
        )

    refreshed = await _http_work(blocking)
    # GitHub 滚动刷新令牌；若响应没带新的则保留旧的
    if refreshed.refresh_token is None:
        refreshed = TokenSet(
            access_token=refreshed.access_token,
            token_type=refreshed.token_type,
            refresh_token=tokens.refresh_token,
            expires_at=refreshed.expires_at,
            scope=refreshed.scope or tokens.scope,
        )
    return refreshed


__all__ = [
    "AuthorizationServer",
    "OAuthError",
    "PendingAuthorization",
    "ProtectedResource",
    "TokenSet",
    "discover_authorization_server",
    "discover_protected_resource",
    "refresh_token_set",
    "resolve_client_id",
    "run_authorization",
]
