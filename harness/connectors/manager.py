"""连接器运行时管理：黏合声明存储与 MCP 注册表 / 工具表。

职责（连接器生命周期 install → enable → bind → invoke）：

* :meth:`start_enabled` —— ``ensure_started`` 阶段把 enabled∧bound 的
  连接器接入 MCP 注册表并注册 ``mcp__<name>__<tool>`` 工具；
* :meth:`enable` / :meth:`disable` —— 运行时动态启停（连接/断开、
  注册/注销工具、刷新 MCP 说明段、广播 ``plugin/changed``）；
* :meth:`bind` —— 探测性连接及所属关闭成功后标记 bound；
* OAuth（方式 B）—— :meth:`begin_oauth_login` 启动设备流/授权码流，
  令牌存系统凭据库（Windows 凭据管理器 / 0600 文件兜底），连接时
  注入 ``Authorization: Bearer``，过期自动刷新；
  :meth:`oauth_status` 供 UI 轮询，:meth:`oauth_logout` 注销凭据。

与 ``HARNESS_MCP_SERVERS`` 的关系：env 声明由 ``ensure_started``
直接驱动（始终视为启用），本管理层只管 ``connectors.json`` 里的声明。
"""

from __future__ import annotations

from harness.i18n import tr

import asyncio
import logging
import threading
import time
from typing import Any, Callable

from ..core.errors import ConnectorError
from ..core.cleanup import join_cleanup, raise_cleanup_errors
from ..core.events import EventBus
from ..prompt.registry import SystemPromptRegistry
from ..tools.builtin.mcp_client import McpRegistry
from ..tools.builtin.mcp_tools import build_remote_tool
from ..tools.registry import ToolRegistry
from .credentials import CredentialStore
from .oauth import (
    OAuthError,
    PendingAuthorization,
    TokenSet,
    refresh_token_set,
    run_authorization,
)
from .store import VAR_REF_RE, ConnectorRecord, ConnectorStore

logger = logging.getLogger(__name__)


class ConnectorManager:
    """连接器运行时管理器。"""

    def __init__(
        self,
        store: ConnectorStore,
        mcp: McpRegistry,
        tools: ToolRegistry,
        prompts: SystemPromptRegistry,
        bus: EventBus,
        credentials: CredentialStore,
        *,
        register_prompt: Any = None,
    ) -> None:
        self.store = store
        self.mcp = mcp
        self.tools = tools
        self.prompts = prompts
        self.bus = bus
        #: OAuth 凭据（系统钥匙串 / 0600 文件兜底）
        self.credentials = credentials
        #: 注入 Harness._register_mcp_prompt（说明段注册复用既有逻辑）
        self._register_prompt = register_prompt
        #: 进行中的 OAuth 授权（``name -> PendingAuthorization`` 快照）
        self._oauth_pending: dict[str, dict[str, Any]] = {}
        #: OAuth 自有资源错误保留身份，其余错误保留原诊断字符串。
        self._oauth_error: dict[str, OAuthError | str] = {}
        #: 后台授权任务（``name -> asyncio.Task``），close 时统一取消
        self._oauth_epochs: dict[str, int] = {}
        self._oauth_tasks: dict[str, asyncio.Task[Any]] = {}
        self._retired_oauth_tasks: set[asyncio.Task[Any]] = set()
        self._oauth_lock = threading.RLock()
        self._close_task: asyncio.Task[None] | None = None
        #: 连接前的本地鉴权错误，供连接器目录展示。
        self._auth_errors: dict[str, tuple[str, tuple[str, ...]]] = {}
        #: 每次启停推进代号，迟到的连接不能恢复已撤销的工具。
        self._connection_epochs: dict[str, int] = {}

    # -- 启动 ------------------------------------------------------------
    async def start_enabled(self) -> None:
        """把 enabled∧bound 的连接器接入 MCP 注册表并注册工具。

        单台失败只记警告，不影响其它连接器（与 MCP 既有容错纪律一致）。
        """
        for record in self.store.list():
            if not (record.enabled and record.bound):
                continue
            await self._connect_and_register(record)

    async def _connect_and_register(self, record: ConnectorRecord) -> int:
        """连接一台连接器并注册其工具，返回注册的工具数。"""
        if not self._current_record(record):
            return 0
        epoch = self._connection_epochs.get(record.name, 0) + 1
        self._connection_epochs[record.name] = epoch
        config = record.to_mcp_config()
        await self._apply_oauth_header(record, config)
        if self._connection_epochs.get(record.name) != epoch or not self._current_record(record):
            return 0
        try:
            self._validate_auth_header(record, config)
            self._unregister_tools(record.name)
            supervisor = await self.mcp.replace(config)
            if self._connection_epochs.get(record.name) != epoch or not self._current_record(record):
                await supervisor.aclose()
                return 0
            await supervisor.connect()
            if self._connection_epochs.get(record.name) != epoch or not self._current_record(record):
                await supervisor.aclose()
                return 0
        except Exception as exc:
            logger.warning(tr('log.3ca623ed39ff'), record.name, exc)
            return 0
        return self._register_tools(record, supervisor)

    def _current_record(self, record: ConnectorRecord) -> bool:
        """包更新或配置变化后，旧声明不能重新注册工具或完成绑定。"""
        try:
            return self.store.get(record.name) == record
        except ConnectorError:
            return False

    def _register_tools(self, record: ConnectorRecord, supervisor: Any) -> int:
        """把监督器当前的工具注册进工具表。"""
        count = 0
        for public_name in supervisor.public_names():
            remote = supervisor.remote_tool(public_name)
            if remote is None:
                continue
            self.tools.register(
                build_remote_tool(supervisor, remote, timeout=record.to_mcp_config().tool_call_timeout_ms / 1000 + 5),
                replace=True,
            )
            count += 1
        if count:
            self._refresh_prompt()
        return count

    def _refresh_prompt(self) -> None:
        """刷新系统提示里的 MCP 说明段。"""
        instructions = self.mcp.instructions()
        if self._register_prompt is not None:
            self._register_prompt(instructions)

    # -- 运行时启停 ------------------------------------------------------
    async def enable(self, name: str) -> dict[str, Any]:
        """启用一台连接器：连接 + 注册工具。"""
        record = self.store.set_enabled(name, True)
        registered = await self._connect_and_register(record)
        await self._emit_changed("connector/enable", name)
        return {**record.to_dict(), "connected": registered > 0, "tool_count": registered}

    async def disable(self, name: str) -> dict[str, Any]:
        """停用一台连接器：注销工具 + 关闭连接（幂等）。"""
        record = self.store.set_enabled(name, False)
        await self.disconnect_runtime(name)
        await self._emit_changed("connector/disable", name)
        return {**record.to_dict(), "connected": False, "tool_count": 0}

    async def disconnect_runtime(self, name: str) -> None:
        """停止插件拥有的连接而不改写用户连接偏好。"""
        from ..tools.builtin.mcp_client import McpError
        self._connection_epochs[name] = self._connection_epochs.get(name, 0) + 1
        self._unregister_tools(name)
        try:
            supervisor = self.mcp.get(name)
        except McpError:
            pass
        else:
            await supervisor.aclose()
        self._refresh_prompt()

    def _unregister_tools(self, name: str) -> None:
        """注销一台连接器的全部 ``mcp__<name>__*`` 工具。"""
        prefix = f"mcp__{name}__"
        for tool_name in list(self.tools.names()):
            if tool_name.startswith(prefix):
                self.tools.unregister(tool_name)

    async def bind(self, name: str) -> dict[str, Any]:
        """探测连接及所属关闭成功后保存绑定状态。

        提交前再次核对声明身份；已启用的连接器随后正式接入。
        """
        record = self.store.get(name)
        config = record.to_mcp_config()
        await self._apply_oauth_header(record, config)
        self._validate_auth_header(record, config)
        # 绑定是授权闭环：连接/鉴权失败必须显式抛出。
        # McpServerConfig 默认 fail_on_startup_error=False（后台接入时
        # 吞掉错误、只记警告并安排重连），直接用于探测会把 401 之类的
        # 失败吞掉、误标 bound，这里强制改为快速失败。
        config.fail_on_startup_error = True
        from ..tools.builtin.mcp_client import McpServerSupervisor
        probe = McpServerSupervisor(config)
        failures: list[BaseException] = []
        try:
            await probe.connect()
        except Exception as exc:
            failure = ConnectorError(tr('python.manager.27fb8cfcde13', p0=f'{name!r}', p1=f'{exc}'))
            failure.__cause__ = exc
            failures.append(failure)
        except BaseException as exc:
            failures.append(exc)
        try:
            await join_cleanup(asyncio.create_task(probe.aclose()))
        except BaseException as exc:
            failures.append(exc)
        raise_cleanup_errors(failures)
        if not self._current_record(record):
            raise ConnectorError(tr('config.validation.message_22'))
        record = self.store.set_bound(name, True)
        if record.enabled:
            return await self.enable(name)
        await self._emit_changed("connector/bind", name)
        return {**record.to_dict(), "connected": False, "tool_count": 0}

    async def unbind(self, name: str) -> dict[str, Any]:
        """解绑并停用。"""
        record = self.store.get(name)
        result = await self.disable(name) if record.enabled else {
            **record.to_dict(),
            "connected": False,
            "tool_count": 0,
        }
        self.store.set_bound(name, False)
        await self._emit_changed("connector/unbind", name)
        return result

    # -- OAuth（方式 B：远程 MCP + 浏览器授权） ---------------------------
    async def begin_oauth_login(self, name: str, *, open_browser: bool = True) -> dict[str, Any]:
        """启动 OAuth 授权（设备流优先，授权码 + PKCE 回退）。

        校验通过后立刻在事件循环上挂后台任务并返回 pending 状态，
        实际授权（发现 → 用户授权 → 令牌交换 → 落库 → 绑定）异步进行；
        UI 用 :meth:`oauth_status` 轮询进展。

        :raises ConnectorError: 连接器不存在 / 未声明 OAuth / 非 HTTP 传输 /
            已有授权在进行。
        """
        if self._close_task is not None:
            raise ConnectorError(tr('connector.manager.closed'), code='connector/closed')
        record = self.store.get(name)
        if not record.metadata.get("oauth"):
            raise ConnectorError(
                tr('python.manager.50d17bed7cde', p0=f'{name!r}'),
                code="connector/oauth-unsupported",
            )
        if record.transport != "streamable-http":
            raise ConnectorError(
                tr('python.manager.79c8a26a3e6a', p0=f'{name!r}'),
                code="connector/oauth-unsupported",
            )
        with self._oauth_lock:
            if name in self._oauth_tasks and not self._oauth_tasks[name].done():
                raise ConnectorError(
                    tr('python.manager.4630f44f5cf1', p0=f'{name!r}'),
                    code="connector/oauth-pending",
                )
            self._oauth_tasks.pop(name, None)
            self._oauth_error.pop(name, None)
            self._oauth_pending[name] = {
                "state": "pending",
                "started_at": time.time(),
            }
        epoch = self._oauth_epochs.get(name, 0) + 1
        self._oauth_epochs[name] = epoch
        task = asyncio.create_task(self._oauth_login_task(name, open_browser, epoch))
        with self._oauth_lock:
            self._oauth_tasks[name] = task
        return self.oauth_status(name)

    async def _oauth_login_task(self, name: str, open_browser: bool, epoch: int) -> None:
        """后台授权：发现 → 授权 → 落库 → 绑定 → 广播。"""
        def current():
            return self._oauth_epochs.get(name, 0) == epoch
        if not current():
            return
        try:
            record = self.store.get(name)
            oauth_meta = record.metadata.get("oauth") or {}
            scopes = oauth_meta.get("scopes") if isinstance(oauth_meta.get("scopes"), list) else None
            # OAuth 元数据在 metadata.oauth 子键里（client_id_env / scopes），
            # 授权发现与 client_id 解析都以它为准
            tokens, client_id, issuer = await run_authorization(
                record.url or "",
                name,
                oauth_meta,
                scopes=scopes,
                on_pending=self._on_pending(name, epoch),
                open_browser=open_browser,
            )
            if not current():
                return
            if not self._current_record(record):
                raise OAuthError.resource('config.validation.message_22')
            payload: dict[str, Any] = {
                **tokens.to_dict(),
                "client_id": client_id,
                "issuer": issuer,
            }
            self.credentials.set(name, payload)
            record = self.store.set_bound(name, True)
            with self._oauth_lock:
                self._oauth_pending.pop(name, None)
            if record.enabled:
                await self._connect_and_register(record)
            if current():
                if not self._current_record(record):
                    raise OAuthError.resource('config.validation.message_22')
                await self._emit_changed("connector/oauth-authorized", name)
        except asyncio.CancelledError:
            # 服务关闭 / 用户注销：清掉挂起状态，不留「授权中」残影
            with self._oauth_lock:
                if current():
                    self._oauth_pending.pop(name, None)
            raise
        except Exception as exc:
            if not current():
                return
            with self._oauth_lock:
                self._oauth_pending.pop(name, None)
                self._oauth_error[name] = exc if isinstance(exc, OAuthError) else str(exc)
            logger.warning(tr('log.8ac27a4edc0d'), name, exc)
            await self._emit_changed("connector/oauth-error", name)
            return

    def _on_pending(self, name: str, epoch: int) -> Callable[[PendingAuthorization], None]:
        def update(pending: PendingAuthorization) -> None:
            with self._oauth_lock:
                if self._oauth_epochs.get(name, 0) != epoch:
                    return
                self._oauth_pending[name] = {
                    "state": "pending",
                    "mode": pending.mode,
                    "user_code": pending.user_code,
                    "verification_uri": pending.verification_uri,
                    "authorize_url": pending.authorize_url,
                    "started_at": pending.started_at,
                }
        return update

    def oauth_status(self, name: str) -> dict[str, Any]:
        """OAuth 状态快照（供 UI 轮询）。

        ``state`` 取值：``none``（未授权）/ ``pending``（授权中）/
        ``authorized``（已授权）/ ``error``（授权失败）。
        """
        return self._oauth_status_for(self.store.get(name))

    def _oauth_status_for(self, record: ConnectorRecord) -> dict[str, Any]:
        """复用本次目录快照，避免每个连接器重新扫描全部插件包。"""
        name = record.name
        with self._oauth_lock:
            pending = self._oauth_pending.get(name)
            error = self._oauth_error.get(name)
        creds = self.credentials.get(name)
        base: dict[str, Any] = {
            "state": "none",
            "supported": bool(record.metadata.get("oauth")),
            "backend": self.credentials.backend_name,
        }
        if pending is not None:
            base.update(pending)
            base["state"] = "pending"
        elif error is not None:
            base["state"] = "error"
            base["message"] = error.localized_text() if isinstance(error, OAuthError) else error
        elif creds is not None:
            base["state"] = "authorized"
            base["scope"] = str(creds.get("scope", ""))
            base["expires_at"] = creds.get("expires_at")
        return base

    async def oauth_logout(self, name: str) -> dict[str, Any]:
        """注销 OAuth：取消进行中的授权、删除凭据并解绑。"""
        with self._oauth_lock:
            self._oauth_epochs[name] = self._oauth_epochs.get(name, 0) + 1
            task = self._oauth_tasks.get(name)
            if task is not None and not task.done():
                self._retired_oauth_tasks.add(task)
                task.add_done_callback(self._retired_oauth_finished)
                task.cancel()
            self._oauth_tasks.pop(name, None)
            self._oauth_pending.pop(name, None)
            self._oauth_error.pop(name, None)
        self.credentials.delete(name)
        record = self.store.get(name)
        if record.bound:
            await self.unbind(name)
        else:
            await self._emit_changed("connector/oauth-logout", name)
        return {"name": name, "state": "none"}

    def _retired_oauth_finished(self, task: asyncio.Task[Any]) -> None:
        """正常结束释放引用，异常保留到管理器关闭时报告。"""
        if task.cancelled() or task.exception() is None:
            self._retired_oauth_tasks.discard(task)

    async def _apply_oauth_header(self, record: ConnectorRecord, config: Any) -> None:
        """若该连接器已有 OAuth 凭据，把 ``Authorization`` 换成 OAuth 令牌。

        OAuth 令牌优先于 ``${VAR}`` 环境引用（更安全：令牌在系统凭据库，
        无需环境变量）；过期时用刷新令牌静默续期，刷新失败则回退到
        环境变量路径并记警告。
        """
        payload = self.credentials.get(record.name)
        if not payload:
            return
        try:
            tokens = TokenSet.from_dict(payload)
            if tokens.expired():
                refreshed = await refresh_token_set(
                    str(payload.get("issuer", "")),
                    str(payload.get("client_id", "")),
                    tokens,
                )
                tokens = refreshed
                self.credentials.set(
                    record.name,
                    {
                        **tokens.to_dict(),
                        "client_id": str(payload.get("client_id", "")),
                        "issuer": str(payload.get("issuer", "")),
                    },
                )
            config.headers = {
                key: value for key, value in config.headers.items()
                if key.lower() != "authorization"
            }
            config.headers["Authorization"] = f"Bearer {tokens.access_token}"
        except OAuthError as exc:
            logger.warning(
                tr('log.4058cdf5982d'),
                record.name, exc,
            )

    def _validate_auth_header(self, record: ConnectorRecord, config: Any) -> None:
        """拦截空鉴权头；OAuth 凭据覆盖环境引用后再校验，避免误拒绝。"""
        self._auth_errors.pop(record.name, None)
        for key, value in config.headers.items():
            if key.lower() != "authorization":
                continue
            parts = value.split()
            if parts and not (len(parts) == 1 and parts[0].lower() == "bearer"):
                continue
            variables = sorted({
                variable
                for header, template in record.headers.items()
                if header.lower() == "authorization"
                for variable in VAR_REF_RE.findall(template)
            })
            if record.metadata.get('oauth'):
                key = 'connector.auth.requiredEnvOAuth' if variables else 'connector.auth.requiredOAuth'
            else:
                key = 'connector.auth.requiredEnv' if variables else 'connector.auth.required'
            self._auth_errors[record.name] = (key, tuple(variables))
            raise ConnectorError(self._auth_error_text(record.name), code="connector/auth-required")

    def _auth_error_text(self, name: str) -> str:
        """保留变量名，读取时按当前语言组合列表及完整错误说明。"""
        key, variables = self._auth_errors[name]
        return tr(key, name=name, variables=tr('ui.common.list_separator').join(variables))

    # -- 目录 ------------------------------------------------------------
    def catalog(self) -> list[dict[str, Any]]:
        """连接器目录：声明 ⨝ 实时连接状态 ⨝ 工具数 ⨝ OAuth 状态。"""
        entries: list[dict[str, Any]] = []
        supervisors = {s.server_name: s for s in self.mcp.supervisors}
        for record in self.store.list():
            supervisor = supervisors.get(record.name)
            entries.append(
                {
                    **record.to_dict(),
                    "connected": bool(supervisor and supervisor.connected),
                    "tool_count": len(supervisor.public_names()) if supervisor else 0,
                    "last_error": (
                        self._auth_error_text(record.name)
                        if record.name in self._auth_errors else (
                            supervisor.status().get("last_error", "") if supervisor else ""
                        )
                    ),
                    "oauth": self._oauth_status_for(record),
                }
            )
        return entries

    async def aclose(self) -> None:
        """共享唯一关闭任务，观察者取消不打断已开始的授权收尾。"""
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close_oauth())
        await join_cleanup(self._close_task)

    async def _close_oauth(self) -> None:
        """只向原授权任务投递一次取消，等待全部所属清理。"""
        with self._oauth_lock:
            active = list(self._oauth_tasks.values())
            tasks = list(dict.fromkeys([*active, *self._retired_oauth_tasks]))
            for name in self._oauth_tasks:
                self._oauth_epochs[name] = self._oauth_epochs.get(name, 0) + 1
            self._oauth_tasks.clear()
            self._oauth_pending.clear()
        for task in active:
            if not task.done():
                task.cancel()
        if tasks:
            outcomes = await asyncio.gather(*tasks, return_exceptions=True)
            raise_cleanup_errors([error for error in outcomes
                if isinstance(error, BaseException) and not isinstance(error, asyncio.CancelledError)])

    # -- 事件 ------------------------------------------------------------
    async def _emit_changed(self, action: str, name: str) -> None:
        await self.bus.emit(
            "plugin/changed", None, {"kind": "connector", "action": action, "name": name}
        )


__all__ = ["ConnectorManager"]
