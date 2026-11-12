"""workflow 工具。

模型侧看到的只有一个工具 ``workflow``：把一段**普通 JavaScript** 脚本交给
PTC（process, tool, code）沙箱执行，脚本里用 ``agent()`` / ``pipeline()`` /
``parallel()`` 把工作扇出给大量子代理，最后 ``return`` 一个值作为工具结果。

执行边界：

* 脚本引擎 ``workflow_guest.mjs`` 是一份固定副本，跑在 ``node:vm`` 创建的
  空上下文里，没有文件系统、网络、定时器、Node API。工作全部由子代理做。
* 宿主侧在 :mod:`.workflow_host`：进程生命周期、并发上限、JSON Schema 子集
  校验、进度事件转发都在那里。本模块只负责**模型可见的契约**：参数 schema、
  meta 校验、run 生命周期、前后台分流、结果渲染。
* ``run_in_background: true`` 不等待：把整轮注册成 ``ctx.jobs`` 里的一个
  ``workflow`` 作业，立刻返回 job id；返回值随作业完成通知一起到达。
* 非 ``completed`` 的结束原因（cancelled / error）**抛异常**而不是返回部分
  结果，工具结果里不该出现看起来成功但其实被取消的返回值。
"""

from __future__ import annotations

from harness.i18n import tr

import asyncio
import contextlib
import itertools
import json
import signal as signal_mod
from typing import Any, Mapping

from ...core.errors import ToolExecutionError
from ..registry import ToolDefinition, ToolRunContext
from .workflow_host import WorkflowGuest, WorkflowLimits

#: 渲染给模型的返回值上限（字符）。超出就截断并注明还剩多少。
MAX_RESULT_CHARS = 50_000

_META_FIELDS = ("name", "description", "whenToUse", "phases")
_PHASE_FIELDS = ("title", "detail", "provider", "model")


def _agent_count(agents_started: int) -> str:
    return tr('workflow.agent_one' if agents_started == 1 else 'workflow.agent_many', count=agents_started)


def render_result(
    name: str,
    agents_started: int,
    value: Any,
    max_chars: int = MAX_RESULT_CHARS,
) -> str:
    """把脚本返回值渲染成给模型看的文本（超长截断并注明剩余字符数）。"""
    body = tr('workflow.completed', name=name, agents=_agent_count(agents_started))
    payload = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    rendered = body + payload
    if len(rendered) <= max_chars:
        return rendered
    return (
        rendered[:max_chars]
        + tr('workflow.truncated', count=len(rendered) - max_chars)
    )


def stop_reason_error(result: Mapping[str, Any]) -> str | None:
    """结束原因 -> 错误文案；``completed`` 返回 ``None``。"""
    status = str(result.get("stopReason", "error"))
    error = result.get("error")
    if status == "completed":
        return None
    if status == "cancelled":
        return tr('workflow.cancelled', error=error)
    return tr('workflow.failed', error=error or tr('workflow.unknown_error'))


def job_outcome_of(
    result: Mapping[str, Any],
    name: str,
    max_chars: int = MAX_RESULT_CHARS,
) -> dict[str, Any]:
    """后台作业的最终结果。"""
    status = str(result.get("stopReason", "error"))
    if status == "completed":
        started = int(result.get("agentsStarted", 0) or 0)
        return {
            "status": "completed",
            "detail": _agent_count(started),
            "result": render_result(name, started, result.get("value"), max_chars),
        }
    if status == "cancelled":
        return {"status": "killed"}
    return {"status": "failed", "detail": result.get("error") or tr('workflow.unknown_error')}


def validate_meta(value: Any) -> dict[str, Any]:
    """校验并**规范化** meta。

    meta 是普通 JSON 数据而非代码；这里逐条列出违规，模型能照着改。
    返回的是从已校验字段重建的新对象，绝不直接沿用调用方的引用。
    """
    if not isinstance(value, Mapping):
        raise ToolExecutionError(tr('runtime.validation.59ad29510962'))
    violations: list[str] = []
    for key in value:
        if key not in _META_FIELDS:
            violations.append(
                tr('workflow.meta.unknown_field', field=key)
            )
    name = value.get("name")
    if not isinstance(name, str) or not name:
        violations.append(tr('workflow.meta.name'))
    description = value.get("description")
    if not isinstance(description, str) or not description:
        violations.append(tr('workflow.meta.description'))
    when_to_use = value.get("whenToUse")
    if when_to_use is not None and not isinstance(when_to_use, str):
        violations.append(tr('workflow.meta.when'))

    phases: list[dict[str, Any]] = []
    raw_phases = value.get("phases")
    if raw_phases is not None:
        if not isinstance(raw_phases, list):
            violations.append(tr('workflow.meta.phases'))
        else:
            for index, entry in enumerate(raw_phases):
                prefix = f"meta.phases[{index}]"
                if not isinstance(entry, Mapping):
                    violations.append(tr('workflow.meta.object', path=prefix))
                    continue
                for key in entry:
                    if key not in _PHASE_FIELDS:
                        violations.append(tr('workflow.meta.unknown_phase', path=prefix, field=key))
                title = entry.get("title")
                if not isinstance(title, str) or not title:
                    violations.append(tr('workflow.meta.title', path=prefix))
                for optional in ("detail", "provider", "model"):
                    item = entry.get(optional)
                    if item is not None and not isinstance(item, str):
                        violations.append(tr('workflow.meta.string', path=prefix, field=optional))
                if isinstance(title, str) and title:
                    phases.append(
                        {
                            key: entry[key]
                            for key in _PHASE_FIELDS
                            if entry.get(key) is not None
                        }
                    )

    if violations:
        raise ToolExecutionError(tr('workflow.meta.invalid', violations=tr('common.error_separator').join(violations)))
    meta: dict[str, Any] = {"name": name, "description": description}
    if when_to_use is not None:
        meta["whenToUse"] = when_to_use
    if raw_phases is not None:
        meta["phases"] = phases
    return meta


def _parameters(enable_run_in_background: bool) -> dict[str, Any]:
    """模型可见的参数 schema。"""
    properties: dict[str, Any] = {
        "script": {
            "type": "string",
            "description": (
                tr('owned.workflow_tools.dd216cb70782')
            ),
        },
        "meta": {
            "type": "object",
            "additionalProperties": True,
            "description": tr('owned.workflow_tools.3cd9136567a6'),
            "properties": {
                "name": {
                    "type": "string",
                    "description": tr('owned.workflow_tools.7f450653a095'),
                },
                "description": {
                    "type": "string",
                    "description": tr('owned.workflow_tools.e538d0692cb9'),
                },
                "whenToUse": {
                    "type": "string",
                    "description": tr('owned.workflow_tools.25e6390a82d3'),
                },
                "phases": {
                    "type": "array",
                    "description": tr('owned.workflow_tools.8fb2c07d8e19'),
                    "items": {
                        "type": "object",
                        "additionalProperties": True,
                        "properties": {
                            "title": {
                                "type": "string",
                                "description": (
                                    tr('owned.workflow_tools.3c7d2356bdd3')
                                ),
                            },
                            "detail": {
                                "type": "string",
                                "description": tr('owned.workflow_tools.a17ac798a4db'),
                            },
                            "provider": {
                                "type": "string",
                                "description": (
                                    tr('owned.workflow_tools.c460fb56485d')
                                ),
                            },
                            "model": {
                                "type": "string",
                                "description": (
                                    tr('owned.workflow_tools.823202cf3371')
                                ),
                            },
                        },
                        "required": ["title"],
                    },
                },
            },
            "required": ["name", "description"],
        },
        "args": {
            "type": "object",
            "additionalProperties": True,
            "description": (
                tr('owned.workflow_tools.ad9e67288c53')
            ),
        },
    }
    if enable_run_in_background:
        properties["run_in_background"] = {
            "type": "boolean",
            "description": (
                tr('owned.workflow_tools.2b2eae90c0c3')
            ),
        }
    return {
        "type": "object",
        "properties": properties,
        "required": ["script", "meta"],
        "additionalProperties": False,
    }


class _WorkflowProcess:
    """把一次 workflow 运行包装成 job 作业能管理的「子进程」。

    :class:`~harness.tools.builtin.job_tools.JobRegistry` 只认一套最小的
    进程契约（字节流 + ``wait()`` + ``terminate()``）。适配器不暴露 Node PID，
    由工作流宿主管理 Node 和子代理；保持同一契约后，``job_output`` / ``job_kill`` / ``job_list`` 就能和
    shell 后台作业共用一套心智模型。
    """

    __slots__ = (
        "_guest",
        "stdout",
        "stderr",
        "returncode",
        "result",
        "_transport",
        "_cancelled",
        "_task",
        "_on_result",
    )

    def __init__(self, guest: WorkflowGuest, on_result=None) -> None:
        self._guest = guest
        self.stdout = None  # 结果文本由 runner 直接写进 job.stdout
        self.stderr = None
        self.returncode: int | None = None
        #: 最近一次 ``guest.run()`` 的原始结果（``wait()`` 只返回退出码，
        #: 作业汇需要的是这份字典，所以单独留一份给调用方取）。
        self.result: dict[str, Any] | None = None
        self._transport = None
        self._cancelled = False
        self._task = None
        self._on_result = on_result

    async def wait(self) -> int:
        # 泵与清理可能同时等待，同一运行只创建一个任务。
        if self._task is None:
            if self._cancelled:
                self.returncode = -signal_mod.SIGTERM
                return self.returncode
            self._task = asyncio.create_task(self._run())
        try:
            return await asyncio.shield(self._task)
        except asyncio.CancelledError:
            if self._cancelled and self._task.done():
                self.returncode = -signal_mod.SIGTERM
                return self.returncode
            raise

    async def _run(self) -> int:
        try:
            result = await self._guest.run()
        except asyncio.CancelledError:
            self.returncode = -signal_mod.SIGTERM
            return self.returncode
        except Exception as exc:  # noqa: BLE001 - 失败要以作业状态回报给模型
            self.returncode = 1
            raise exc
        self.result = result
        if self._cancelled:
            self.returncode = -signal_mod.SIGTERM
        elif str(result.get("stopReason")) == "completed":
            self.returncode = 0
        else:
            self.returncode = 1
        if self._on_result is not None:
            self._on_result(result)
        return self.returncode

    def terminate(self) -> None:
        if self._cancelled:
            return
        self._cancelled = True
        if self._task is not None and not self._task.done():
            self._task.cancel()

    def kill(self) -> None:
        self.terminate()

    def send_signal(self, sig: int) -> None:
        self.terminate()


def _safe_meta_name(args: Any) -> str:
    """渲染用的工作流名；meta 非法时也别把渲染本身搞崩。"""
    if isinstance(args, Mapping):
        meta = args.get("meta")
        if isinstance(meta, Mapping):
            name = meta.get("name")
            if isinstance(name, str) and name:
                return name
    return "workflow"


class _JobSink:
    """后台作业的输出汇：runner 早于 ``job`` 句柄存在，所以先攒后绑定。

    :meth:`JobRegistry.spawn` 会立刻 ``create_task`` 起 ``_pump``，而作业句柄
    只有 ``spawn`` 返回后才有。这里用「未绑定时先缓存」消掉这个窗口，
    免得极端调度下把结果写丢。
    """

    __slots__ = ("_job", "_pending")

    def __init__(self) -> None:
        self._job: Any = None
        self._pending: list[Mapping[str, Any]] = []

    def bind(self, job: Any) -> None:
        self._job = job
        pending, self._pending = self._pending, []
        for outcome in pending:
            self._flush(outcome)

    def write(self, outcome: Mapping[str, Any]) -> None:
        if self._job is None:
            self._pending.append(outcome)
            return
        self._flush(outcome)

    def _flush(self, outcome: Mapping[str, Any]) -> None:
        job = self._job
        if job is None:  # pragma: no cover - bind 之后不会再走这条路
            return
        status = str(outcome.get("status", "failed"))
        stdout = getattr(job, "stdout", None)
        result = outcome.get("result")
        if stdout is not None and isinstance(result, str) and result:
            stdout.append_text(result + "\n")
        detail = outcome.get("detail")
        if detail:
            job.detail = str(detail)
        if status == "killed":
            job.status = "killed"
        elif status == "failed":
            # 让作业如实反映 workflow 失败，而不是被通用的退出码洗成 completed。
            job.status = "failed"


def build_workflow_tools(
    manager: Any,
    *,
    jobs: Any = None,
    bus: Any = None,
    cwd: str | None = None,
    tool_name: str = "workflow",
    max_result_chars: int = MAX_RESULT_CHARS,
    enable_run_in_background: bool = True,
    max_concurrent_agents: int | None = None,
    max_total_agents: int | None = None,
    max_items_per_call: int | None = None,
    sync_timeout_ms: int | None = None,
) -> list[ToolDefinition]:
    """构造 ``workflow`` 工具，绑定到给定的 :class:`SubagentManager`。

    ``jobs`` 存在且 ``enable_run_in_background`` 为真时，模型可以传
    ``run_in_background: true`` 把整轮注册成后台作业；否则该参数根本不出现在
    schema 里，避免模型用一个永远报错的选项。
    """

    limits = WorkflowLimits.coerce(
        max_concurrent_agents=max_concurrent_agents,
        max_items_per_call=max_items_per_call,
        max_total_agents=max_total_agents,
        sync_timeout_ms=sync_timeout_ms,
    )
    allow_background = bool(enable_run_in_background and jobs is not None)
    run_counter = itertools.count(1)

    def _next_run_id() -> str:
        return f"run_{next(run_counter)}"

    def _make_guest(
        args: Mapping[str, Any],
        meta: Mapping[str, Any],
        run_id: str,
        ctx: ToolRunContext,
    ) -> WorkflowGuest:
        loop = asyncio.get_running_loop()

        def on_progress(kind: str, event: Mapping[str, Any]) -> None:
            if bus is None:
                return
            payload = {"runId": run_id, "workflow": meta.get("name"), "kind": kind}
            for key, value in event.items():
                payload.setdefault(key, value)
            with contextlib.suppress(RuntimeError):
                loop.create_task(bus.emit("workflow/progress", payload))

        return WorkflowGuest(
            manager,
            script=str(args.get("script", "") or ""),
            meta=meta,
            args=args.get("args"),
            limits=limits,
            cwd=getattr(ctx, "cwd", None) or cwd,
            on_progress=on_progress,
            parent_session_id=ctx.session_id,
        )

    async def workflow(args: Any, ctx: ToolRunContext) -> dict[str, Any]:
        """执行一段编排脚本，把子代理扇出出去。"""
        meta = validate_meta(args.get("meta"))
        background = args.get("run_in_background") is True
        if background and not allow_background:
            raise ToolExecutionError(tr('workflow.background.unavailable'))
        run_id = _next_run_id()
        guest = _make_guest(args, meta, run_id, ctx)

        if background:
            sink = _JobSink()

            async def _spawn() -> _WorkflowProcess:
                ctx.check_execution()
                # 先把可取消句柄交给作业表，再等待工作流执行。
                return _WorkflowProcess(guest, lambda outcome: sink.write(job_outcome_of(outcome, meta["name"], max_result_chars)))

            job = await jobs.spawn(
                kind="workflow",
                label=meta["name"],
                owner=ctx.session_id,
                spawn=_spawn,
            )
            sink.bind(job)
            return {
                "kind": "background",
                "jobId": job.id,
                "runId": run_id,
                "agentsStarted": None,
            }

        result = await guest.run()
        error = stop_reason_error(result)
        if error is not None:
            raise ToolExecutionError(error)
        agents_started = int(result.get("agentsStarted", 0) or 0)
        return {
            "kind": "foreground",
            "runId": run_id,
            "agentsStarted": agents_started,
            "result": result.get("value"),
        }

    def to_content(args: Any, value: Any) -> str:
        name = _safe_meta_name(args)
        if isinstance(value, Mapping) and value.get("kind") == "background":
            return tr('workflow.background', name=name, job=value.get('jobId'), run=value.get('runId'))
        started = 0
        if isinstance(value, Mapping):
            started = int(value.get("agentsStarted", 0) or 0)
        return render_result(
            name, started, value.get("result") if isinstance(value, Mapping) else value,
            max_result_chars,
        )

    def describe(args: Any, value: Any) -> dict[str, Any]:
        return {
            "card": "generic",
            "title": f"workflow: {_safe_meta_name(args)}",
            "rawInput": args.get("script") if isinstance(args, Mapping) else None,
        }

    parameter_keys = {
        ('properties', 'script', 'description'): 'owned.workflow_tools.dd216cb70782',
        ('properties', 'meta', 'description'): 'owned.workflow_tools.3cd9136567a6',
        ('properties', 'meta', 'properties', 'name', 'description'): 'owned.workflow_tools.7f450653a095',
        ('properties', 'meta', 'properties', 'description', 'description'): 'owned.workflow_tools.e538d0692cb9',
        ('properties', 'meta', 'properties', 'whenToUse', 'description'): 'owned.workflow_tools.25e6390a82d3',
        ('properties', 'meta', 'properties', 'phases', 'description'): 'owned.workflow_tools.8fb2c07d8e19',
        ('properties', 'meta', 'properties', 'phases', 'items', 'properties', 'title', 'description'): 'owned.workflow_tools.3c7d2356bdd3',
        ('properties', 'meta', 'properties', 'phases', 'items', 'properties', 'detail', 'description'): 'owned.workflow_tools.a17ac798a4db',
        ('properties', 'meta', 'properties', 'phases', 'items', 'properties', 'provider', 'description'): 'owned.workflow_tools.c460fb56485d',
        ('properties', 'meta', 'properties', 'phases', 'items', 'properties', 'model', 'description'): 'owned.workflow_tools.823202cf3371',
        ('properties', 'args', 'description'): 'owned.workflow_tools.ad9e67288c53',
    }
    if allow_background:
        parameter_keys[("properties", "run_in_background", "description")] = "owned.workflow_tools.2b2eae90c0c3"

    return [
        ToolDefinition(
            schema={
                "description_key": "owned.workflow_tools.2fb24d5fc017",
                "parameter_keys": parameter_keys,
                "name": tool_name,
                "description": tr('owned.workflow_tools.2fb24d5fc017'),
                "parameters": _parameters(allow_background),
            },
            execute=workflow,
            argument_semantics={'script': 'code'},
            to_content=to_content,
            describe=describe,
            concurrency_safe=False,
            timeout=None,
            title=tool_name,
        ),
    ]


__all__ = [
    "MAX_RESULT_CHARS",
    "build_workflow_tools",
    "job_outcome_of",
    "render_result",
    "stop_reason_error",
    "validate_meta",
]
