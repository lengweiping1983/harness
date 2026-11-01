"""内置 PowerShell 工具：pwsh。

为什么 Windows 上必须有独立于 ``bash`` 的 PowerShell 面：
本 harness 的 ``bash`` 在 Windows 上是 ``cmd.exe``（见 ``shell_tools``），
而 PowerShell 是完全不同的执行面——对象管道、``$env:``、``Get-ChildItem``、
``Select-String``、``Invoke-RestMethod`` 等需要由 PowerShell 解释执行。

关键行为：
* 参数契约为 ``command`` / ``description`` / ``timeoutMs`` / ``workdir`` /
  ``run_in_background``；结果分 ``background``（显式后台）、``promoted``
  （超时后**转为后台**而不是 kill）、``foreground`` 三种形态。
* 命令用 ``-EncodedCommand``（UTF-16LE base64）传递，彻底绕开引号转义与
  注入问题；不需要手工拼接命令行字符串。
* 输出统一为 UTF-8：脚本前置 ``[Console]::OutputEncoding`` 赋值。

实现上不做 PowerShell 脚本 AST 解析，只做编码与退出码收敛，因此对任何
合法脚本都适用。
"""

from __future__ import annotations

from harness.i18n import tr
from ..arguments import background_argument, finite_number, string_argument

import asyncio
import base64
import os
import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ...llm.types import TextBlock
from ..command_output import STDOUT_CHARS, STDERR_CHARS, command_display, command_progress_key, render_command_output, summarize_command_output, exited_command_result
from ..registry import ToolDefinition, ToolRunContext
from .shell_tools import _build_child_env, _make_label
from ...core.text_encoding import decode_output, output_encoding

if TYPE_CHECKING:  # pragma: no cover - 仅用于类型标注
    from .job_tools import JobRegistry

#: 命令默认超时（秒）。与 shell 的 120s 对齐。
DEFAULT_TIMEOUT = 120.0
#: 回传给模型的输出上限
MAX_OUTPUT_CHARS = STDOUT_CHARS
#: stderr 回传上限
MAX_STDERR_CHARS = STDERR_CHARS

#: 候选解释器：先找 PowerShell 7（pwsh），再退到 Windows PowerShell 5.1。
#: PS7 默认输出就是 UTF-8，PS5.1 默认是 OEM 代码页，靠脚本前置项纠正。
_CANDIDATES = ("pwsh", "powershell.exe", "powershell")

#: 前置项：把控制台输出编码掰成 UTF-8。
#: 独立放在用户命令前一行，错误显示时按前置行数还原用户位置。
_PREAMBLE = (
    "$OutputEncoding=[System.Text.UTF8Encoding]::new($false);"
    "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false);"
    "$ProgressPreference='SilentlyContinue';"
    # 错误默认以 CLIXML（重定向时才启用的序列化格式）写到 stderr，模型很难读；
    # 切回文本视图（PS7 生效，PS5.1 会忽略这个赋值）。
    "$ErrorView='NormalView';"
)

#: 后置项：把退出码收敛成一个确定的整数。独立成一行（与前置项同理），
#: 否则 PowerShell 回显出错构句时会把这一长串内部脚本也带出来。
#: - PowerShell 脚本自身 ``exit N`` 时 $LASTEXITCODE 即为 N；
#: - 调用外部程序时 $LASTEXITCODE 是该程序的退出码；
#: - 纯 cmdlet 出错时 $LASTEXITCODE 可能为空，此时用 $? 兜底。
# ``$?`` 必须在**任何语句之前**捕获：PowerShell 里一次成功的赋值就会把 ``$?``
# 重置为 True，所以必须先读 ``$?`` 再赋值。写反了会让 ``Write-Error`` 这类
# 非终止错误被误判为成功（退出码 0）。
_EPILOGUE = (
    "$__harness_ok=$?;"
    "$__harness_ec=$LASTEXITCODE;"
    "if ($null -eq $__harness_ec) { $__harness_ec = $(if ($__harness_ok) { 0 } else { 1 }) };"
    "if ($__harness_ec -isnot [int]) { $__harness_ec = 0 };"
    "exit $__harness_ec"
)

#: 前置项占的行数（用于把 PowerShell 报的行号还原到用户脚本坐标系）。
_PREAMBLE_LINES = 1

#: 解释器解析结果缓存：``(exe_path, is_pwsh7)``。同一进程内 PATH 不会变，
#: 没必要为每次调用反复查磁盘。
_resolved: tuple[str, bool] | None = None


def pwsh_execution_cwd(args: Any, ctx: ToolRunContext) -> Path:
    """范围核验与进程启动共用绝对目录，避免相对路径按服务目录解释。"""
    base = Path(ctx.cwd or os.getcwd())
    requested = args.get("workdir")
    candidate = Path(requested).expanduser() if requested else base
    return (candidate if candidate.is_absolute() else base / candidate).resolve()


def _resolve_powershell(*, refresh: bool = False) -> tuple[str, bool] | None:
    """定位可用的 PowerShell 解释器。

    :returns: ``(可执行文件路径, 是否 PowerShell 7+)``；找不到返回 ``None``。
    """
    global _resolved
    if _resolved is not None and not refresh:
        return _resolved

    # PATH 优先：装了 PS7 就用 PS7。
    # WSL 会继承 Windows PATH。自动选择 powershell.exe 会把 Linux 路径
    # 交给 Windows 解释器，工作目录、文件参数和进程组都不再属于本执行面。
    # POSIX 仅自动发现本地解释器；跨系统执行应由显式适配器处理。
    candidates = _CANDIDATES if os.name == "nt" else ("pwsh", "powershell")
    for name in candidates:
        found = shutil.which(name)
        if found and (os.name == "nt" or not found.lower().endswith(".exe")):
            _resolved = (found, name == "pwsh")
            return _resolved

    # PATH 里没有时兜底查两个固定位置（PATH 被精简过的机器上很常见）。
    for path in (() if os.name != "nt" else (
        r"C:\Program Files\PowerShell\7\pwsh.exe",
        r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
    )):
        if os.path.isfile(path):
            _resolved = (path, "PowerShell\\7" in path)
            return _resolved
    return None


#: PowerShell 重定向 stderr 时错误记录的 CLIXML 包装头。
_CLIXML_HEADER = b"#< CLIXML"
#: CLIXML 里转义掉的控制字符（PowerShell 用 ``_xHHHH_`` 形式转义）。
_CLIXML_ESCAPES = {
    "_x000D__x000A_": "\r\n",
    "_x000D_": "\r",
    "_x000A_": "\n",
    "_x0009_": "\t",
    "_x0020_": " ",
    "_x0000_": "\x00",
}

#: 抓取 CLIXML 里每一条 ``<S S="Error">…</S>`` 文本。
_CLIXML_ITEM_RE = re.compile(rb'<S S="Error">(.*?)</S>', re.S)


def _unescape_clixml(raw: bytes) -> bytes:
    """还原 CLIXML 里的转义序列。"""
    text = raw.decode('utf-8')
    for needle, real in _CLIXML_ESCAPES.items():
        text = text.replace(needle, real)
    # PowerShell 还会把 XML 实体转义回去。
    return (
        text.replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&apos;", "'")
        .replace("&amp;", "&")
        .encode("utf-8")
    )


#: PowerShell 报 ``At line:N`` 时 N 会把我们注入的前置行算进去，需要减回来。
_LINE_RE = re.compile(r"^At line:(\d+) char:(\d+)(?=\r?$)", re.M)


def _fix_line_numbers(text: str) -> str:
    """把 PowerShell 报的行号/列号还原成用户脚本里的真实位置。"""

    def repl(match: re.Match[str]) -> str:
        line = max(1, int(match.group(1)) - _PREAMBLE_LINES)
        return f"At line:{line} char:{match.group(2)}"

    return _LINE_RE.sub(repl, text)


def _strip_clixml(raw: bytes) -> bytes:
    """把 PowerShell 的 CLIXML stderr 还原成人类可读的纯文本。

    背景：PowerShell 在**输出被重定向**（而不是连着真实控制台）时，会把错误
    记录序列化成 CLIXML 再写到 stderr，开头是 ``#< CLIXML``。模型几乎无法从
    XML 里读出错误原因，而 CLI 用户也只想看到 ``boom`` 这种一行文本。

    PowerShell 7 的 ``$ErrorView = 'NormalView'`` 与 ``$PSStyle.OutputRendering``
    都不能关掉它（实测均无效），唯一纯净的做法是在脚本里把错误流转成
    ``[Console]::Error`` 写入，但那样会把整条命令塞进管道，既拖垮大输出的吞吐，
    又破坏 ``exit N`` / ``$LASTEXITCODE`` 的语义。所以这里在 **Python 侧**解包：
    保留原生命令的流式行为与退出码语义，只把最终字节翻译成人能读的文本。

    非 CLIXML 的 stderr 原样返回，因此外部程序（git、python 等）的原生报错
    不会被误伤。
    """
    if not raw or not raw.lstrip().startswith(_CLIXML_HEADER):
        return raw

    items = _CLIXML_ITEM_RE.findall(raw)
    if not items:
        # 结构不认识的 CLIXML：宁可原样返回，也不要把内容弄丢。
        return raw

    lines: list[str] = []
    # 我们注入的前置/后置脚本对用户毫无意义（模型看到只会以为命令里写了一段
    # 莫名其妙的东西），PowerShell 回显出错构句时会把它一起带出来，精确剔除。
    # PowerShell 有时给回移行加 "+ "（构句显示）/ "- "（脚本行）前缀，两种都试。
    internal = (_PREAMBLE.strip(), _EPILOGUE.strip())

    def strip_internal(text: str) -> str:
        """删掉 PowerShell 回显里的内部前置/后置脚本。

        有两种形态：整行就是内部脚本（前置行的独立回显），以及
        ``<内部脚本> : <错误消息>`` 这种把内部脚本拼在消息前面的形态
        （后置行与 ``Write-Error`` 同行时就会这样）。后者删掉内部片段后
        要把残留的 ``: `` 连接符也去掉，只留真正给用户看的那句消息。
        """
        candidate = text
        for prefix in ("+ ", "- "):
            if candidate.startswith(prefix):
                candidate = candidate[len(prefix):].strip()
                break
        for frag in internal:
            if candidate == frag:
                return ""
            for separator in (" : ", ": "):
                marker = frag + separator
                if candidate.startswith(marker):
                    return candidate[len(marker):].strip()
        return text

    for item in items:
        text = _unescape_clixml(item).decode('utf-8').strip()
        if not text:
            continue
        # 先原样尝试：``+ CategoryInfo`` 这类 PowerShell 自带的元信息行带了
        # "+ " 前缀，必须原样保留，不能顺手剥掉。
        cleaned = strip_internal(text)
        if not cleaned:
            continue
        lines.append(_fix_line_numbers(cleaned))
    if not lines:
        return b""
    return ("\n".join(lines) + "\n").encode("utf-8")



def _decode_pwsh_output(stdout: bytes, stderr: bytes, encoding: str) -> dict:
    """CLIXML 是已声明的 UTF-8 适配边界，不套用原生命令的输出编码。"""
    result = decode_output(stdout, b'', encoding)
    xml = stderr.startswith(b'#< CLIXML')
    diagnostic = decode_output(b'', stderr, 'utf-8' if xml else encoding)
    if xml and not diagnostic.get('encoding_errors'):
        diagnostic['stderr'] = _strip_clixml(stderr).decode('utf-8')
    result['stderr'] = diagnostic['stderr']
    errors = result.get('encoding_errors', []) + diagnostic.get('encoding_errors', [])
    if errors:
        result['encoding_errors'] = errors
    return result


def _build_script(command: str) -> str:
    """把用户命令包成带前置/后置项的完整脚本。

    前置项单独占一行（而不是与用户命令同行），是为了错误信息可读：
    PowerShell 会把出错的**构句**原样回显在 stderr 里，若前置项与用户命令同行，
    每条错误前面都会拼上一长段内部前置脚本（实测如此），模型读到的是噪声。

    代价是用户脚本的报错行号整体后移 1，由 :func:`_strip_clixml` 统一减回来。
    """
    return _PREAMBLE + "\n" + command + "\n" + _EPILOGUE


def _encode_command(script: str) -> str:
    """按 ``-EncodedCommand`` 的要求编码脚本。

    PowerShell 规定该参数必须是 **UTF-16LE** 的 base64。直接传原始字符串的话，
    非 ASCII 命令会被调用侧的代码页破坏（这正是中文脚本乱码/报语法错误的
    根源），所以必须显式编码后经 argv 传递。
    """
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def _build_argv(exe: str, script: str) -> list[str]:
    """构造 argv。

    ``-NoProfile`` 跳过用户 profile（否则 profile 里的 alias/函数会污染脚本，
    行为不可复现）；``-NonInteractive`` 禁止任何挂起式提示，否则会死等输入；
    ``-ExecutionPolicy Bypass`` 只影响本次进程，避免受机器组策略影响而拒绝执行。
    """
    return [
        exe,
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-EncodedCommand",
        _encode_command(script),
    ]


async def _launch(argv: list[str], cwd: str, env: dict[str, str]) -> Any:
    """启动 PowerShell 子进程。"""
    return await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=os.name != "nt",
    )


async def _collect(proc: Any, run_timeout: float, ctx: ToolRunContext | None = None, *,
                   registry: Any = None, label: str | None = None, output_encoding: str = "utf-8") -> tuple[bytes, bytes, int, bool]:
    """在前台读取子进程输出。

    必须**并发**抽干两个管道：只等 ``proc.wait()`` 的话，输出超过管道缓冲
    （Windows 上约 64KB）后子进程会写阻塞、永不退出，于是大输出命令会被
    误判成超时。

    :returns: ``(stdout, stderr, returncode, 是否超时)``。
        超时时返回已读字节；后台接手时先初始化输出，再读 StreamReader
        中剩余字节。不得丢弃已经消费的输出。
    """
    from ..process_output import collect_process
    return await collect_process(proc, run_timeout, ctx, registry=registry, kind="pwsh", label=tr('python.pwsh_tools.785521b4c16d') if label is None else label, output_encoding=output_encoding)


def build_pwsh_tools(
    *,
    timeout: float = DEFAULT_TIMEOUT,
    registry: "JobRegistry | None" = None,
    owned_processes: bool = False,
) -> list[ToolDefinition]:
    """构造 PowerShell 工具。

    :param timeout: 前台执行默认超时（秒）。
    :param registry: 后台作业注册表。提供后支持 ``run_in_background``，
        并且**超时会被提升为后台作业**：
        长命令不该因为前端超时被杀掉，它还在正常跑，模型可以改用
        ``job_output`` 接着收。
    """

    async def pwsh(args: Any, ctx: ToolRunContext) -> Any:
        """执行一条 PowerShell 命令。"""
        command = string_argument(args.get("command"), "command")
        run_timeout = finite_number(args.get("timeoutMs", timeout * 1000), "timeoutMs", 1, 900000) / 1000
        background = background_argument(args.get("run_in_background", False), registry is not None)
        if registry is not None:
            registry.assert_ready(ctx.session_id)
        encoding = output_encoding(args.get("output_encoding", "utf-8"))
        resolved = _resolve_powershell()
        if resolved is None:
            return {
                "execution_state": "not-started",
                "exit_code": -1,
                "stdout": "",
                "stderr": (
                    tr('python.pwsh_tools.6aa4f45fee8d')
                ),
                "error": True,
            }
        exe, _is_pwsh7 = resolved

        cwd = str(pwsh_execution_cwd(args, ctx))
        env = _build_child_env(ctx.session_id)
        argv = _build_argv(exe, _build_script(command))

        async def launch():
            ctx.check_execution()
            if owned_processes:
                from ..owned_process import spawn_owned
                return await spawn_owned(argv, cwd=cwd, env=env)
            return await _launch(argv, cwd, env)

        # -- 显式后台 ----------------------------------------------------
        if background:

            async def _spawn_bg() -> Any:
                return await launch()

            job = await registry.spawn(
                kind="pwsh",
                label=_make_label(command),
                owner=ctx.session_id,
                spawn=_spawn_bg,
                output_encoding=encoding,
            )
            return {"job_id": job.id, "job": job.public(), "background": True}

        try:
            from ..processes import start_process
            from ..process_output import cleanup_process
            proc = await start_process(launch, stop=lambda process: cleanup_process(
                process, ctx, registry=registry, kind="pwsh", label=_make_label(command), output_encoding=encoding))
        except OSError as exc:
            return {"execution_state": "not-started", "exit_code": -1, "stdout": "", "stderr": tr('python.pwsh_tools.672bbe6d1b4a', p0=f'{exc}'), "error": True}

        stdout_b, stderr_b, returncode, timed_out = await _collect(
            proc, run_timeout, ctx, registry=registry, label=_make_label(command), output_encoding=encoding)

        # -- 超时提升为后台 ------------------------------------------------
        # 命令没死，只是不该在前台等。杀掉它会丢掉真实
        # 进度（编译、测试套件），所以把同一个进程交给 job_* 工具接着看。
        if timed_out and registry is not None:

            async def _adopt() -> Any:
                return proc

            job = await registry.spawn(
                kind="pwsh",
                label=_make_label(command),
                owner=ctx.session_id,
                spawn=_adopt,
                output_encoding=encoding,
                initial_stdout=stdout_b,
                initial_stderr=stderr_b,
            )
            return {
                "promoted": True,
                "background": True,
                "job_id": job.id,
                "job": job.public(),
                "timed_out": True,
                "note": (
                    tr('python.pwsh_tools.40518bceb425', p0=f'{run_timeout:g}')
                ),
            }

        if timed_out:
            from ..processes import stop_process
            await stop_process(proc)
            decoded = _decode_pwsh_output(stdout_b, stderr_b, encoding)
            stdout, stderr = decoded["stdout"], decoded["stderr"]
            return {
                **summarize_command_output(stdout, stderr, prefix='harness-pwsh-',
                                           stdout_limit=MAX_OUTPUT_CHARS, stderr_limit=MAX_STDERR_CHARS),
                **{key: value for key, value in decoded.items() if key not in ("stdout", "stderr")},
                "exit_code": -1,
                "timeout_note": tr('python.pwsh_tools.648561aede4b', p0=f'{run_timeout:g}'),
                "execution_state": "timed-out",
                "timed_out": True,
                "error": True,
            }

        decoded = _decode_pwsh_output(stdout_b, stderr_b, encoding)
        return exited_command_result(decoded, returncode, prefix='harness-pwsh-',
                                     stdout_limit=MAX_OUTPUT_CHARS, stderr_limit=MAX_STDERR_CHARS)

    def pwsh_to_content(args: Any, value: Any) -> list[TextBlock]:
        if value.get("promoted"):
            job = value.get("job", {})
            return [
                TextBlock(
                    text="\n".join(
                        [
                            value.get("note", tr('python.pwsh_tools.8eb453a32554')),
                            tr('python.pwsh_tools.f898f4ed71d4', p0=f"{value['job_id']}", p1=f"{job.get('status', 'running')}"),
                            tr('python.pwsh_tools.e5419a978376', p0=f"{job.get('label', '')}"),
                        ]
                    )
                )
            ]
        if value.get("background"):
            job = value.get("job", {})
            return [
                TextBlock(
                    text="\n".join(
                        [
                            tr('python.pwsh_tools.8f85e3746b95', p0=f"{value['job_id']}"),
                            tr('python.pwsh_tools.8fc6a859e608'),
                            tr('python.pwsh_tools.8cb3b54db350', p0=f"{job.get('status', 'running')}", p1=f"{job.get('label', '')}"),
                        ]
                    )
                )
            ]

        return [TextBlock(text=render_command_output(value))]

    properties: dict[str, Any] = {
        "command": {"type": "string", "description": tr('python.pwsh_tools.be2c2167cc75')},
        "description": {
            "type": "string",
            "description": tr('python.pwsh_tools.86eefed4fea7'),
        },
        "timeoutMs": {
            "type": "number", "minimum": 1, "maximum": 900000,
            "description": (
                tr('python.pwsh_tools.3074663b9f6f', p0=f'{int(timeout * 1000)}')
            ),
        },
        "workdir": {"type": "string", "description": tr('python.pwsh_tools.27b94e7315c5')},
    }
    if registry is not None:
        properties["run_in_background"] = {
            "type": "boolean",
            "description": (
                tr('python.pwsh_tools.faa885dee6b4')
            ),
        }

    properties["output_encoding"] = {"type": "string", "description": tr("encoding.parameter")}
    parameter_keys = {
        ("properties", "output_encoding", "description"): "encoding.parameter",
        ('properties', 'command', 'description'): 'python.pwsh_tools.be2c2167cc75',
        ('properties', 'description', 'description'): 'python.pwsh_tools.86eefed4fea7',
        ('properties', 'timeoutMs', 'description'): ('python.pwsh_tools.3074663b9f6f', {'p0': f'{int(timeout * 1000)}'}),
        ('properties', 'workdir', 'description'): 'python.pwsh_tools.27b94e7315c5',
    }
    if registry is not None:
        parameter_keys[('properties', 'run_in_background', 'description')] = 'python.pwsh_tools.faa885dee6b4'

    return [
        ToolDefinition(
            schema={
                "parameter_keys": parameter_keys,
                "description_key": 'python.pwsh_tools.3b99508e37fc',
                "name": "pwsh",
                "description": (
                    tr('python.pwsh_tools.3b99508e37fc')
                ),
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": ["command"],
                    "additionalProperties": False,
                },
            },
            execute=pwsh,
            execution_cwd=pwsh_execution_cwd,
            effect="execute",
            progress_key=command_progress_key,
            to_content=pwsh_to_content,
            describe=command_display,
            # 与 bash 同理：shell 有副作用，不能并发
            concurrency_safe=False,
            timeout=max(timeout, 900.0) + 15,
            dangerous=True,
        )
    ]


__all__ = ["build_pwsh_tools"]
