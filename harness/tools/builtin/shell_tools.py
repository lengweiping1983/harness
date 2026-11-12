"""内置 Shell 工具：bash。

安全设计：
* 默认有超时上限，防止模型写出永不返回的命令；
* 限制输出长度，超出部分落盘到临时文件并把路径回传模型；
* 退出码非 0 时返回 ``is_error=True``，让模型知道失败了。

后台执行：
* 传入 ``run_in_background=true`` 时立即返回 ``job_id``，命令在后台继续跑，
  不受本工具超时限制；用 ``job_output`` 收集输出、``job_kill`` 终止。
* 后台分支需要注入 :class:`~harness.tools.builtin.job_tools.JobRegistry`；
  未注入时不声明后台参数，直接调用的后台请求也明确拒绝。
"""

from __future__ import annotations

from harness.i18n import tr
from ..arguments import background_argument, finite_number, string_argument

import asyncio
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ...core.text_encoding import decode_output, output_encoding
from ...llm.types import TextBlock
from ..command_output import STDOUT_CHARS, STDERR_CHARS, command_display, command_progress_key, render_command_output, summarize_command_output, exited_command_result
from ..registry import ToolDefinition, ToolRunContext
from ..process_output import cleanup_process, collect_process
from ..processes import start_process
from ..windows_shell import WINDOWS_SHELL_BRIDGE, windows_shell_command
from ..process_env import execution_env

if TYPE_CHECKING:  # pragma: no cover - 仅用于类型标注
    from .job_tools import JobRegistry

#: 命令默认超时（秒）
DEFAULT_TIMEOUT = 120.0
#: 回传给模型的输出上限
MAX_OUTPUT_CHARS = STDOUT_CHARS
MAX_STDERR_CHARS = STDERR_CHARS
#: 后台作业 label 里命令摘要的最大长度
MAX_LABEL_CHARS = 120


def shell_execution_cwd(args: Any, ctx: ToolRunContext) -> Path:
    """与命令实际启动使用同一目录解析规则，供可信工作树范围核验。"""
    base = Path(ctx.cwd or os.getcwd())
    requested = args.get('cwd')
    if isinstance(requested, str) and requested.strip():
        candidate = Path(requested).expanduser()
        return (candidate if candidate.is_absolute() else base / candidate).resolve()
    return base.resolve()


def _build_child_env(session_id: str) -> dict[str, str]:
    """为所属子进程固定 UTF-8，不修改宿主环境。"""
    return execution_env({"HARNESS_SESSION": session_id})


def _make_label(command: str) -> str:
    """给后台作业生成一行命令摘要。"""
    flat = " ".join(command.split())
    if len(flat) <= MAX_LABEL_CHARS:
        return flat
    return flat[: MAX_LABEL_CHARS - 1] + "\u2026"


def build_shell_tools(
    *,
    timeout: float = DEFAULT_TIMEOUT,
    registry: "JobRegistry | None" = None,
    owned_processes: bool = False,
) -> list[ToolDefinition]:
    """构造 shell 工具。

    :param timeout: 前台执行默认超时（秒）。
    :param registry: 后台作业注册表；提供后 ``bash`` 支持
        ``run_in_background``，并与之产出的 ``job_*`` 工具配套。
    """

    async def bash(args: Any, ctx: ToolRunContext) -> Any:
        """在工作目录里执行一条 shell 命令。"""
        encoding = output_encoding(args.get("output_encoding", "utf-8"))
        command = string_argument(args.get("command"), "command")
        run_timeout = finite_number(args.get("timeout", timeout), "timeout", 0.001, 900)
        background = background_argument(args.get("run_in_background", False), registry is not None)
        if registry is not None:
            registry.assert_ready(ctx.session_id)
        cwd = ctx.cwd or os.getcwd()
        # 模型常按主流 Agent 约定附带 cwd / description：cwd 真实生效
        # （相对路径按会话工作目录解析），description 仅作展示、不影响执行。
        requested_cwd = args.get("cwd")
        if isinstance(requested_cwd, str) and requested_cwd.strip():
            candidate = shell_execution_cwd(args, ctx)
            if not candidate.is_dir():
                return {"execution_state": "not-started", "exit_code": -1, "stdout": "",
                        "stderr": tr('python.shell_tools.9065985f4c79', p0=f'{requested_cwd}'),
                        "error": True}
            cwd = str(candidate)
        env = _build_child_env(ctx.session_id)

        async def launch():
            ctx.check_execution()
            if owned_processes:
                from ..owned_process import spawn_owned
                return await spawn_owned([command], cwd=cwd, env=env, shell=True)
            if os.name == 'nt':
                executable, command_line = windows_shell_command(command, env)
                return await asyncio.create_subprocess_exec(
                    sys.executable, '-I', '-X', 'utf8', '-c', WINDOWS_SHELL_BRIDGE, executable, command_line,
                    cwd=cwd, env=env, stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                    creationflags=0x08000000)
            return await asyncio.create_subprocess_shell(
                command, cwd=cwd, env=env, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, start_new_session=os.name != "nt")

        # -- 后台分支 ----------------------------------------------------
        # 后台作业不设超时，立即返回 job id。
        if background:

            job = await registry.spawn(
                kind="bash",
                label=_make_label(command),
                owner=ctx.session_id,
                spawn=launch,
                output_encoding=encoding,
            )
            return {"job_id": job.id, "job": job.public(), "background": True}

        try:
            proc = await start_process(launch, stop=lambda process: cleanup_process(
                process, ctx, registry=registry, kind="bash", label=_make_label(command), output_encoding=encoding))
        except OSError as exc:
            return {"execution_state": "not-started", "exit_code": -1, "stdout": "", "stderr": tr('python.shell_tools.cb1f63d314bd', p0=f'{exc}'), "error": True}

        stdout_b, stderr_b, returncode, timed_out = await collect_process(
            proc, run_timeout, ctx, registry=registry, kind="bash", label=_make_label(command), output_encoding=encoding)
        if timed_out:
            await cleanup_process(proc, ctx, registry=registry, kind="bash", label=_make_label(command),
                                  stdout=stdout_b, stderr=stderr_b, output_encoding=encoding)
            decoded = decode_output(stdout_b, stderr_b, encoding)
            stdout, stderr = decoded["stdout"], decoded["stderr"]
            return {
                **summarize_command_output(stdout, stderr, stdout_limit=MAX_OUTPUT_CHARS, stderr_limit=MAX_STDERR_CHARS),
                **{key: value for key, value in decoded.items() if key not in ("stdout", "stderr")},
                "exit_code": -1,
                "timeout_note": tr('python.shell_tools.c2086f4a58e2', p0=f'{run_timeout}'),
                "timed_out": True,
                "execution_state": "timed-out",
                "error": True,
            }

        decoded = decode_output(stdout_b, stderr_b, encoding)
        return exited_command_result(decoded, returncode,
                                     stdout_limit=MAX_OUTPUT_CHARS, stderr_limit=MAX_STDERR_CHARS)

    def bash_to_content(args: Any, value: Any) -> list[TextBlock]:
        # 后台：回显 job id 与状态，提示后续怎么收集。
        if value.get("background"):
            job = value.get("job", {})
            lines = [
                tr('python.shell_tools.8f85e3746b95', p0=f"{value['job_id']}"),
                tr('python.shell_tools.d2409d9ca44b'),
                tr('python.shell_tools.8cb3b54db350', p0=f"{job.get('status', 'running')}", p1=f"{job.get('label', '')}"),
            ]
            return [TextBlock(text="\n".join(lines))]

        return [TextBlock(text=render_command_output(value))]

    background_prop: dict[str, Any] = {
        "type": "boolean",
        "description": (
            tr('python.shell_tools.faa885dee6b4')
        ),
    }
    if registry is None:
        # 没有注册表时暴露该参数会误导模型，直接不声明。
        properties: dict[str, Any] = {
            "command": {"type": "string", "description": tr('python.shell_tools.c40d29f11a59')},
            "timeout": {"type": "number", "minimum": 0.001, "maximum": 900, "description": tr('python.shell_tools.1278ec03800e', p0=f'{timeout}')},
            "cwd": {"type": "string", "description": tr('python.shell_tools.4ee6a88ba6c1')},
            "description": {"type": "string", "description": tr('python.shell_tools.8b2c23bee5e1')},
        }
    else:
        properties = {
            "command": {"type": "string", "description": tr('python.shell_tools.c40d29f11a59')},
            "timeout": {"type": "number", "minimum": 0.001, "maximum": 900, "description": tr('python.shell_tools.2a45044a228a', p0=f'{timeout}')},
            "cwd": {"type": "string", "description": tr('python.shell_tools.4ee6a88ba6c1')},
            "description": {"type": "string", "description": tr('python.shell_tools.8b2c23bee5e1')},
            "run_in_background": background_prop,
        }

    properties["output_encoding"] = {"type": "string", "description": tr("encoding.parameter")}
    parameter_keys = {
        ("properties", "output_encoding", "description"): "encoding.parameter",
        ('properties', 'command', 'description'): 'python.shell_tools.c40d29f11a59',
        ('properties', 'timeout', 'description'): (
            'python.shell_tools.1278ec03800e' if registry is None else 'python.shell_tools.2a45044a228a', {'p0': f'{timeout}'}),
        ('properties', 'cwd', 'description'): 'python.shell_tools.4ee6a88ba6c1',
        ('properties', 'description', 'description'): 'python.shell_tools.8b2c23bee5e1',
    }
    if registry is not None:
        parameter_keys[('properties', 'run_in_background', 'description')] = 'python.shell_tools.faa885dee6b4'

    description_key = ('python.shell_tools.7ae8b8577b31' if registry is not None
                       else 'python.shell_tools.foreground_description')

    return [
        ToolDefinition(
            schema={
                "parameter_keys": parameter_keys,
                "description_key": description_key,
                "name": "bash",
                "description": (
                    tr(description_key)
                ),
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": ["command"],
                    "additionalProperties": False,
                },
            },
            execute=bash,
            argument_semantics={'command': 'shell', 'cwd': 'path', 'description': 'text'},
            hook_names=('Bash',),
            execution_cwd=shell_execution_cwd,
            progress_key=command_progress_key,
            to_content=bash_to_content,
            describe=command_display,
            # shell 有副作用，不能并发
            concurrency_safe=False,
            timeout=max(timeout, 900.0) + 15,
            dangerous=True,
        )
    ]


__all__ = ["build_shell_tools"]
