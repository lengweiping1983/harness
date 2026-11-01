"""异步事件总线。

服务与事件被贡献到一个共享的 ``Context`` 上，这里拆成两个更轻量的构件：
:class:`EventBus` + :class:`Context`：

* :class:`EventBus` —— 事件名 -> 监听器列表，支持 ``on`` / ``once`` / ``emit`` /
  ``off``；监听器可以是同步函数或协程函数。
* :class:`Context` —— 服务的注册表，插件通过它贡献工具、skill、提示词片段等。

这里用 **显式注册/撤销** 表达"注册即效果、撤销即还原"的语义：
:meth:`Context.plugin` 上下文管理器会把内部
产生的服务注册登记下来，退出时按逆序撤销。
"""

from __future__ import annotations

from harness.i18n import tr

import asyncio
import inspect
import logging
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Iterator

logger = logging.getLogger(__name__)

# 事件监听器签名：(event_payload) -> None | Awaitable[None]
Listener = Callable[[Any], Any]


@dataclass(eq=False)
class _Registration:
    """一条订阅记录，用于精确撤销。"""

    event: str
    listener: Listener
    once: bool = False
    cancelled: bool = False


class EventBus:
    """轻量异步事件总线。

    典型用法::

        bus = EventBus()

        @bus.on("tool/call")
        async def on_call(payload): ...

        await bus.emit("tool/call", payload)
    """

    def __init__(self) -> None:
        # 只跟踪按事件分组的活动注册；保留已取消的闭包
        # 会让其流队列无法释放，并使每个增量事件都扫描
        # 启动以来累积的全部订阅。
        self._listeners: dict[str, list[_Registration]] = defaultdict(list)

    # -- 订阅 ------------------------------------------------------------
    def on(self, event: str, listener: Listener | None = None) -> Listener | Callable[[Listener], Listener]:
        """注册一个监听器。

        既支持装饰器写法 ``@bus.on('x')``，也支持直接调用
        ``bus.on('x', func)``。若 ``listener`` 省略则返回装饰器。
        """
        if listener is None:
            def decorator(func: Listener) -> Listener:
                self.on(event, func)
                return func
            return decorator
        self._listeners[event].append(_Registration(event, listener))
        return listener

    def once(self, event: str, listener: Listener) -> Listener:
        """注册一个只触发一次的监听器。"""
        self._listeners[event].append(_Registration(event, listener, once=True))
        return listener

    def off(self, event: str, listener: Listener) -> None:
        """移除一个已注册的监听器（若存在）。"""
        for reg in self._listeners.get(event, ()):
            if reg.listener is listener or reg.listener == listener:
                self._remove(reg)
                break

    def _remove(self, reg: _Registration) -> None:
        reg.cancelled = True
        registrations = self._listeners.get(reg.event)
        if registrations is not None:
            try:
                registrations.remove(reg)
            except ValueError:
                return
            if not registrations:
                self._listeners.pop(reg.event, None)

    def clear(self) -> None:
        """清空所有监听器（主要供测试与程序退出时使用）。"""
        # 进行中的 emit() 快照也必须遵守 clear()。
        for registrations in self._listeners.values():
            for reg in registrations:
                reg.cancelled = True
        self._listeners.clear()

    # -- 触发 ------------------------------------------------------------
    async def emit(self, event: str, *args: Any, **kwargs: Any) -> None:
        """触发事件，依次调用所有监听器。

        同步与异步监听器都支持。单个监听器抛出的异常会被记录但不会中断
        其它监听器——事件总线不应因为一个 UI 消费者出错而让 agent 循环崩溃。
        """
        # 复制一份再遍历：监听器可能在执行过程中注册/注销新监听器，
        # 直接遍历活列表会漏掉新注册的、或在迭代中崩溃。
        registrations = list(self._listeners.get(event, ()))
        for reg in registrations:
            if reg.cancelled:
                continue
            # 只有 once 监听器在触发后自我移除；普通监听器必须常驻。
            # 调用前先移除，防止重入 emit() 导致重复触发。
            if reg.once:
                self._remove(reg)
            try:
                result = reg.listener(*args, **kwargs)
                if inspect.isawaitable(result):
                    await result
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(tr('log.3b26bc63d7e5'), event)


class Context:
    """服务容器：插件通过它贡献并撤销服务。

    服务就是一个挂在 ``ctx.<name>`` 上的对象（通常是普通 Python 类实例）。
    :meth:`set` 注册一个服务，:meth:`plugin` 上下文管理器可以在退出时
    批量撤销一个插件产生的所有注册。

    嵌套的 ``plugin()`` 上下文是允许的：内层退出只撤销内层产生的注册。
    """

    def __init__(self, bus: EventBus | None = None) -> None:
        self.bus = bus or EventBus()
        self._services: dict[str, Any] = {}
        # 每个 plugin 作用域对应一个注册栈帧
        self._stack: list[list[tuple[str, Any]]] = []

    # -- 服务注册 --------------------------------------------------------
    def set(self, name: str, service: Any) -> Any:
        """注册（或覆盖）一个服务并返回它。

        插件作用域内覆盖时记录旧服务以便退出恢复；作用域外直接替换。
        """
        # 只有可退出的插件作用域需要撤销历史；根注册不保留旧实例。
        if self._stack:
            frame = self._stack[-1]
            if name in self._services:
                frame.append((name, self._services[name]))
            else:
                frame.append((name, _MISSING))
        self._services[name] = service
        return service

    def get(self, name: str, default: Any = None) -> Any:
        """按名字获取服务；不存在返回 ``default``。"""
        return self._services.get(name, default)

    def require(self, name: str) -> Any:
        """按名字获取服务；不存在抛 :class:`KeyError`。"""
        if name not in self._services:
            raise KeyError(tr('python.events.4e0021c91b7c', p0=f'{name!r}', p1=f'{sorted(self._services)}'))
        return self._services[name]

    def has(self, name: str) -> bool:
        """判断服务是否已注册。"""
        return name in self._services

    def __contains__(self, name: str) -> bool:
        return name in self._services

    @contextmanager
    def plugin(self, name: str = "plugin") -> Iterator["Context"]:
        """服务作用域：块内服务在退出时按逆序恢复；事件订阅由注册者撤销。

        这就是"作用域退出 = 效果回滚"的 Python 表达。
        """
        frame: list[tuple[str, Any]] = []
        self._stack.append(frame)
        try:
            yield self
        finally:
            self._stack.pop()
            # 逆序撤销，恢复到进入作用域前的状态
            for key, previous in reversed(frame):
                if previous is _MISSING:
                    self._services.pop(key, None)
                else:
                    self._services[key] = previous

    def on(self, event: str, listener: Listener | None = None):
        """在当前 Context 的总线上注册事件监听器（等价于 ``ctx.bus.on``）。"""
        return self.bus.on(event, listener)

    # 便捷属性：让 `ctx.tools`、`ctx.bus` 这类访问可读
    def __getattr__(self, name: str) -> Any:
        # 只在正常属性查找失败后调用；避免与内部属性冲突。
        services = self.__dict__.get("_services", {})
        if name in services:
            return services[name]
        raise AttributeError(tr('python.events.ce242aacf063', p0=f'{name!r}'))


class _Missing:
    """哨兵类型，标记"进入作用域前该服务不存在"。"""

    __slots__ = ()


_MISSING = _Missing()


__all__ = ["EventBus", "Context", "Listener"]
