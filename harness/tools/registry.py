"""工具注册表与执行流水线。

**注册表（registry）** 负责：
* 持有 :class:`ToolDefinition`（schema + 执行函数 + 展示元数据）；
* 提供 ``schemas()`` 生成给模型的 :class:`ToolSchema` 列表；
* 按名字解析工具调用。

**执行流水线** 的钩子顺序：

.. code-block:: text

    参数校验 → restriction → pre-execute → guard(可否执行) →
    restriction → around(包裹执行) → restriction → 同步执行许可复核 →
    execute → 物化成内容块（或沿用 ToolResult） → post-execute

每一层都可以被业务方替换或短路：
* ``pre_execute`` 可以直接返回一个终态（跳过执行）；
* ``guard`` 返回拒绝理由会阻止执行并把理由回传模型；
* ``around`` 是上下文管理器，可观测计时/权限；
* ``post_execute`` 可改写结果（脱敏、截断）；
"""

from __future__ import annotations

from harness.i18n import tr

import asyncio
import inspect
import logging
import time
from copy import copy
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from .write_scope import write_scope_denial
from typing import Any, Awaitable, Callable, Iterable, Literal, Mapping

from ..core.errors import (
    ToolArgsError,
    ToolDeniedError,
    ToolError,
    ToolExecutionError,
    
    ToolNotFoundError,
)
from ..core.jsonutil import JsonValue, to_jsonable
from ..llm.types import TextBlock, ToolSchema, ToolUseBlock
from .categories import infer_category
from .schema import validate_or_raise
from .processes import ProcessCleanupError

logger = logging.getLogger(__name__)


def team_goal_receipt_allowed(name: str, args: Any, ctx: Any) -> bool:
    """只读汇总可提交目标回执，不放开其他写操作或恢复检查。"""
    return (name == 'update_goal' and args.get('action') in {'complete', 'blocked'}
            and ctx.services.get('team_goal') is not None
            and ctx.services.get('team_goal_writer') is True
            and not ctx.services.get('recovery_read_only'))


@dataclass(frozen=True, slots=True)
class ToolRunContext:
    """一次工具执行的运行时上下文。"""

    call_id: str
    session_id: str
    cwd: str | None
    turn: int
    step: int
    #: 传给工具的取消信号（``asyncio.Event`` 风格的 ``Cancelled`` 标记）。
    cancelled: bool = False
    #: 工具可通过它读取 agent 提供的额外数据（如 skill 正文、子 agent 句柄）。
    services: dict[str, Any] = field(default_factory=dict)
    #: 单次调用的权限检查，在环绕钩子之后、实际执行之前评估。
    admission_checks: list[Callable[[], str | None]] = field(default_factory=list)
    #: 调用方提供的同步检查，与防护器创建的准入检查不同，
    #: 在 execute_call 设置后仍保留，并仅用于本次调用。
    execution_checks: tuple[Callable[[Any, Any, Any], str | None], ...] = ()
    #: 当前工具入口绑定的同步复核；不进入模型参数，也不共享给其他调用。
    _execution_gate: Callable[[], None] | None = field(default=None, repr=False, compare=False)

    def check_execution(self) -> None:
        """可信工具在延后启动进程等副作用前复用原准入检查。"""
        if self._execution_gate is not None:
            self._execution_gate()

    async def report_output(self, stdout: str = "", stderr: str = "", *, stdout_dropped: int = 0, stderr_dropped: int = 0, encoding_errors: list[dict] | None = None) -> None:
        """报告可恢复的输出尾部快照；最终 ToolResult 仍是权威结果。

        宿主未提供此服务时保持静默，已有 SDK 工具无需改动。
        关联身份由宿主绑定，工具只能提供文本和截断数量。
        """
        callback = self.services.get("tool_output")
        if callback is None:
            return
        limit = 32768
        payload = {"stdout": stdout[-limit:], "stderr": stderr[-limit:],
                   "stdout_dropped": max(0, stdout_dropped) + max(0, len(stdout) - limit),
                   "stderr_dropped": max(0, stderr_dropped) + max(0, len(stderr) - limit)}
        if encoding_errors:
            payload["encoding_errors"] = [dict(issue) for issue in encoding_errors]
        result = callback(payload)
        if inspect.isawaitable(result):
            await result


@dataclass
class ToolResult:
    """工具执行的规范化结果。"""

    #: 给模型看的内容块（已物化）。
    content: list[TextBlock]
    #: 关联的工具调用 id（由注册表在返回前填上）。
    tool_use_id: str = ""
    #: 是否是错误结果。
    is_error: bool = False
    #: 结构化结果，供 UI 渲染（如 diff、todo 列表）。
    display: JsonValue = None
    #: 供业务代码使用的原始结构化数据（不进模型上下文）。
    data: Any = None
    #: 执行耗时（毫秒）。
    duration_ms: int = 0
    #: 可信运行时证据：True 表示发生变化或有界的实时等待，
    #: False 表示确认没有进展，None 保留按副作用判断的行为。
    #: 不能仅因时间戳变化而设置，也不能绕过任务预算。
    progress: bool | None = None
    #: 可信操作和结果标识，包含影响执行的输入。
    #: None 为现有 SDK 工具保留渲染文本的精确比较。
    progress_key: JsonValue = None


class ToolDefinition:
    """一个注册的工具。

    :param schema: 模型可见的 schema。
    :param execute: 同步或异步执行函数，签名 ``(args, ctx) -> Any``。
                    返回值会被 :func:`to_jsonable` 规范化。
    :param to_content: 把返回值转成给模型看的内容块；默认 JSON 化。
    :param describe: 生成 UI 展示数据；只在顶层调用时计算。
    :param concurrency_safe: 是否可与其它工具并行执行。
    :param timeout: 单次执行超时（秒），``None`` 表示不限。
    :param dangerous: 标记该工具需要更高权限（供权限策略使用）。
    :param category: UI 分组用的分类 id；留空按工具名推断。**不进模型请求**。
    :param progress_key: 可信执行代码提供的操作及结果标识，仅用于防空转。
                         必须包含相关执行参数，排除展示路径、耗时等无关元数据；
                         不传时比较完整输入和结果文本。
    :param progress: 可信执行代码确认的进展；True 为实际变化，False 为无变化，
                     None 或不传保留原有 effect 语义，不能由模型参数直接决定。
    :param write_paths: 同步声明本次调用写入的绝对文件路径，供宿主范围校验与写入协调。
    :param execution_cwd: 同步声明本次命令的绝对执行目录，不进入模型参数。
    :param hook_names: 可信执行代码声明的 Hooks 规范名；首项用于输入，其余项参与匹配。
    :param defer_loading: 允许会话在大目录中按需提供此工具的定义，不改变执行权限。
    :param defer_always: 自动模式即使小目录也按需加载，显式全量模式仍直接提供。
    :param discovery_namespace: 可信注册来源提供的发现目录分组，不根据名称猜测副作用。
    :param argument_semantics: 可信实现声明的顶层文本参数用途；不进入模型协议。
    """

    __slots__ = (
        "schema", "defer_loading", "defer_always", "discovery_namespace",
        "argument_semantics",
        "execute",
        "to_content",
        "describe",
        "concurrency_safe",
        "timeout",
        "dangerous",
        "title",
        "category",
        "effect", "approval_required", "cancellable", "idempotent", "progress_key", "progress", "write_paths", "execution_cwd", "hook_names",
    )

    def __init__(
        self,
        schema: ToolSchema,
        execute: Callable[[Any, ToolRunContext], Any],
        *,
        to_content: Callable[[Any, Any], list[TextBlock] | str] | None = None,
        describe: Callable[[Any, Any], JsonValue] | None = None,
        concurrency_safe: bool = True,
        timeout: float | None = 120.0,
        dangerous: bool = False,
        title: str | None = None,
        category: str | None = None,
        effect: str = "unknown",
        approval_required: bool = False,
        cancellable: bool = False,
        idempotent: bool = False,
        progress_key: Callable[[Any, Any], JsonValue] | None = None,
        progress: Callable[[Any, Any], bool | None] | None = None,
        write_paths: Callable[[Any, ToolRunContext], Iterable[Any]] | None = None,
        execution_cwd: Callable[[Any, ToolRunContext], Any] | None = None,
        hook_names: tuple[str, ...] = (),
        defer_loading: bool = False,
        defer_always: bool = False,
        discovery_namespace: str | None = None,
        argument_semantics: Mapping[str, Literal['path', 'text', 'shell', 'code']] | None = None,
    ) -> None:
        # 允许直接传 dict（{name, description, parameters}），内部统一成 ToolSchema。
        # 这样内置工具与业务工具都能用最简洁的字面量写法。
        if isinstance(schema, dict):
            schema = ToolSchema(
                name=schema["name"],
                description=schema.get("description", ""),
                description_key=schema.get("description_key"),
                description_values=schema.get("description_values", {}),
                description_parts=schema.get("description_parts", ()),
                parameter_keys=schema.get("parameter_keys", {}),
                parameters=schema.get("parameters", {"type": "object", "properties": {}}),
            )
        self.schema = schema
        self.defer_loading = defer_loading or defer_always
        self.defer_always = defer_always
        self.discovery_namespace = discovery_namespace
        self.argument_semantics = MappingProxyType(dict(argument_semantics or {}))
        self.execute = execute
        self.to_content = to_content
        self.describe = describe
        self.concurrency_safe = concurrency_safe
        self.timeout = timeout
        self.dangerous = dangerous
        self.title = title or schema.name
        #: 人读目录的分组。不进 ToolSchema，所以不会泄漏到模型请求里。
        self.category = category or infer_category(schema.name)
        self.progress_key = progress_key
        self.progress = progress
        self.write_paths = write_paths
        self.execution_cwd = execution_cwd
        #: 由可信工具实现声明的 Hooks 规范名，不按工具名称或副作用猜测。
        if not isinstance(hook_names, tuple) or not all(isinstance(name, str) and name and len(name) <= 128 for name in hook_names):
            raise ValueError(tr('runtime.validation.32f971ebecff'))
        self.hook_names = hook_names
        if effect not in {"unknown", "read", "workspace_write", "external_write", "execute"}:
            raise ValueError(tr('python.registry.bdf7e4deb857'))
        self.effect = effect
        self.approval_required = approval_required
        self.cancellable = cancellable
        self.idempotent = idempotent


# -- 钩子类型 -------------------------------------------------------------
PreHook = Callable[[Any, ToolRunContext], Awaitable[ToolResult | None] | ToolResult | None]
GuardHook = Callable[[ToolDefinition, Any, ToolRunContext], Awaitable[str | None] | str | None]
ExecutionCheck = Callable[[ToolDefinition, Any, ToolRunContext], str | None]
PostHook = Callable[[ToolDefinition, Any, Any, ToolRunContext, ToolResult], Awaitable[ToolResult] | ToolResult]
AroundHook = Callable[[ToolDefinition, Any, ToolRunContext], Any]


class ToolRegistry:
    """工具注册表 + 执行流水线。

    :param allow: 允许模型调用的工具名白名单；``None`` 表示全部允许。
    :param deny:  黑名单，优先级高于白名单。
    """

    def __init__(self, *, allow: Iterable[str] | None = None, deny: Iterable[str] = ()) -> None:
        from .write_coordination import WriteCoordinator
        self._writes = WriteCoordinator()
        self._tools: dict[str, ToolDefinition] = {}
        self._allow = set(allow) if allow is not None else None
        self._deny = set(deny)
        self._parent_scope: ToolRegistry | None = None
        # -- 流水线钩子 -----------------------------------------------------
        self._pre: list[PreHook] = []
        self._guards: list[GuardHook] = []
        self._restrictions: list[GuardHook] = []
        self._execution_checks: list[ExecutionCheck] = []
        self._post: list[PostHook] = []
        self._around: list[AroundHook] = []
        self._result_observers: list[Any] = []
        #: 工具执行时读取的共享服务（skill 目录、子 agent 等）。
        self.services: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # 注册
    # ------------------------------------------------------------------
    def register(self, tool: ToolDefinition, *, replace: bool = False) -> ToolDefinition:
        """注册一个工具。"""
        if tool.schema.name in self._tools and not replace:
            raise ToolError(tr('python.registry.1d8b23fde8d6', p0=f'{tool.schema.name!r}'))
        self._tools[tool.schema.name] = tool
        return tool

    def tool(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        *,
        concurrency_safe: bool = True,
        timeout: float | None = 120.0,
        dangerous: bool = False,
        effect: str = "unknown",
        approval_required: bool = False,
        cancellable: bool = False,
        idempotent: bool = False,
        argument_semantics: Mapping[str, Literal['path', 'text', 'shell', 'code']] | None = None,
    ) -> Callable[[Callable[[Any, ToolRunContext], Any]], Callable[[Any, ToolRunContext], Any]]:
        """装饰器式注册：把一个 ``(args, ctx)`` 函数变成工具。

        用法::

            @registry.tool("echo", "回显输入", {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            })
            async def echo(args, ctx):
                return {"echo": args["text"]}
        """

        def decorator(func: Callable[[Any, ToolRunContext], Any]) -> Callable[[Any, ToolRunContext], Any]:
            definition = ToolDefinition(
                schema=ToolSchema(name=name, description=description, parameters=parameters),
                execute=func,
                concurrency_safe=concurrency_safe,
                timeout=timeout,
                dangerous=dangerous,
                effect=effect, approval_required=approval_required,
                cancellable=cancellable, idempotent=idempotent,
                argument_semantics=argument_semantics,
            )
            self.register(definition)
            return func

        return decorator

    def unregister(self, name: str) -> None:
        """注销一个工具。"""
        self._tools.pop(name, None)

    # ------------------------------------------------------------------
    # 流水线钩子注册
    # ------------------------------------------------------------------
    def add_pre_hook(self, hook: PreHook) -> None:
        """执行前钩子：返回一个 :class:`ToolResult` 可短路整个执行。"""
        self._pre.append(hook)

    def add_guard(self, guard: GuardHook) -> None:
        """准入门：返回非空字符串表示拒绝理由。"""
        self._guards.append(guard)

    def add_restriction(self, guard: GuardHook) -> None:
        """可信运行时限制，先于扩展钩子和操作审批执行。"""
        self._restrictions.append(guard)

    def add_execution_check(self, check: ExecutionCheck) -> None:
        """全部等待结束后、实际调用前，同步检查权限。

        这些检查只查看宿主当前状态，不能等待或产生副作用。
        异步限制仍通过 add_restriction 支持。
        """
        if inspect.iscoroutinefunction(check) or inspect.iscoroutinefunction(getattr(check, '__call__', None)):
            raise TypeError(tr('python.registry.63fbb2b6239d'))
        self._execution_checks.append(check)

    def add_post_hook(self, hook: PostHook) -> None:
        """执行后钩子：可改写 :class:`ToolResult`。"""
        self._post.append(hook)

    def add_around_hook(self, hook: AroundHook) -> None:
        """包裹钩子：上下文管理器形式，包裹真正的 execute。"""
        self._around.append(hook)

    def add_result_observer(self, observer: Any) -> None:
        """观察所有规范化结果，包括直接返回的结果和拒绝。

        观察者可以追加反馈，但必须保留实际工具结果。
        取消和未确认的进程清理不能转换为工具结果。
        """
        self._result_observers.append(observer)

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def get(self, name: str) -> ToolDefinition:
        """按名字取工具定义；不存在抛 :class:`ToolNotFoundError`。"""
        tool = self._tools.get(name)
        if tool is None:
            available = ", ".join(self.names()) or tr('python.registry.f4b39d9b4542')
            raise ToolNotFoundError(tr('python.registry.8ad2d40b7fc2', p0=f'{name!r}', p1=f'{available}'), tool=name)
        return tool

    def has(self, name: str) -> bool:
        return name in self._tools

    def names(self) -> list[str]:
        """已应用白／黑名单的候选工具名；模型工作集由 Agent 决定。"""
        return sorted(n for n in self._tools if self._is_allowed(n))

    def schemas(self) -> list[ToolSchema]:
        """生成当前授权候选定义；Agent 可继续按运行范围和加载策略收敛。"""
        return [self._tools[name].schema for name in self.names()]

    def _is_allowed(self, name: str) -> bool:
        if self._parent_scope is not None and not self._parent_scope._is_allowed(name):
            return False
        if name in self._deny:
            return False
        if self._allow is not None and name not in self._allow:
            return False
        return True

    def restricted(self, allow: Iterable[str]) -> ToolRegistry:
        """可信宿主创建只收权的工具视图；注册、钩子、服务及写入协调仍归原注册表。"""
        if isinstance(allow, (str, bytes)):
            raise ToolError(tr('tool.scope.invalid'))
        try:
            names = set()
            for index, name in enumerate(allow):
                if (index >= 4096 or not isinstance(name, str) or not name.strip()
                        or '\0' in name or len(name.encode('utf-8')) > 256):
                    raise ValueError
                names.add(name)
        except (TypeError, ValueError, UnicodeError) as exc:
            raise ToolError(tr('tool.scope.invalid')) from exc
        # 可变执行设施有意共享，避免复制权限决策、锁或资源所有权。
        scoped = copy(self)
        scoped._parent_scope = self
        scoped._allow = frozenset(names)
        scoped._deny = frozenset()
        return scoped

    # ------------------------------------------------------------------
    # 执行
    # ------------------------------------------------------------------
    async def execute_call(self, call: ToolUseBlock, ctx: ToolRunContext) -> ToolResult:
        # 批量调用可共享服务，但调用身份及准入闭包必须独立。
        ctx = replace(ctx, call_id=call.id, admission_checks=[], _execution_gate=None)
        # 运行参数独立于模型消息；Hooks 重写不得篡改已持久化的原始请求。
        from ..core.jsonutil import JsonConversionError
        try:
            call = replace(call, input=to_jsonable(call.input))
        except JsonConversionError:
            # 非 JSON 参数仍交给既有 schema 校验，保留其错误契约。
            pass
        result = await self._execute_call(call, ctx)
        for observer in self._result_observers:
            outcome = observer(call, ctx, result)
            if inspect.isawaitable(outcome):
                await outcome
        return result

    async def _execute_call(self, call: ToolUseBlock, ctx: ToolRunContext) -> ToolResult:
        """执行一次工具调用，走完整流水线。

        任何失败都被规范化成 ``is_error=True`` 的结果回传给模型——
        让模型有机会看到错误并自我纠正，比让整个循环崩溃更好。
        """
        tool_name = call.name
        # 准入闭包由本次 guard 生成；复用上下文时不得携带上次审批。
        ctx.admission_checks.clear()
        started = time.monotonic()
        result: ToolResult

        try:
            definition = self.get(tool_name)
        except ToolNotFoundError:
            available = self.names()
            return self._error_result(
                tr('python.registry.eca70f61b33c', p0=f'{tool_name!r}', p1=f"{', '.join(available) or tr('common.none')}"), call.id, started,
                progress_key=['tool/not-found', tool_name, available])

        # 仅过滤工具结构无法约束过期或虚构的调用。
        # 在验证、钩子和审批之前拒绝，因为这些操作也可能产生副作用。
        if not self._is_allowed(tool_name):
            return self._error_result(
                tr('python.registry.1a46d1ca96bc', p0=f'{tool_name!r}'), call.id, started,
                progress_key=['tool/disabled', tool_name])

        # -- 1) 参数校验 -------------------------------------------------
        try:
            args = validate_or_raise(call.input, dict(definition.schema.parameters), tool=tool_name)
        except ToolArgsError as exc:
            result = self._error_result(f'[{exc.code}] {exc.message}', call.id, started)
            result.display.update(exc.to_dict())
            return result

        # 恢复检查与只读任务是可信运行时限制，不是模型参数，
        # 在扩展钩子和可复用审批之前应用。
        if (ctx.services.get('recovery_read_only') or ctx.services.get('task_read_only')) and not team_goal_receipt_allowed(definition.schema.name, args, ctx) and (definition.effect != 'read' or
                tool_name == 'browser' and args.get('action') not in {'navigate', 'observe', 'screenshot', 'tabs', 'diagnostics', 'options'}):
            key = 'python.registry.9036ea01262c' if ctx.services.get('recovery_read_only') else 'agent.task_read_only.denied'
            result = self._error_result(tr(key), call.id, started)
            result.display['recovery_restricted' if ctx.services.get('recovery_read_only') else 'task_restricted'] = True
            return result

        scope_denial = write_scope_denial(definition, args, ctx)
        if scope_denial:
            return self._error_result(scope_denial, call.id, started)

        for restriction in self._restrictions:
            try:
                reason = restriction(definition, args, ctx)
                if inspect.isawaitable(reason):
                    reason = await reason
                if reason:
                    return self._error_result(tr('python.registry.f2b86e87a36f', p0=f'{reason}'), call.id, started)
            except ProcessCleanupError:
                raise
            except Exception as exc:
                return self._error_result(tr('python.registry.2eaf049e080e', p0=f'{exc}'), call.id, started)

        # -- 2) pre-execute 钩子（可短路） -------------------------------
        try:
            for hook in self._pre:
                outcome = hook(args, ctx)
                if inspect.isawaitable(outcome):
                    outcome = await outcome
                if outcome is not None:
                    outcome.tool_use_id = call.id
                    return outcome
        except ProcessCleanupError:
            raise
        except Exception as exc:
            return self._error_result(tr('python.registry.801e2631d02b', p0=f'{exc}'), call.id, started)

        scope_denial = write_scope_denial(definition, args, ctx)
        if scope_denial:
            return self._error_result(scope_denial, call.id, started)

        # -- 3) guard 门禁 ------------------------------------------------
        for guard in self._guards:
            try:
                reason = guard(definition, args, ctx)
                if inspect.isawaitable(reason):
                    reason = await reason
                if reason:
                    return self._error_result(tr('python.registry.f2b86e87a36f', p0=f'{reason}'), call.id, started)
            except ProcessCleanupError:
                raise
            except Exception as exc:
                return self._error_result(tr('python.registry.4b6e6a4afb3b', p0=f'{exc}'), call.id, started)

        # 审批/扩展钩子可能挂起；期间用户能进入计划模式。
        # 再检查当前限制，不能复用等待之前的执行许可。
        for restriction in self._restrictions:
            try:
                reason = restriction(definition, args, ctx)
                if inspect.isawaitable(reason):
                    reason = await reason
                if reason:
                    return self._error_result(tr('python.registry.f2b86e87a36f', p0=f'{reason}'), call.id, started)
            except ProcessCleanupError:
                raise
            except Exception as exc:
                return self._error_result(tr('python.registry.2eaf049e080e', p0=f'{exc}'), call.id, started)

        # -- 4) 环绕钩子与执行 ----------------------------------------------
        try:
            value = await self._run_execute(definition, args, ctx)
            # 工具可以直接返回一个完整的 ToolResult（例如 skill 工具需要
            # 携带 display/data 等结构化信息），此时跳过后续物化。
            if isinstance(value, ToolResult):
                value.tool_use_id = call.id
                value.duration_ms = int((time.monotonic() - started) * 1000)
        except ToolDeniedError as exc:
            return self._error_result(str(exc), call.id, started)
        except asyncio.TimeoutError:
            return self._error_result(tr('python.registry.7e453dc4d903', p0=f'{tool_name}', p1=f'{definition.timeout}'), call.id, started)
        except asyncio.CancelledError:
            raise
        except ProcessCleanupError:
            # 未确认的前台清理必须终止回合，不能作为普通工具错误继续调用模型。
            raise
        except ToolExecutionError as exc:
            # 预期的文件系统或校验拒绝作为配对工具结果返回，不属于运行时崩溃。
            # 结构化诊断与可信的观察／撤销证据一并保留。
            logger.info(tr('log.9148c4ec83b5'), tool_name, exc)
            file_error = exc.code.startswith('FS_')
            result = self._error_result(tr('python.registry.5c7e277177d5', p0=f'{tool_name}',
                p1=f'[{exc.code}] {exc.message}' if file_error else str(exc)), call.id, started)
            if file_error:
                result.display.update(exc.to_dict())
            proof = getattr(exc, 'file_observation', None)
            if proof is not None:
                result.data = {'_fs_observation': proof}
            return result
        except Exception as exc:
            logger.exception(tr('log.9c9855054bd6'), tool_name)
            return self._error_result(tr('python.registry.5c7e277177d5', p0=f'{tool_name}', p1=f'{exc}'), call.id, started)

        # -- 5) 物化成内容块 ---------------------------------------------
        result = value if isinstance(value, ToolResult) else self._materialize(definition, args, value, call.id, started)

        # -- 6) post-execute 钩子 -----------------------------------------
        for hook in self._post:
            try:
                replaced = hook(definition, args, value, ctx, result)
                if inspect.isawaitable(replaced):
                    replaced = await replaced
                if replaced is not None:
                    result = replaced
            except ProcessCleanupError:
                raise
            except Exception as exc:
                return self._error_result(tr('python.registry.ec1ff343b03d', p0=f'{exc}'), call.id, started)

        result.duration_ms = int((time.monotonic() - started) * 1000)
        result.tool_use_id = call.id
        return result

    async def _run_execute(self, definition: ToolDefinition, args: Any, ctx: ToolRunContext) -> Any:
        """在 around 钩子包裹下执行工具本体（含超时控制）。"""

        def _check_current() -> None:
            if not self._is_allowed(definition.schema.name) or self._tools.get(definition.schema.name) is not definition:
                raise ToolDeniedError(tr('python.registry.0987ffdcf4e0'))
            if (ctx.services.get('recovery_read_only') or ctx.services.get('task_read_only')) and not team_goal_receipt_allowed(definition.schema.name, args, ctx) and (definition.effect != 'read' or
                    definition.schema.name == 'browser' and args.get('action') not in {'navigate', 'observe', 'screenshot', 'tabs', 'diagnostics', 'options'}):
                raise ToolDeniedError(tr('python.registry.8cd36df945d6' if ctx.services.get('recovery_read_only') else 'agent.task_read_only.denied'))
            scope_denial = write_scope_denial(definition, args, ctx)
            if scope_denial:
                raise ToolDeniedError(scope_denial)

        def _admit(locked_paths) -> None:
            _check_current()
            for check in (*ctx.execution_checks, *self._execution_checks):
                reason = check(definition, args, ctx)
                if inspect.isawaitable(reason):
                    if inspect.iscoroutine(reason):
                        reason.close()
                    raise ToolDeniedError(tr('python.registry.748e62a6f0cc'))
                if reason is not None and not isinstance(reason, str):
                    raise ToolDeniedError(tr('python.registry.4f0617e1b4ef'))
                if reason:
                    raise ToolDeniedError(tr('python.registry.f2b86e87a36f', p0=f'{reason}'))
            # 这些检查同步执行，调用前不存在调度间隙。
            for check in ctx.admission_checks:
                reason = check()
                if reason:
                    raise ToolDeniedError(reason)
            scope_denial = write_scope_denial(definition, args, ctx)
            if scope_denial:
                raise ToolDeniedError(scope_denial)
            self._writes.check(locked_paths, definition, args, ctx)

        async def _execute(locked_paths) -> Any:
            _check_current()
            for restriction in self._restrictions:
                reason = restriction(definition, args, ctx)
                if inspect.isawaitable(reason):
                    reason = await reason
                if reason:
                    raise ToolDeniedError(tr('python.registry.f2b86e87a36f', p0=f'{reason}'))
            gate = lambda: _admit(locked_paths)
            gate()
            execution_ctx = replace(ctx, _execution_gate=gate)
            outcome = definition.execute(args, execution_ctx)
            if inspect.isawaitable(outcome):
                return await outcome
            return outcome

        async def _inner() -> Any:
            async with self._writes.hold(definition, args, ctx) as paths:
                # 等待路径后，执行入口重新核验启停、权限与准入闭包。
                return await _execute(paths)

        return await self._run_with_around(definition, args, ctx, _inner)

    async def _run_with_around(
        self,
        definition: ToolDefinition,
        args: Any,
        ctx: ToolRunContext,
        inner: Callable[[], Awaitable[Any]],
    ) -> Any:
        """显式实现 around 钩子的嵌套（进入顺序正序，退出逆序）。"""
        from contextlib import AsyncExitStack
        async with AsyncExitStack() as stack:
            for hook in self._around:
                cm = hook(definition, args, ctx)
                await stack.enter_async_context(cm)
            coro = inner()
            if definition.timeout is not None:
                return await asyncio.wait_for(coro, timeout=definition.timeout)
            return await coro

    def _materialize(
        self,
        definition: ToolDefinition,
        args: Any,
        value: Any,
        call_id: str,
        started: float,
    ) -> ToolResult:
        """把执行返回值规范化成 :class:`ToolResult`。"""
        # 1) 计算 UI 展示数据（只对顶层调用计算）
        display: JsonValue = None
        if definition.describe is not None:
            try:
                display = to_jsonable(definition.describe(args, value))
            except Exception:
                logger.exception(tr('log.dc808f1cce3b'), definition.schema.name)
                display = None

        # 2) 计算给模型看的内容
        content: list[TextBlock]
        if definition.to_content is not None:
            rendered = definition.to_content(args, value)
            if isinstance(rendered, str):
                content = [TextBlock(text=rendered)]
            else:
                content = list(rendered)
        else:
            # 默认：JSON 序列化
            import json

            try:
                text = json.dumps(to_jsonable(value), ensure_ascii=False, indent=2)
            except Exception:
                text = str(value)
            content = [TextBlock(text=text)]

        # 工具返回值里的 error 标志（例如 shell 的非零退出码）会被提升为
        # is_error，让模型明确知道"这次执行没成功"，而不是只看到一段文本。
        is_error = False
        if isinstance(value, dict):
            is_error = bool(value.get("error", False))

        progress_key = None
        if definition.progress_key is not None:
            try:
                progress_key = to_jsonable(definition.progress_key(args, value))
            except Exception:
                # 证据提取不能丢弃已完成操作，
                # 也不能将其变成诱导重新执行的错误。
                logger.exception(tr('log.de08225bb452'), definition.schema.name)
        progress = None
        if definition.progress is not None:
            try:
                evidence = definition.progress(args, value)
                if isinstance(evidence, bool):
                    progress = evidence
            except Exception:
                logger.exception(tr('log.5515dea70cac'), definition.schema.name)
        return ToolResult(
            tool_use_id=call_id,
            content=content,
            is_error=is_error,
            display=display,
            data=value,
            progress_key=progress_key,
            progress=progress,
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    @staticmethod
    def _error_result(message: str, call_id: str, started: float, *, progress_key: JsonValue = None) -> ToolResult:
        """构造一个错误结果。"""
        return ToolResult(
            content=[TextBlock(text=message)],
            tool_use_id=call_id,
            is_error=True,
            display={"error": message},
            data=None,
            progress_key=progress_key,
            duration_ms=int((time.monotonic() - started) * 1000),
        )


    def __repr__(self) -> str:
        return f"<ToolRegistry tools={len(self._tools)} visible={len(self.names())}>"


__all__ = [
    "ToolDefinition",
    "ToolRegistry",
    "ToolRunContext",
    "ToolResult",
]
