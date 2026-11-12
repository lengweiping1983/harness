"""Web 层运行时上下文。

把"这一份服务实例需要的所有东西"收在一个对象里：Harness 实例、事件循环
（HTTP 处理线程要把协程投递过去）、连接中枢、回合队列、静态资源目录。

所有 API 处理器都从这里取依赖，便于测试时整体替换成假实现。
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from harness.i18n import tr
from .errors import ApiError, ServiceUnavailable
from .hub import ConnectionHub
from .static_assets import StaticAssets

#: HTTP 线程等待协程结果的默认上限（秒）。
DEFAULT_CALL_TIMEOUT = 30.0


@dataclass
class WebContext:
    """一次 ``serve()`` 的全部运行时依赖。"""

    harness: Any
    loop: asyncio.AbstractEventLoop
    hub: ConnectionHub = field(default_factory=ConnectionHub)
    assets: StaticAssets | None = None
    dist_root: Path | None = None
    call_timeout: float = DEFAULT_CALL_TIMEOUT
    shutting_down: bool = False
    shared_access: Any = None
    #: 绑定到本上下文的路由表（惰性构建，见 :meth:`router`）。
    _router: Any = field(default=None, repr=False, compare=False)

    def start_event_bridge(self) -> None:
        """所有运行入口共用实时发布；订阅只建立一次。"""
        if not hasattr(self.harness, "bus") or self.__dict__.get("_bridge_started"):
            return
        self.__dict__["_bridge_started"] = True
        question_events = ("user_question/ask", "user_question/answered", "user_question/closed", "plan/review", "plan_mode", "permission_policy")
        names = ("browser/activity", "goal/message", "goal/state", "steer/state", "run/state", "queue_state", "error", "stopping", "automation/completed", "turn/start", "turn/end", "user/message", "system/prompt", "request/start", "request/progress", "request/end", "assistant/delta", "assistant/message", "assistant/partial", "tool/start", "tool/output", "tool/end", "hook/state", "compaction/start", "compaction/progress", "compaction/end", "todo/write", "skill/loaded", "agent/loaded", "agent/team", "agent/member-stream", "subagent/status", "subagent/message", "deliverables/presented")
        def forward(name, *args):
            if not args or not isinstance(args[-1], dict):
                return
            payload = dict(args[-1])
            sid = payload.get("session_id") or payload.get("parent_session_id") or (args[0] if len(args) > 1 and isinstance(args[0], str) else None)
            if not sid:
                return
            if "type" in payload:
                payload["delta_type"] = payload.pop("type")
            payload.update(type=name, session_id=sid)
            if name == 'tool/end':
                from .tool_results import project_result
                payload = project_result(payload, event_seq=payload.get('result_seq'), name=payload.get('name'))
            if name == "agent/loaded" and callable(getattr(self.harness, "agent_mount_state", None)):
                # 同一状态用于实时回执与刷新恢复，版本来自已落库的挂载事件。
                payload["mount"] = self.harness.agent_mount_state(sid)
            self.hub.publish(sid, self.hub.stamp(sid, payload))
            if name == "run/state" and payload.get("kind") != "context_compact" and payload.get("started_at", True) is not None and payload.get("status") in {"completed", "failed", "interrupted"}:
                # 保留 WebSocket 的 done 终止帧。
                self.hub.publish(sid, self.hub.stamp(sid, {"type": "done", "session_id": sid, "run_id": payload.get("id")}))
        self.__dict__["_bridge_listeners"] = []
        for name in names + question_events:
            listener = (lambda n: lambda *args: forward(n, *args))(name)
            self.harness.bus.on(name, listener)
            self.__dict__["_bridge_listeners"].append((name, listener))

    def stop_event_bridge(self) -> None:
        for name, listener in self.__dict__.pop("_bridge_listeners", []):
            self.harness.bus.off(name, listener)
        self.__dict__["_bridge_started"] = False


    def router(self) -> Any:
        """本上下文的路由表。

        必须挂在 ctx 上而不是 Handler 类上：同一进程里可能先后起多个
        ``serve()``（测试里就是这样），类级缓存会让后来的实例复用前一个
        harness 的处理器闭包，读到错误的会话。
        """
        if self._router is None:
            from .api import build_router

            self._router = build_router(self)
        return self._router

    def call(self, coro: Any, *, timeout: float | None = None) -> Any:
        """在 Web 事件循环上执行协程并同步等待结果。

        HTTP 处理器跑在 ``ThreadingHTTPServer`` 的线程里，而 harness 的协程
        都活在 ``loop`` 上；这里用 ``run_coroutine_threadsafe`` 搭桥。
        """
        if timeout is None:
            timeout = self.call_timeout
        future = self._submit(coro)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError as exc:
            future.cancel()
            raise ApiError(tr('python.audit.request_timeout'), status=504, code='server/request-timeout') from exc
        except concurrent.futures.CancelledError as exc:
            # 取消只说明结果未确认，不能把写入操作描述为确定没有执行。
            raise ServiceUnavailable(tr('web.request_cancelled'), code='server/request-cancelled') from exc

    def spawn(self, coro: Any) -> Any:
        """把协程丢到事件循环后台执行，不等待结果。"""
        return self._submit(coro)

    def _submit(self, coro: Any) -> Any:
        """关闭期间拒绝新请求，并释放尚未交给事件循环的协程。"""
        if self.shutting_down or self.loop.is_closed() or not self.loop.is_running():
            coro.close()
            raise ServiceUnavailable(tr('web.unavailable'), code='server/unavailable')
        try:
            return asyncio.run_coroutine_threadsafe(coro, self.loop)
        except RuntimeError as exc:
            # 检查与提交之间事件循环也可能关闭。
            coro.close()
            raise ServiceUnavailable(tr('web.unavailable'), code='server/unavailable') from exc

    def workspaces(self) -> Any:
        """项目注册表（惰性构建，绑定本上下文的 harness 实例）。

        挂在 ctx 上而不是模块级单例：同一进程可能先后起多个 ``serve()``
        （测试里就是这样），全局注册表会让后一个实例读到前一个 ``HARNESS_HOME``。
        """
        registry = self.__dict__.get("_workspaces")
        if registry is None:
            from .registry import WorkspaceRegistry

            home = getattr(getattr(self.harness, "config", None), "home", None)
            registry = WorkspaceRegistry(home, seed=str(self.workspace_root()))
            self.__dict__["_workspaces"] = registry
        return registry

    def workspace_root(self) -> Path:
        """媒体文件读取的根目录（会话 cwd，缺省回落到当前目录）。"""
        cwd = getattr(self.harness.config, "cwd", None)
        return Path(cwd).expanduser().resolve() if cwd else Path.cwd().resolve()

__all__ = ["WebContext", "DEFAULT_CALL_TIMEOUT"]
