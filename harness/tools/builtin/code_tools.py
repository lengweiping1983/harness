"""内置代码执行工具：run_code。

与 ``bash`` 的分工：

* ``bash`` —— 跑项目里的**已有**命令（git / pytest / npm），需要文件在磁盘上；
* ``run_code`` —— 跑**临时**代码片段（写个脚本验证算法、试个 API 用法、
  处理一次数据），无需先建文件，跑完自动清理。

多语言支持：python / node / bash / powershell，按语言映射到对应解释器，
未安装的解释器返回明确的安装提示而不是抛栈。

子进程默认使用 UTF-8；例外输出编码必须显式指定，预览与最终结果使用严格增量解码。
"""

from __future__ import annotations

from harness.i18n import tr
from ..arguments import finite_number, string_argument

import asyncio
import ntpath
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from ...core.errors import ToolExecutionError
from ...llm.types import TextBlock
from ..command_output import STDOUT_CHARS, STDERR_CHARS, command_display, command_progress_key, render_command_output, summarize_command_output
from ..registry import ToolDefinition, ToolRunContext
from ..process_output import cleanup_process, collect_process
from ..processes import ProcessCleanupError, start_process
from ...core.text_encoding import decode_output, output_encoding
from ..process_env import execution_env

#: 默认超时（秒）
DEFAULT_TIMEOUT = 120.0
#: 回传给模型的输出上限
MAX_OUTPUT_CHARS = STDOUT_CHARS
MAX_STDERR_CHARS = STDERR_CHARS

#: 支持的语言对应平台命令模板与临时脚本后缀
#:
#: 占位符 ``{file}`` 是临时脚本路径，所有语言均通过参数向量启动。
_COMMANDS: dict[str, dict[str, Any]] = {
    "python": {
        "suffix": ".py",
        "win": ['"{python}"', '"{file}"'],
        "posix": ['"{python}"', '"{file}"'],
    },
    "python3": {
        "suffix": ".py",
        "win": ['"{python}"', '"{file}"'],
        "posix": ['"{python3}"', '"{file}"'],
    },
    "node": {
        "suffix": ".js",
        "win": ['"{node}"', '"{file}"'],
        "posix": ['"{node}"', '"{file}"'],
    },
    "bash": {
        "suffix": ".sh",
        "win": ['"{bash}"', '"{file}"'],
        "posix": ['"{bash}"', '"{file}"'],
    },
    "powershell": {
        "suffix": ".ps1",
        "win": ['"{powershell}"', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', '"{file}"'],
        "posix": ['"{pwsh}"', '-NoProfile', '-File', '"{file}"'],
    },
}

# 当前公开语言别名共用命令模板，不复制平台配置。
_COMMANDS["nodejs"] = _COMMANDS["node"]
_COMMANDS["sh"] = _COMMANDS["bash"]

#: 解释器缺失时的安装提示
_MISSING_HINT_KEYS = {
    "node": 'python.code_tools.49ef7891c319',
    "bash": 'python.code_tools.360c80f2f063',
    "powershell": 'python.code_tools.43fd932f5761',
    "pwsh": 'python.code_tools.f812fb1a98ab',
}


def _which(name: str) -> str | None:
    """Windows临时脚本需要原生解释器，WSL入口不接受本机脚本路径。"""
    candidate = shutil.which(name)
    if name != 'bash' or sys.platform != 'win32':
        return candidate
    windows = os.environ.get('SystemRoot', r'C:\Windows')
    launchers = {ntpath.normcase(ntpath.join(windows, folder, 'bash.exe'))
                 for folder in ('System32', 'Sysnative')}
    local = os.environ.get('LOCALAPPDATA')
    if local:
        launchers.add(ntpath.normcase(ntpath.join(local, 'Microsoft', 'WindowsApps', 'bash.exe')))
    if candidate is not None and ntpath.normcase(candidate) not in launchers:
        return candidate
    search = os.pathsep.join(part for part in os.get_exec_path()
                             if ntpath.normcase(ntpath.join(part, 'bash.exe')) not in launchers)
    native = shutil.which('bash', path=search)
    if native is not None:
        return native
    git = shutil.which('git')
    if git is not None:
        bundled = Path(git).parent.parent / 'bin' / 'bash.exe'
        if bundled.is_file():
            return str(bundled)
    return None


def _resolve_python() -> str:
    """定位当前解释器（最稳，永远存在）。"""
    return sys.executable or "python"


def _build_env(ctx: ToolRunContext) -> dict[str, str]:
    # 与 shell_tools 保持一致：强制 UTF-8，且不把宿主凭据带入临时脚本。
    return execution_env({"HARNESS_SESSION": ctx.session_id})


def build_code_tools(
    cwd: str | None,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    registry: Any = None,
    owned_processes: bool = False,
) -> list[ToolDefinition]:
    """构造 run_code；Harness 传入所属执行器开关和清理作业表。"""

    async def run_code(args: Any, ctx: ToolRunContext) -> Any:
        """在临时文件里执行一段代码并返回输出。"""
        jobs = registry if registry is not None else ctx.services.get('jobs')
        language = string_argument(args.get("language", "python"), "language").strip().lower()
        code = string_argument(args.get("code"), "code")
        run_timeout = finite_number(args.get("timeout", timeout), "timeout", 0.001, 900)
        if jobs is not None:
            jobs.assert_ready(ctx.session_id)

        spec = _COMMANDS.get(language)
        if spec is None:
            supported = tr('ui.common.list_separator').join(sorted(_COMMANDS))
            raise ToolExecutionError(
                tr('python.code_tools.0379b1ec016b', p0=f'{language!r}', p1=f'{supported}'), language=language
            )

        encoding = output_encoding(args.get("output_encoding", "utf-8"))
        workdir = Path(ctx.cwd or cwd or Path.cwd())
        env = _build_env(ctx)
        is_win = sys.platform == "win32"

        # 按语言解析解释器，缺失直接给安装提示而不是抛栈
        python = _resolve_python()
        node = _which("node")
        bash = _which("bash")
        powershell = _which("powershell") or _which("pwsh")

        resolved: dict[str, str | None] = {
            "python": python,
            "python3": _which("python3") or python,
            "node": node,
            "nodejs": node,
            "bash": bash,
            "sh": bash,
            "powershell": powershell,
            "pwsh": powershell,
        }

        # 本平台命令模板里引用到的占位符，逐一确认可用
        active_parts = spec["win"] if is_win else spec["posix"]
        required: list[str] = []
        for part in active_parts:
            if "{" in part:
                token = part.split("{", 1)[1].split("}", 1)[0]
                # {file} 是我们自己的临时文件路径，不是解释器，稍后单独填充
                if token != "file" and token not in required:
                    required.append(token)
        for token in required:
            if not resolved.get(token):
                raise ToolExecutionError(
                    tr(_MISSING_HINT_KEYS[token]) if token in _MISSING_HINT_KEYS else tr('python.code_tools.6f27472198a5', p0=f'{token}'),
                    language=language,
                    interpreter=token,
                )

        # 写临时文件（跑完在 finally 里清理）
        suffix = spec["suffix"]
        fd, path = tempfile.mkstemp(prefix="harness-code-", suffix=suffix)
        tmp_path = Path(path)
        preserve_script = False
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(code)

            # 解释器与脚本路径作为参数向量项传入，不作为 Shell 代码。
            argv = []
            for part in active_parts:
                part = part.strip('"')
                for token in required:
                    part = part.replace("{" + token + "}", str(resolved[token]))
                argv.append(part.replace("{file}", str(tmp_path)))

            async def launch():
                ctx.check_execution()
                if owned_processes:
                    from ..owned_process import spawn_owned
                    return await spawn_owned(argv, cwd=str(workdir), env=env)
                return await asyncio.create_subprocess_exec(
                    *argv, cwd=str(workdir), env=env,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                    start_new_session=os.name != "nt")

            async def stop_startup(proc):
                nonlocal preserve_script
                try:
                    await cleanup_process(proc, ctx, registry=jobs, kind='run_code',
                        label=tr('python.code_tools.90ca50fba110', p0=language), output_encoding=encoding)
                except BaseException as exc:
                    preserve_script = True
                    if isinstance(exc, ProcessCleanupError):
                        exc.details['temporary_script'] = str(tmp_path)
                        raise
                    raise ProcessCleanupError(tr('process.startup.script_cleanup_failed', path=str(tmp_path)),
                        temporary_script=str(tmp_path)) from exc

            try:
                proc = await start_process(launch, stop=stop_startup)
            except OSError as exc:
                raise ToolExecutionError(tr('python.code_tools.3f8b1b36e149', p0=f'{language}', p1=f'{exc}'), language=language) from exc

            stdout_b, stderr_b, returncode, timed_out = await collect_process(
                proc, run_timeout, ctx, registry=jobs, kind='run_code', label=tr('python.code_tools.90ca50fba110', p0=f'{language}'), output_encoding=encoding)
            if timed_out:
                await cleanup_process(proc, ctx, registry=jobs, kind='run_code', label=tr('python.code_tools.90ca50fba110', p0=f'{language}'),
                                      stdout=stdout_b, stderr=stderr_b, output_encoding=encoding)

            decoded = decode_output(stdout_b, stderr_b, encoding)
            stdout, stderr = decoded["stdout"], decoded["stderr"]
            return {
                **summarize_command_output(stdout, stderr, prefix='harness-code-',
                                           stdout_limit=MAX_OUTPUT_CHARS, stderr_limit=MAX_STDERR_CHARS),
                "language": language,
                "exit_code": -1 if timed_out else returncode,
                **{key: value for key, value in decoded.items() if key not in ("stdout", "stderr")},
                "error": timed_out or returncode != 0 or bool(decoded.get("encoding_errors")),
                "execution_state": "timed-out" if timed_out else "exited",
                **({'timed_out': True, 'timeout_note': tr('python.code_tools.2d018de37ca6', p0=f'{run_timeout}')} if timed_out else {}),
            }
        finally:
            # 进程启动收尾未确认时保留脚本及诊断路径，不提前改变其输入。
            if not preserve_script:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def run_code_to_content(args: Any, value: Any) -> list[TextBlock]:
        return [TextBlock(text=render_command_output(value, label=tr('python.code_tools.941635bfed24')))]

    def run_code_display(args: Any, value: Any) -> dict[str, Any]:
        return {
            **command_display(args, value),
            "stdout_lines": value.get("stdout", "").count("\n") + 1 if value.get("stdout") else 0,
        }

    return [
        ToolDefinition(
            schema={
                "description_key": 'python.code_tools.60a0f145e12b',
                "parameter_keys": {
                    ("properties", "output_encoding", "description"): "encoding.parameter",
                    ('properties', 'language', 'description'): 'python.code_tools.f956ba24323b',
                    ('properties', 'code', 'description'): 'python.code_tools.e35e4317902b',
                    ('properties', 'timeout', 'description'): ('python.code_tools.22d77921f838', {'p0': f'{timeout}'}),
                },
                "name": "run_code",
                "description": (
                    tr('python.code_tools.60a0f145e12b')
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "output_encoding": {"type": "string", "description": tr("encoding.parameter")},
                        "language": {
                            "type": "string",
                            "enum": ["python", "python3", "node", "nodejs", "bash", "sh", "powershell"],
                            "description": tr('python.code_tools.f956ba24323b'),
                        },
                        "code": {"type": "string", "description": tr('python.code_tools.e35e4317902b')},
                        "timeout": {"type": "number", "minimum": 0.001, "maximum": 900, "description": tr('python.code_tools.22d77921f838', p0=f'{timeout}')},
                    },
                    "required": ["code"],
                    "additionalProperties": False,
                },
            },
            execute=run_code,
            argument_semantics={'code': 'code'},
            execution_cwd=lambda args, ctx: Path(ctx.cwd or cwd or Path.cwd()).resolve(),
            progress_key=command_progress_key,
            to_content=run_code_to_content,
            describe=run_code_display,
            # 代码执行有副作用，不并发
            concurrency_safe=False,
            timeout=930.0,
            dangerous=True,
        )
    ]


__all__ = ["build_code_tools"]
