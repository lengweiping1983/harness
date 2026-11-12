"""内置持久终端工具：terminal_open/send/read/signal/close/list。

与一次性的 ``bash`` 工具的区别：``bash`` 每次调用都新建进程、跑完即退出；
持久终端（PTY）会**常驻**，模型可以在同一会话里分多次往同一个 shell 写命令、
回读历史输出、给前台进程发信号，直到显式 ``terminal_close``。这对应真实
开发中的 REPL / ``gdb`` / ``npm run dev`` / 长跑调试器等场景。

对齐的原契约：

* 工具名 ``terminal_open`` / ``terminal_send`` / ``terminal_read`` /
  ``terminal_signal`` / ``terminal_close`` / ``terminal_list``；
* 会话 id 形如 ``pty-1``，按 owner（会话）隔离；
* ``terminal_send`` 的等待原因 ``stdin_read`` / ``inferred_idle`` /
  ``timeout`` / ``session_exit``；
* ``terminal_send(run_in_background=true)`` 返回 job id，交给已迁移的
  ``job_output`` / ``job_kill`` 收集与停止；
* 结果渲染与 ``render.ts`` 同构：截断标记 ``[output truncated]``、
  ``[lines: a-b of n]``、``[wait: reason]``、``[session: status]``。

平台适配说明（非功能缺失）：

* POSIX 使用原生 PTY，支持真实 TTY、作业控制、前台中断及窗口尺寸。
  Windows 优先使用 ConPTY；运行库缺失时使用合并管道，视图按实际传输标注。
* 因此提示符识别只能做启发式：输出尾部匹配 shell 提示符时记 ``stdin_read``，
  否则按输出静默时长记 ``inferred_idle``。
* 信号在 POSIX 上走进程组（``killpg``），在 Windows 上映射为
  ConPTY 控制台输入或 CTRL_BREAK / terminate / kill，保留管道路径的降级处理。
"""

from __future__ import annotations

from harness.core.text_encoding import utf8_prefix

from harness.i18n import tr
from ...core.text_encoding import TextDecoder, output_encoding as resolve_output_encoding
from ..process_env import execution_env

import asyncio
import contextlib
import logging
import math
import os
import re
import shutil
import signal as signal_mod
import subprocess
import sys
import tempfile
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ...core.errors import ToolDeniedError
from ...core.cleanup import join_cleanup, raise_cleanup_errors
from ...llm.types import TextBlock
from ..registry import ToolDefinition, ToolRunContext
from ..processes import ProcessCleanupError, stop_process
from ..terminal_stream import TerminalStream
from ..terminal_transcript import TerminalTranscript

logger = logging.getLogger(__name__)

# -- 与原 config.ts 默认值对齐 ------------------------------------------------
DEFAULT_MAX_RESULT_BYTES = 256 * 1024
MIN_MAX_RESULT_BYTES = 64
DEFAULT_SCROLLBACK_LINES = 10_000
DEFAULT_SCROLLBACK_MAX_BYTES = 4 * 1024 * 1024
DEFAULT_MAX_READ_BYTES = 256 * 1024
DEFAULT_POLL_INTERVAL = 0.05
DEFAULT_IDLE_SILENCE = 3.0
DEFAULT_HANDOFF_GRACE = 0.5
DEFAULT_STARTUP_RESULT_BYTES = 8192
DEFAULT_SEND_TIMEOUT = 30.0
DEFAULT_READ_LINES = 500

#: 原 ``TerminalWaitReason``
WAIT_REASONS = ("stdin_read", "inferred_idle", "timeout", "session_exit")
#: 原 ``TerminalSignal``
TERMINAL_SIGNALS = ("SIGINT", "SIGTERM", "SIGKILL", "SIGTSTP", "SIGHUP")

TRUNCATED = "\n[output truncated]"
_TRUNCATED_BYTES = len(TRUNCATED.encode("utf-8"))
#: 单行上限，防止无换行的长输出把内存吃满
_MAX_LINE_CHARS = 64 * 1024


class TerminalError(RuntimeError):
    """持久终端服务层的错误（模型可见的文案都在这里组织）。"""


# ======================================================================
# 解码
# ======================================================================
class _StreamDecoder(TextDecoder):
    """终端复用框架增量解码，不猜测外部编码。"""

    def feed(self, data: bytes) -> str:
        return self.decode(data)

    def flush(self) -> str:
        return self.decode(b'', final=True)

# ---------------------------------------------------------------------------
# 滚动缓冲
# ---------------------------------------------------------------------------
class _LineBuffer:
    """按行保留的终端回显缓冲。

    ``scrollbackLines`` / ``scrollbackMaxBytes`` 双上限：超出后
    从**最旧**的一侧丢弃，并记录丢弃行数，这样 ``terminal_read`` 才知道自己
    读到的是不是完整窗口。
    """

    __slots__ = ("_lines", "_max_lines", "_max_chars", "_chars", "_dropped", "_tail", "_tail_dropped", "_cursor")

    def __init__(
        self,
        max_lines: int = DEFAULT_SCROLLBACK_LINES,
        max_chars: int = DEFAULT_SCROLLBACK_MAX_BYTES,
    ) -> None:
        self._lines: deque[str] = deque()
        self._max_lines = max(1, int(max_lines))
        self._max_chars = max(1, int(max_chars))
        self._chars = 0
        self._dropped = 0
        #: 尚未见到换行的残尾，等下一批输出接上
        self._tail = ""
        self._tail_dropped = 0
        self._cursor = 0

    # -- 写入 ------------------------------------------------------------
    def append(self, text: str) -> None:
        """按换行提交记录，回车和退格只移动当前行的写入位置。"""
        for char in re.split(r"([\n\r\b])", text):
            if not char:
                continue
            if char == "\n":
                line = self._tail
                if self._tail_dropped:
                    line = "[line truncated] " + line
                self._push(line)
                self._tail = ""
                self._cursor = 0
                self._tail_dropped = 0
            elif char == "\r":
                self._cursor = 0
            elif char == "\b":
                self._cursor = max(0, self._cursor - 1)
            else:
                self._tail = self._tail[:self._cursor] + char + self._tail[self._cursor + len(char):]
                self._cursor += len(char)
                if len(self._tail) > _MAX_LINE_CHARS:
                    excess = len(self._tail) - _MAX_LINE_CHARS
                    self._tail_dropped += excess
                    self._tail = self._tail[excess:]
                    self._cursor = max(0, self._cursor - excess)

    def flush(self) -> None:
        """进程结束后把残尾补成一行。"""
        if self._tail:
            tail, self._tail = self._tail, ""
            self._cursor = 0
            if self._tail_dropped:
                tail = "[line truncated] " + tail
            self._tail_dropped = 0
            self._push(tail)

    def pending(self) -> tuple[str, bool]:
        """尚未换行的有界尾部；读取不消费，不影响提示符判断。"""
        return self._tail, bool(self._tail_dropped)

    def _push(self, line: str) -> None:
        self._lines.append(line)
        self._chars += len(line) + 1
        while len(self._lines) > self._max_lines:
            oldest = self._lines.popleft()
            self._chars -= len(oldest) + 1
            self._dropped += 1
        while self._chars > self._max_chars and len(self._lines) > 1:
            oldest = self._lines.popleft()
            self._chars -= len(oldest) + 1
            self._dropped += 1

    # -- 读取 ------------------------------------------------------------
    @property
    def dropped_lines(self) -> int:
        """因为超出上限而被丢弃的行数。"""
        return self._dropped

    @property
    def total_lines(self) -> int:
        """累计产生的行数（含已丢弃的），单调递增。"""
        return len(self._lines) + self._dropped

    def snapshot(self) -> list[str]:
        """当前仍保留的全部行。"""
        return list(self._lines)

    def drain(self) -> None:
        """丢弃当前保留的全部行，但**不**重置行号计数。

        用于把启动横幅（motd）从 ``terminal_read`` 的默认窗口里摘掉：
        行号仍然单调递增，所以后续 ``offset`` 语义不变。
        """
        for line in self._lines:
            self._chars -= len(line) + 1
            self._dropped += 1
        self._lines.clear()
        self._chars = 0

    def last_line(self) -> str:
        """最后一条非空行（用于提示符启发式）。

        残尾永远是**最新**的内容：shell 打完提示符后不换行，下一条输出会接在
        提示符后面。所以先看残尾，再往回找已换行的行。
        """
        tail = self._tail.strip()
        if tail:
            return tail
        for line in reversed(self._lines):
            stripped = line.strip()
            if stripped:
                return stripped
        return ""


class TerminalSendOperation:
    """一次 ``terminal_send`` 的执行体。

    前台调用直接 ``await op.done()``；后台调用把它包成作业，模型改用
    ``job_output`` 轮询——两种形态共用同一份增量输出，避免两条路径各写一套。
    """

    __slots__ = ("session_id", "_future", "_delta", "_delta_chars", "_dropped", "_cancel_requested", "_task")

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._future: asyncio.Future[dict[str, Any] | Exception] = asyncio.get_running_loop().create_future()
        self._delta: deque[str] = deque()
        self._delta_chars = 0
        self._dropped = 0
        self._cancel_requested = False
        self._task: asyncio.Task[None] | None = None

    # -- 输出 ------------------------------------------------------------
    def feed(self, text: str) -> None:
        """记录这段时间内新产生的输出。"""
        if text:
            self._delta.append(text)
            self._delta_chars += len(text)
            while self._delta_chars > DEFAULT_MAX_RESULT_BYTES:
                excess = self._delta_chars - DEFAULT_MAX_RESULT_BYTES
                first = self._delta.popleft()
                dropped = min(excess, len(first))
                if dropped < len(first):
                    self._delta.appendleft(first[dropped:])
                self._delta_chars -= dropped
                self._dropped += dropped

    def read_output(self) -> tuple[str, bool]:
        """消费式读取自上次以来新增的输出，返回 ``(文本, 是否丢过数据)``。"""
        text = "".join(self._delta)
        lost = self._dropped > 0
        self._delta.clear()
        self._delta_chars = self._dropped = 0
        if lost:
            text = '[output truncated]\n' + text
        return text, lost

    # -- 状态 ------------------------------------------------------------
    @property
    def is_done(self) -> bool:
        return self._future.done()

    @property
    def cancel_requested(self) -> bool:
        return self._cancel_requested

    def settle(self, reason: str, detail: str | None = None) -> None:
        """落定等待结果；重复调用只有第一次生效。"""
        if self._future.done():
            return
        payload: dict[str, Any] = {"reason": reason}
        if detail:
            payload["detail"] = detail
        self._future.set_result(payload)

    def fail(self, error: Exception) -> None:
        """在同一完成对象中保留失败，不伪造终端退出，也不遗留未取异常。"""
        if not self._future.done():
            self._future.set_result(error)

    async def done(self) -> dict[str, Any]:
        """等待输入结果，失败由前台工具或原后台作业统一报告。"""
        outcome = await asyncio.shield(self._future)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def request_cancel(self) -> bool:
        """请求取消等待：立即落定，但**不**向子进程发信号。

        必须同时 :meth:`settle`，否则后台作业侧的 ``await proc.wait()`` 会一直
        挂到自己的 reap 超时，白白拖慢 ``job_kill``。
        """
        if self._future.done():
            return False
        self._cancel_requested = True
        self.settle("session_exit", "cancelled")
        return True

# ---------------------------------------------------------------------------
# 会话
# ---------------------------------------------------------------------------
#: 提示符尾字符：命中即认为「shell 已把控制权交回 stdin」
_PROMPT_TAIL = ("$", "#", ">", "%", "❯", "›", "»", "▸")

_POSIX_SIGNALS = {
    "SIGINT": lambda: signal_mod.SIGINT,
    "SIGTERM": lambda: signal_mod.SIGTERM,
    "SIGKILL": lambda: signal_mod.SIGKILL,
    "SIGTSTP": lambda: signal_mod.SIGTSTP,
    "SIGHUP": lambda: signal_mod.SIGHUP,
}


#: 子进程 stdout/stderr 固定按 UTF-8 产出，读取侧只认 UTF-8 就能解码。
_CHILD_IO_ENCODING = "utf-8"

#: Windows 批处理必须用 CRLF；cmd 是**逐行惰性读取**批处理文件的。
_BATCH_NEWLINE = "\r\n"
#: 让 cmd 以 UTF-8 解释批处理文件里的非 ASCII 内容（关键前置，已实测有效）。
_BATCH_PREAMBLE = "chcp 65001 >nul"


def _write_command_file(directory: str, index: int, text: str) -> str:
    """把含非 ASCII 的命令写成 UTF-8 批处理文件，返回文件路径。

    Windows 的 ``cmd.exe`` / ``powershell`` 从**管道**读 stdin 时，非 ASCII
    会被逐字节打成 U+FFFD——实测 UTF-8 / cp936 / GBK / ``chcp 65001`` 全部无效，
    是不可逆的。但从**文件**读时 cmd 逐行按当前代码页解码，只要前面先跑过
    ``chcp 65001``，UTF-8 批处理文件里的中文就能原样执行。

    所以非 ASCII 命令一律走「临时 ``.bat`` + ``call``」这条纯 ASCII 通道，
    管道里只发送 ASCII，彻底绕开坏掉的管道输入路径。
    """
    path = os.path.join(directory, "cmd-%d.bat" % index)
    body = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", _BATCH_NEWLINE)
    script = "@echo off" + _BATCH_NEWLINE + _BATCH_PREAMBLE + _BATCH_NEWLINE
    if body:
        script += body + _BATCH_NEWLINE
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(script)
    return path


@dataclass
class _TerminalConfig:
    """单次运行的可调参数（对齐原 ``terminal-bash/config.ts``）。"""

    scrollback_lines: int = DEFAULT_SCROLLBACK_LINES
    scrollback_max_bytes: int = DEFAULT_SCROLLBACK_MAX_BYTES
    max_read_bytes: int = DEFAULT_MAX_READ_BYTES
    poll_interval: float = DEFAULT_POLL_INTERVAL
    idle_silence: float = DEFAULT_IDLE_SILENCE
    send_timeout: float = DEFAULT_SEND_TIMEOUT


class _TerminalSession:
    """一个常驻的 shell 进程 + 它的回显缓冲。"""

    def __init__(
        self,
        *,
        session_id: str,
        owner: str,
        kind: str,
        name: str | None,
        cwd: str,
        proc: Any,
        config: _TerminalConfig,
        output_encoding: str = "utf-8",
    ) -> None:
        self.id = session_id
        self.owner = owner
        self.kind = kind
        self.name = name
        self.cwd = cwd
        self.proc = proc
        self.config = config
        self.decoder = _StreamDecoder(output_encoding)
        self.buffer = _LineBuffer(config.scrollback_lines, config.scrollback_max_bytes)
        self.stream = TerminalStream()
        self.transcript = TerminalTranscript()
        self._input_lock = asyncio.Lock()
        self.created_at = time.time()
        self._ops: list[TerminalSendOperation] = []
        self._foreground: TerminalSendOperation | None = None
        self._lock = asyncio.Lock()
        self._reader_task: asyncio.Task[None] | None = None
        self._scratch: str | None = None
        self._scratch_seq = 0
        self._revision = 0
        self._output_changed = asyncio.Event()
        self._closing = False
        self._closed = False
        self._process_stopped = False
        self._close_lock = asyncio.Lock()
        self._cleanup_error: str | None = None
        self._read_error: Exception | None = None

    # -- 生命周期 --------------------------------------------------------
    def start_reader(self) -> None:
        if self._reader_task is None:
            self._reader_task = asyncio.create_task(self._reader())

    async def _reader(self) -> None:
        """持续把 stdout 灌进滚动缓冲与当前等待中的操作。"""
        stream = getattr(self.proc, "stdout", None)
        decoder = self.decoder
        try:
            if stream is not None:
                while True:
                    chunk = await stream.read(65536)
                    if not chunk:
                        break
                    text = decoder.feed(chunk)
                    if text:
                        self._ingest(text)
        except asyncio.CancelledError:  # pragma: no cover - 关闭路径
            raise
        except Exception as exc:
            self._read_error = exc
            for operation in list(self._ops):
                operation.fail(exc)
        finally:
            with contextlib.suppress(Exception):
                tail = decoder.flush()
                if tail:
                    self._ingest(tail)
            self.buffer.flush()
            self._output_changed.set()
            # EOF 基本等价于「进程没了」，让还在等的前台操作立刻返回。
            if self._read_error is None:
                self._settle_all("session_exit", tr('feedback.2093b6f6bda4'))

    def _ingest(self, text: str) -> None:
        self._revision += 1
        clean = self.transcript.feed(text)
        self.buffer.append(clean)
        self.stream.append(text)
        self._output_changed.set()
        for op in self._ops:
            if not op.is_done:
                op.feed(clean)

    def _settle_all(self, reason: str, detail: str | None = None) -> None:
        for op in list(self._ops):
            op.settle(reason, detail)

    def _retire(self, op: TerminalSendOperation) -> None:
        with contextlib.suppress(ValueError):
            self._ops.remove(op)
        if self._foreground is op:
            self._foreground = None

    @property
    def busy(self) -> bool:
        return self._foreground is not None and not self._foreground.is_done

    @property
    def is_closed(self) -> bool:
        return self._closed

    @property
    def closing(self) -> bool:
        return self._closing

    def _check_output(self) -> None:
        if self._read_error is not None:
            raise TerminalError(tr('ui.terminal.outputFailed', error=str(self._read_error))) from self._read_error

    # -- 输入 ------------------------------------------------------------
    async def write_input(self, text: str, *, execution_check: Callable[[], bool | None] | None = None) -> None:
        """即时键盘输入，与模型写入串行执行，不等待空闲。"""
        async with self._input_lock:
            self._check_output()
            # 在真正写入前复核；排队期间权限变化或操作落定不能留下迟到输入。
            if execution_check is not None and execution_check() is False:
                return
            if self._closing or self._cleanup_error or self._closed or self.proc.returncode is not None:
                raise TerminalError(tr('python.terminal_tools.b900e373af4f', p0=f'{self.id}'))
            try:
                self.proc.stdin.write(text.encode(_CHILD_IO_ENCODING))
                await asyncio.wait_for(self.proc.stdin.drain(), 5)
            except (OSError, TimeoutError) as exc:
                raise TerminalError(tr('python.terminal_tools.56f84ddcd971', p0=f'{exc}')) from exc

    def resize(self, cols: int, rows: int) -> None:
        if not hasattr(self.proc, 'resize'):
            raise TerminalError(tr('python.terminal_tools.96e00847546a'))
        if self._closing or self._closed or self.proc.returncode is not None:
            raise TerminalError(tr('python.terminal_tools.194bcd7e1ce8'))
        try:
            self.proc.resize(cols, rows)
        except OSError as exc:
            raise TerminalError(tr('python.terminal_tools.afe7b8a50c47', p0=f'{exc}')) from exc

    async def read_stream(self, cursor: int, wait_ms: int = 1000) -> dict[str, Any]:
        """长轮询原始输出，不推进其他观察者的游标。"""
        if cursor == self.stream.end and self.proc.returncode is None and not (self._reader_task and self._reader_task.done()):
            self._output_changed.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._output_changed.wait(), wait_ms / 1000)
        return {**self.stream.read(cursor), 'terminal': self.snapshot(),
                'eof': self._closed or bool(self._reader_task and self._reader_task.done())}

    async def start_send(
        self, text: str, *, submit: bool, background: bool = False,
        execution_check: Callable[[], None] | None = None
    ) -> TerminalSendOperation:
        """写入 stdin 并开始等待输出稳定。

        同一时间只允许**一次前台**输入，因为它独占「输出是否已稳定」这个判定。
        后台输入不受此限——否则 ``npm run dev`` 跑起来之后就再也没法打字了。
        """
        async with self._lock:
            self._check_output()
            if self._closing or self._cleanup_error or self._closed:
                raise TerminalError(tr('python.terminal_tools.6a4b35820eae', p0=f'{self.id}'))
            if self.busy and not background:
                raise TerminalError(
                    tr('python.terminal_tools.15a1aef878a8', p0=f'{self.id}')
                )
            if self.proc.returncode is not None:
                raise TerminalError(tr('python.terminal_tools.072ff2693de2', p0=f'{self.id}'))
            op = TerminalSendOperation(self.id)
            self._ops.append(op)
            if not background:
                self._foreground = op
            started = time.monotonic()
            budget = self.config.send_timeout
            op._task = asyncio.create_task(
                self._run_send(op, text, submit, started, budget, execution_check)
            )
            return op

    async def _run_send(
        self,
        op: TerminalSendOperation,
        text: str,
        submit: bool,
        start: float,
        budget: float,
        execution_check: Callable[[], None] | None = None,
    ) -> None:
        try:
            if execution_check is not None:
                execution_check()
            await self._send_loop(op, text, submit, start, budget, execution_check)
        except Exception as exc:
            op.fail(exc)
        finally:
            self._retire(op)

    def _payload_for_stdin(self, text: str, submit: bool) -> str:
        """把模型给的输入变成**可安全写入管道**的 ASCII 文本。

        ``cmd.exe`` 从管道读 stdin 走的是控制台输入例程：非 ASCII 字节会不可逆地
        变成 U+FFFD，``chcp`` 救不回来。但从**文件**读批处理时是按当前代码页逐行
        解码的，而 cmd 对 .bat 又是惰性读取，所以「先写 UTF-8 的 .bat、开头 chcp
        65001、再用纯 ASCII 的 ``call`` 触发」能让中文原样通过。
        """
        if sys.platform != "win32" or hasattr(self.proc, 'resize') or text.isascii():
            return text
        if not submit:
            raise TerminalError(
                tr('python.terminal_tools.a19c62eb0086')
            )
        if self._scratch is None:
            self._scratch = tempfile.mkdtemp(prefix="harness-terminal-")
        self._scratch_seq += 1
        path = _write_command_file(self._scratch, self._scratch_seq, text)
        return 'call "{}"'.format(path)

    def _discard_scratch(self) -> None:
        """删掉本次会话攒下的临时批处理目录。"""
        if self._scratch is None:
            return
        directory, self._scratch = self._scratch, None
        shutil.rmtree(directory, ignore_errors=True)
    async def _send_loop(
        self,
        op: TerminalSendOperation,
        text: str,
        submit: bool,
        start: float,
        budget: float,
        execution_check: Callable[[], None] | None = None,
    ) -> None:

        # ``cmd.exe`` 从**管道**读 stdin 时按 CRLF 断行；只发 \r 会被当成行内字符，
        # 命令永远不执行，表现为「输入后毫无回显」。
        newline = ("\r" if hasattr(self.proc, 'resize') else "\r\n") if sys.platform == "win32" else "\n"
        payload = self._payload_for_stdin(text, submit)
        if payload and submit:
            payload += newline
        if payload:
            try:
                def admit():
                    if op.is_done:
                        return False
                    if execution_check is not None:
                        execution_check()
                await self.write_input(payload, execution_check=admit)
            except (BrokenPipeError, ConnectionResetError, OSError) as exc:
                op.settle("session_exit", tr('terminal.feedback.stdin_closed', error=exc))
                return
            except ToolDeniedError:
                raise
            except Exception as exc:  # noqa: BLE001
                op.settle("session_exit", tr('terminal.feedback.stdin_failed', error=exc))
                return

        # 超时基准固定在「这次输入被接受」的时刻。若在这里才起算，事件循环繁忙时
        # 任务被推迟多久，等待预算就被平白拉长多久。
        last_change = start
        deadline = start + budget
        revision = self._revision
        while True:
            if op.is_done:
                return
            if self.proc.returncode is not None:
                op.settle("session_exit", tr('terminal.feedback.exit_code', code=self.proc.returncode))
                return
            now = time.monotonic()
            if now >= deadline:
                op.settle("timeout", tr('python.terminal_tools.4758b3849f1a', p0=f'{budget:g}'))
                return
            if self._revision != revision:
                revision = self._revision
                last_change = now
            if now - last_change >= self.config.idle_silence:
                reason = "stdin_read" if self._looks_ready() else "inferred_idle"
                op.settle(reason)
                return
            await asyncio.sleep(self.config.poll_interval)

    def _looks_ready(self) -> bool:
        """提示符启发式：最后一行像 shell 提示符就说明它在等输入。"""
        last = self.buffer.last_line()
        if not last:
            return False
        return last.endswith(_PROMPT_TAIL)

    # -- 状态 ------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        if self.proc.returncode is None:
            return {"kind": "running"}
        code = int(self.proc.returncode)
        # asyncio 的 returncode 已是退出码或负信号号，不是 os.waitpid 的编码状态。
        if code < 0:
            return {"kind": "exited", "exit_code": None, "signal": -code}
        return {"kind": "exited", "exit_code": code, "signal": None}

    def read(self, offset: int, count: int) -> dict[str, Any]:
        """按行读取滚动缓冲窗口。"""
        count = max(1, min(int(count), 10_000))
        total = self.buffer.total_lines
        requested = max(0, min(int(offset), total))
        start = max(requested, self.buffer.dropped_lines)
        available = max(0, total - start)
        take = min(count, available)
        retained = self.buffer.snapshot()
        base = self.buffer.dropped_lines
        window = retained[start - base : start - base + take]
        return {
            "text": "\n".join(window),
            "totalLines": total,
            "lineBegin": start,
            "lineEnd": start + take,
            "truncated": bool(self.buffer.dropped_lines) or take < available,
        }

    def peek(self, count: int = DEFAULT_READ_LINES) -> dict[str, Any]:
        """页面用的只读尾窗，包含未换行进度且限制返回字符数。"""
        count = max(1, min(int(count), 2000))
        result = self.read(max(0, self.buffer.total_lines - count), count)
        pending, pending_lost = self.buffer.pending()
        limit = 32768
        result['truncated'] |= result['lineBegin'] > 0 or len(result['text']) > limit or pending_lost or len(pending) > limit
        removed = result['text'][:-limit] if len(result['text']) > limit else ''
        result['lineBegin'] += removed.count('\n')
        result['partialFirstLine'] = bool(removed and not removed.endswith('\n'))
        result['text'] = result['text'][-limit:]
        result['pendingText'] = pending[-limit:]
        return result

    def snapshot(self) -> dict[str, Any]:
        """对外可见的会话视图。"""
        view: dict[str, Any] = {
            "sessionId": self.id,
            "type": self.kind,
            "cwd": self.cwd,
            "pid": self.proc.pid,
            "status": self.status(),
            "transport": 'pty' if hasattr(self.proc, 'resize') else 'pipe',
            "output_encoding": self.decoder.encoding,
            **({"encoding_errors": [{"channel": "stdout", **self.decoder.error}]} if self.decoder.error else {}),
        }
        if self._read_error is not None:
            view['output_error'] = {'key': 'ui.terminal.outputFailed', 'params': {'error': str(self._read_error)}}
        if self.name:
            view["name"] = self.name
        if self._closing or self._cleanup_error:
            view['control'] = {'state': 'stopping' if self._closing else 'cleanup-failed'}
            if self._cleanup_error:
                view['control']['error'] = self._cleanup_error
        return view

    # -- 信号 ------------------------------------------------------------
    async def signal(self, name: str) -> dict[str, Any]:
        name = str(name).upper()
        if name not in TERMINAL_SIGNALS:
            raise TerminalError(tr('python.terminal_tools.0d0d000ff2a1', p0=f'{name}', p1=f"{', '.join(TERMINAL_SIGNALS)}"))
        if self.proc.returncode is not None:
            raise TerminalError(tr('python.terminal_tools.874919e65348', p0=f'{self.id}'))
        pid = int(self.proc.pid)
        try:
            if sys.platform == "win32":
                await self._signal_windows(name)
            elif hasattr(self.proc, 'signal_foreground'):
                self.proc.signal_foreground(_POSIX_SIGNALS[name]())
            else:
                pid = os.getpgid(pid)
                os.killpg(pid, _POSIX_SIGNALS[name]())
        except OSError as exc:
            raise TerminalError(tr('python.terminal_tools.80eb52f6bdf4', p0=str(exc))) from exc
        return {"delivered": True, "targetPgid": pid}

    async def _signal_windows(self, name: str) -> None:
        """Windows 上没有 killpg，逐级降级到 terminate / kill。"""
        if name == "SIGKILL":
            self.proc.kill()
            return
        if name == "SIGINT" and hasattr(self.proc, 'resize'):
            await self.write_input('\x03')
            return
        if name == "SIGINT":
            with contextlib.suppress(ProcessLookupError, OSError, ValueError, AttributeError):
                self.proc.send_signal(signal_mod.CTRL_BREAK_EVENT)
                return
        self.proc.terminate()

    # -- 关闭 ------------------------------------------------------------
    async def close(self, reason: str = "closed") -> str:
        """终止进程并回收句柄；重复调用返回 ``already-closing``。"""
        async with self._close_lock:
            if self._closed:
                return "already-closing"
            self._closing = True
            try:
                if not self._process_stopped:
                    if self._cleanup_error and self.proc.returncode is not None:
                        raise TerminalError(tr('python.terminal_tools.73d52d86fbea'))
                    await stop_process(self.proc)
                    # 终止已确认后只重试读取收尾，不再向可能复用的进程身份发信号。
                    self._process_stopped = True
                task = self._reader_task
                if task is not None and not task.cancelled():
                    await asyncio.wait_for(asyncio.shield(task), timeout=2)
                self._settle_all("session_exit", reason)
                self._reader_task = None
                self._cleanup_error = None
                self._close_transport()
                self._discard_scratch()
                self._closed = True
                return "closed"
            except asyncio.CancelledError:
                self._cleanup_error = tr('python.terminal_tools.dc5db4b6be09')
                raise
            except Exception as exc:
                self._cleanup_error = f'{type(exc).__name__}: {exc}'
                logger.warning(tr('log.a5494f0c86be'), self.id, self._cleanup_error, exc_info=True)
                return 'cleanup-failed'
            finally:
                self._closing = False

    def _close_transport(self) -> None:
        """显式关掉子进程的传输层，避免 ``ResourceWarning`` / 句柄泄漏。"""
        transport = getattr(self.proc, "_transport", None)
        if transport is not None:
            with contextlib.suppress(Exception):
                transport.close()

# ======================================================================
# 后台适配器：把一次输入包装成 JobRegistry 认识的「子进程」
# ======================================================================
class _SendStream:
    """给 :class:`JobRegistry` 用的 stdout 适配器。

    ``_pump`` 只会 ``await stream.read(n)`` 直到拿到空字节，所以这里轮询
    取出「自上次以来新增」的输出；操作结束后把剩余部分排空再返回 ``b""``。
    """

    __slots__ = ("_op", "_interval", "_buf")

    def __init__(self, op: TerminalSendOperation, interval: float = 0.02) -> None:
        self._op = op
        self._interval = max(0.001, float(interval))
        self._buf = ""

    async def read(self, size: int = 65_536) -> bytes:
        while True:
            if self._buf:
                take = max(1, int(size))
                chunk, self._buf = self._buf[:take], self._buf[take:]
                return chunk.encode('utf-8')
            text, _lost = self._op.read_output()
            if text:
                self._buf += text
                continue
            if self._op.is_done:
                return b""
            await asyncio.sleep(self._interval)


class _SendProcess:
    """让 :class:`TerminalSendOperation` 看起来像一个可管理的子进程。

    有了它，``job_output`` / ``job_kill`` / ``job_list`` 对「前台跑起来的长任务」
    和「后台跑起来的长任务」是同一套心智模型，模型不用记两套工具。
    """

    __slots__ = ("stdout", "stderr", "returncode", "_transport", "_op", "_session", "_outcome")

    def __init__(self, op: TerminalSendOperation, session: _TerminalSession) -> None:
        self.stdout = _SendStream(op)
        self.stderr = None
        self.returncode: int | None = None
        self._transport = None
        self._op = op
        self._session = session
        self._outcome = None

    def completion_snapshot(self) -> dict[str, Any]:
        return {'terminalId': self._session.id, 'terminalStatus': self._session.status(),
                **({'encoding_errors': self._session.snapshot()['encoding_errors']} if self._session.decoder.error else {}),
                **({'wait': dict(self._outcome)} if self._outcome else {})}

    async def wait(self) -> int:
        try:
            outcome = await self._op.done()
        except Exception:
            # 失败属于本次输入，作业回收不得因此向仍活着的终端发停止信号。
            if self.returncode is None:
                self.returncode = 1
            raise
        self._outcome = outcome
        if self.returncode is None:
            # 会话本身退出算失败，纯粹等到输出稳定算成功。
            self.returncode = 1 if outcome.get("reason") == "session_exit" else 0
        return self.returncode

    async def stop_owned(self) -> None:
        """投递完成后才落定等待；失败交由原作业报告，不伪造停止成功。"""
        if self.returncode is not None or self._op.is_done:
            return
        await self._session.signal("SIGINT")
        self.returncode = -signal_mod.SIGTERM
        self._op.request_cancel()


# ======================================================================
# 服务层
# ======================================================================
def _default_backends() -> dict[str, list[str]]:
    """默认终端类型表。"""
    if sys.platform == "win32":
        candidates = {
            "cmd": ["cmd.exe"],
            "powershell": ["powershell.exe", "-NoLogo", "-NoProfile"],
            "pwsh": ["pwsh.exe", "-NoLogo", "-NoProfile"],
        }
        # 目录与启动共用实际可执行文件，避免列出未安装的 Shell。
        available = {}
        for kind, argv in candidates.items():
            executable = shutil.which(argv[0])
            if executable:
                available[kind] = [executable, *argv[1:]]
        return available
    return {  # pragma: no cover - 平台分支
        "bash": ["/bin/bash", "--noprofile", "--norc", "+H", "-i"],
        "sh": ["/bin/sh", "-i"],
        **({'zsh': ['/bin/zsh', '-f', '-i']} if os.path.isfile('/bin/zsh') else {}),
    }


class TerminalSessionService:
    """持有全部常驻终端会话，并按 owner（agent 会话）隔离。

    生命周期：``spawn`` → 若干次 ``send``/``read`` → ``close``；harness 关闭时
    由 :meth:`close_all` 统一回收，避免留下孤儿 shell 进程。
    """

    def __init__(
        self,
        *,
        backends: dict[str, list[str]] | None = None,
        config: _TerminalConfig | None = None,
    ) -> None:
        self.config = config or _TerminalConfig()
        self.backends = dict(backends) if backends else _default_backends()
        #: 启动后等待横幅输出的宽限时间（测试可调小）
        self.handoff_grace = DEFAULT_HANDOFF_GRACE
        self._sessions: dict[str, _TerminalSession] = {}
        self._reserved: dict[str, set[str]] = {}
        self._next_id = 0
        self._spawns: dict[asyncio.Task, str] = {}
        self._closing_owners: dict[str, asyncio.Task] = {}
        self._close_all_task: asyncio.Task | None = None

    # -- 解析 ------------------------------------------------------------
    def resolve(self, kind: str) -> list[str]:
        """把模型给的 ``type`` 解析成 argv；也接受可执行文件路径/名字。"""
        key = str(kind).strip()
        if not key:
            raise TerminalError(tr('python.terminal_tools.dc6b29a3093c'))
        argv = self.backends.get(key)
        if argv is not None:
            return list(argv)
        candidate = os.path.expanduser(key)
        resolved = shutil.which(key) or (candidate if os.path.isfile(candidate) else None)
        if resolved:
            return [resolved]
        known = ", ".join(sorted(self.backends)) or tr('python.terminal_tools.c7bcc6d27f3a')
        raise TerminalError(tr('python.terminal_tools.b187f39dd0f9', p0=f'{key}', p1=f'{known}'))

    def _child_env(self, kind: str | None = None) -> dict[str, str]:
        env = execution_env()
        if sys.platform != 'win32':
            env['TERM'] = 'xterm-256color'
            if kind == 'zsh':
                env['PS1'] = '%1~ ❯ '
                # 隐藏未换行输出的提示标记，保留命令本身输出的百分号。
                env['PROMPT_EOL_MARK'] = ''
        if sys.platform == "win32":
            # 子 shell 里再跑 python 时，管道按 ANSI 代码页解码；显式钉住编码，
            # 否则中文输出会变成乱码（和 shell_tools 里踩的是同一个坑）。
            env["PYTHONIOENCODING"] = _CHILD_IO_ENCODING
        env.pop("PYTHONSTARTUP", None)
        return env

    # -- 创建 ------------------------------------------------------------
    async def spawn(self, *, owner: str, kind: str, name: str | None = None,
                    cwd: str | None = None, output_encoding: str = "utf-8") -> dict[str, Any]:
        """先登记创建归属；关闭等待已接纳创建，并拒绝交叠的新建。"""
        owner = str(owner)
        if self._close_all_task is not None or owner in self._closing_owners:
            raise TerminalError(tr('terminal.resources_closing'))
        encoding = resolve_output_encoding(output_encoding)
        if sys.platform == 'win32' and encoding != 'utf-8':
            # ConPTY 返回 Unicode，适配器对外固定 UTF-8 字节，不可再次按代码页解码。
            raise TerminalError(tr('encoding.terminal_transport'))
        task = asyncio.create_task(self._spawn(owner=owner, kind=kind, name=name, cwd=cwd, output_encoding=encoding))
        self._spawns[task] = owner
        try:
            return await task
        finally:
            self._spawns.pop(task, None)

    async def _spawn(
        self,
        *,
        owner: str,
        kind: str,
        name: str | None = None,
        cwd: str | None = None,
        output_encoding: str = "utf-8",
    ) -> dict[str, Any]:
        """起一个新的常驻终端，返回对外视图（含启动横幅）。"""
        if not str(owner):
            raise TerminalError(tr('python.terminal_tools.89c9dfedbe60'))
        key = str(kind).strip()
        alias = str(name).strip() if name is not None else ""
        alias = alias or None
        if alias is not None:
            taken = self._reserved.setdefault(str(owner), set())
            if alias in taken:
                raise TerminalError(
                    tr('python.terminal_tools.6fbf54c708f3', p0=f'{alias}')
                )
        argv = self.resolve(key)
        workdir = os.path.abspath(str(cwd)) if cwd else os.getcwd()
        if not os.path.isdir(workdir):
            raise TerminalError(tr('python.terminal_tools.26ad00704782', p0=f'{workdir}'))
        if alias is not None:
            self._reserved.setdefault(str(owner), set()).add(alias)
        creation_cancelled = False
        try:
            if sys.platform != 'win32':
                from ..pty_process import spawn_pty
                proc = await spawn_pty(argv, cwd=workdir, env=self._child_env(key))
            else:
                from ..windows_conpty import conpty_api
                try:
                    conpty_api()
                except ImportError:
                    from ..windows_shell import windows_console_argv
                    pipe_argv = windows_console_argv(argv)
                    creating = asyncio.create_task(asyncio.create_subprocess_exec(
                        *pipe_argv, stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                        cwd=workdir, env=self._child_env(key), creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW,
                    ))
                    # 创建不能撤销：保留名称，接收迟到句柄后沿会话关闭路径回收。
                    while True:
                        try:
                            proc = await asyncio.shield(creating)
                            break
                        except asyncio.CancelledError:
                            if creating.cancelled():
                                raise
                            creation_cancelled = True
                else:
                    from ..conpty_process import spawn_conpty
                    proc = await spawn_conpty(argv, cwd=workdir, env=self._child_env(key))
        except BaseException as exc:
            if alias is not None:
                self._reserved.get(str(owner), set()).discard(alias)
            if isinstance(exc, (OSError, ValueError)):
                raise TerminalError(tr('python.terminal_tools.1cf9b16b1d52', p0=f'{key}', p1=f'{exc}')) from exc
            raise

        self._next_id += 1
        session_id = f"pty-{self._next_id}"
        session = _TerminalSession(
            session_id=session_id,
            owner=str(owner),
            kind=key,
            name=alias,
            cwd=workdir,
            proc=proc,
            config=self.config,
            output_encoding=output_encoding,
        )
        self._sessions[session_id] = session
        session.start_reader()
        try:
            if creation_cancelled:
                raise asyncio.CancelledError
            motd = await self._collect_motd(session)
        except BaseException:
            # 被取消或超时的打开操作尚未交付终端标识。
            # 返回前回收其进程；清理失败仍须在同一标识下可见，
            # 并保留名称占用，便于检查。
            await join_cleanup(asyncio.create_task(self.close(str(owner), session_id)))
            raise
        view = session.snapshot()
        view["motd"] = motd
        return view

    async def _collect_motd(self, session: _TerminalSession) -> str:
        """在固定截止时间内收集启动输出，即使服务端输出密集也不延长。"""
        grace = float(self.handoff_grace)
        if not math.isfinite(grace):
            raise TerminalError(tr('python.terminal_tools.d7293f5046a3'))
        grace = max(0.0, grace)
        if grace > 0:
            deadline = time.monotonic() + grace
            quiet_deadline = deadline
            revision = session._revision
            while True:
                remaining = min(deadline, quiet_deadline) - time.monotonic()
                if remaining <= 0:
                    break
                if session._reader_task is not None and session._reader_task.done():
                    # 流结束与进程退出回调可能竞态，只能在原启动预算内等待。
                    # 仅关闭标准输出不能证明进程已退出，
                    # 也不能让启动过程无限阻塞。
                    if session.proc.returncode is None:
                        try:
                            async with asyncio.timeout(remaining):
                                await session.proc.wait()
                        except TimeoutError:
                            pass
                    break
                # 清除通知并等待时，状态检查之间不让出执行权。
                # 输出或流结束会唤醒等待，不定期轮询，也不重置截止时间。
                session._output_changed.clear()
                try:
                    async with asyncio.timeout(remaining):
                        await session._output_changed.wait()
                except TimeoutError:
                    break
                if session._revision != revision:
                    revision = session._revision
                    quiet_deadline = min(deadline, time.monotonic() + min(0.2, grace))
        lines = session.buffer.snapshot()
        pending, lost = session.buffer.pending()
        if pending:
            lines.append(pending)
        text = '\n'.join(line.rstrip() for line in lines).strip()
        limit = min(self.config.max_read_bytes, DEFAULT_STARTUP_RESULT_BYTES)
        if lost or len(text.encode('utf-8')) > limit:
            # 启动预览被截断时，保留滚动回看内容。
            # 若将其排空，后续便无法检查诊断信息。
            return _bound_terminal_text(text, limit) + tr('python.terminal_tools.c0c91906173b')
        session.buffer.drain()
        return text

    # -- 查询 ------------------------------------------------------------
    def expect_owned(self, owner: str, session_id: str) -> _TerminalSession:
        """取会话；不属于该 owner 或不存在都算 LookupError。"""
        session = self._sessions.get(str(session_id))
        if session is None or session.owner != str(owner):
            raise LookupError(
                tr('python.terminal_tools.eaa51f98f69f', p0=f'{session_id}')
            )
        return session

    def list(self, owner: str) -> list[dict[str, Any]]:
        return [s.snapshot() for s in self._sessions.values() if s.owner == str(owner)]

    def list_all(self) -> list[dict[str, Any]]:
        """跨 owner 的全量视图，供 Web 可观测面板总览用。

        与 :meth:`list` 的区别只在不做 owner 过滤 —— 工具层永远按会话隔离，
        但设置面板 / 运行时面板要回答"这台机器上现在总共有哪些终端在跑"。
        快照里带 ``sessionId``，前端据此分组即可，不必再猜归属。
        """
        return [s.snapshot() for s in self._sessions.values()]

    def owners(self) -> set[str]:
        """当前持有终端的 owner 集合。

        调用方据此判断"能不能省略 session_id"：恰好一个 owner 时可以安全
        省略，多于一个就必须显式指定，否则可能读到别人的终端。
        """
        return {str(session.owner) for session in self._sessions.values()}

    # -- 关闭 ------------------------------------------------------------
    async def close(self, owner: str, session_id: str) -> dict[str, Any]:
        session = self.expect_owned(owner, session_id)
        outcome = await session.close()
        # 会话留在表里：进程没了，但滚动缓冲还能读，方便事后看最后几行输出。
        # 名字立刻释放，同一会话可以马上用同一个名字重开。
        if session.name and session.is_closed:
            names = self._reserved.get(str(owner))
            if names is not None:
                names.discard(session.name)
        result = {"sessionId": session.id, "outcome": outcome}
        if session._cleanup_error:
            result['cleanupError'] = session._cleanup_error
        return result

    async def close_owner(self, owner: str) -> None:
        """并发关闭共享所属回收；等待者取消不遗落创建或回收。"""
        owner = str(owner)
        task = self._closing_owners.get(owner)
        if task is None:
            task = asyncio.create_task(self._close_owner(owner))
            self._closing_owners[owner] = task
        try:
            await join_cleanup(task)
        finally:
            if task.done() and self._closing_owners.get(owner) is task:
                self._closing_owners.pop(owner, None)

    async def _close_owner(self, owner: str) -> None:
        """创建结束后再收集实例；失败实例与名称保留以便核实。"""
        pending = [task for task, source in self._spawns.items() if source == owner]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        errors = []
        for session in list(self._sessions.values()):
            if session.owner != owner:
                continue
            try:
                await self.close(owner, session.id)
                if not session.is_closed:
                    raise RuntimeError(session._cleanup_error or tr('terminal.cleanup_incomplete'))
                self._sessions.pop(session.id, None)
            except (Exception, asyncio.CancelledError) as exc:
                errors.append(exc)
        if not errors:
            self._reserved.pop(owner, None)
        raise_cleanup_errors(errors)

    async def close_all(self) -> None:
        """共享全量关闭；覆盖尚在创建及单个 owner 正在回收的资源。"""
        if self._close_all_task is None:
            self._close_all_task = asyncio.create_task(self._close_all())
        task = self._close_all_task
        try:
            await join_cleanup(task)
        finally:
            if task.done() and self._close_all_task is task:
                self._close_all_task = None

    async def _close_all(self) -> None:
        """逐个 owner 尝试，任何失败均不跳过其他 owner。"""
        owners = self.owners() | set(self._spawns.values()) | set(self._closing_owners)
        errors = []
        for owner in sorted(owners):
            try:
                await self.close_owner(owner)
            except (Exception, asyncio.CancelledError) as exc:
                errors.append(exc)
        raise_cleanup_errors(errors)


# ======================================================================
# 渲染（与原 render.ts 同构的标记）
# ======================================================================
def _bound_terminal_text(text: str, max_bytes: int) -> str:
    """按 UTF-8 字节上限安全截断，并补上截断标记。"""
    raw = str(text).encode("utf-8")
    limit = max(MIN_MAX_RESULT_BYTES, int(max_bytes))
    if len(raw) <= limit:
        return str(text)
    head = raw[: max(0, limit - _TRUNCATED_BYTES)]
    return utf8_prefix(head, truncated=True) + TRUNCATED


def _session_line(status: dict[str, Any]) -> str:
    if status.get("kind") == "running":
        return tr('terminal.feedback.session_running')
    code = status.get("exit_code")
    return tr('terminal.feedback.session_exit_code', code=code) if code is not None else tr('terminal.feedback.session_exit_signal', signal=status.get('signal'))


def _error(message: str) -> dict[str, Any]:
    return {"error": True, "message": str(message)}


# ======================================================================
# 工具
# ======================================================================
def terminal_execution_cwd(args: Any, ctx: ToolRunContext) -> Path:
    """相对终端目录基于当前聊天解析，范围核验与实际启动共用结果。"""
    base = Path(ctx.cwd or os.getcwd()).expanduser().resolve()
    requested = args.get('cwd')
    if requested:
        candidate = Path(requested).expanduser()
        return (candidate if candidate.is_absolute() else base / candidate).resolve()
    return base


def build_terminal_tools(
    service: TerminalSessionService,
    *,
    registry: Any = None,
) -> list[ToolDefinition]:
    """构造持久终端工具（open / send / read / signal / close / list）。"""

    async def _spawn_send_job(
        session: _TerminalSession, text: str, submit: bool, ctx: ToolRunContext
    ) -> dict[str, Any]:
        if registry is None:
            raise TerminalError(tr('python.terminal_tools.8142b9d67b14'))
        op = await session.start_send(text, submit=submit, background=True, execution_check=ctx.check_execution)
        proc = _SendProcess(op, session)

        async def _ready() -> Any:
            return proc

        label_text = text.strip()
        label = f"{session.id}: {label_text}" if label_text else tr('terminal.feedback.background_input_label', id=session.id)
        record = await registry.spawn(
            kind="pty-send", label=label, owner=ctx.session_id, spawn=_ready
        )
        return {
            "kind": "background",
            "jobId": record.id,
            "sessionId": session.id,
            "session": session.snapshot(),
        }

    async def terminal_open(args: Any, ctx: ToolRunContext) -> Any:
        try:
            return await service.spawn(
                owner=ctx.session_id,
                kind=str(args["type"]),
                name=args.get("name"),
                output_encoding=args.get("output_encoding", "utf-8"),
                cwd=str(terminal_execution_cwd(args, ctx)),
            )
        except TerminalError as exc:
            return _error(exc)

    def terminal_open_to_content(args: Any, value: Any) -> list[TextBlock]:
        if value.get("error"):
            return [TextBlock(text=str(value.get("message", tr('python.terminal_tools.0cf642cf998c'))))]
        head = tr('terminal.feedback.started', id=value['sessionId'])
        if value.get("name"):
            head += f" ({value['name']})"
        head += tr('terminal.feedback.type', type=value['type'])
        head += '\n' + _session_line(value.get('status') or {})
        motd = str(value.get("motd") or "").strip()
        return [TextBlock(text=f"{head}\n{motd or tr('feedback.79ec43377773')}")]

    async def terminal_send(args: Any, ctx: ToolRunContext) -> Any:
        session_id = str(args["session_id"])
        text = str(args["text"])
        submit = args.get("submit", True) is not False
        background = bool(args.get("run_in_background"))
        try:
            session = service.expect_owned(ctx.session_id, session_id)
            if background:
                return await _spawn_send_job(session, text, submit, ctx)
            op = await session.start_send(text, submit=submit, background=False, execution_check=ctx.check_execution)
        except LookupError as exc:
            return _error(exc)
        except TerminalError as exc:
            return _error(exc)
        try:
            outcome = await op.done()
        except asyncio.CancelledError:
            op.request_cancel()
            async def finish():
                await service.close(ctx.session_id, session.id)
                if not session.is_closed:
                    raise ProcessCleanupError(tr('terminal.cleanup_incomplete'),
                                              terminal_id=session.id, detail=session._cleanup_error)
            await join_cleanup(asyncio.create_task(finish()))
            raise
        body, _lost = op.read_output()
        return {
            "sessionId": session_id,
            "text": _bound_terminal_text(body, service.config.max_read_bytes),
            "wait": outcome,
            "session": session.status(),
            "encoding_errors": session.snapshot().get("encoding_errors", []),
        }

    def terminal_send_to_content(args: Any, value: Any) -> list[TextBlock]:
        if value.get("error"):
            return [TextBlock(text=str(value.get("message", tr('python.terminal_tools.ac77953a1e06'))))]
        if value.get("kind") == "background":
            return [
                TextBlock(
                    text=(
                        tr('python.terminal_tools.f59fb6152b70', p0=f"{value['sessionId']}", p1=f"{value['jobId']}")
                    )
                )
            ]
        wait = value.get("wait") or {}
        lines = [str(value.get("text") or "") or tr('feedback.85d78cce8183')]
        reason = str(wait.get("reason") or "timeout")
        detail = wait.get("detail")
        lines.append(f"[wait: {reason}]" + (f" ({detail})" if detail else ""))
        lines.append(_session_line(value.get("session") or {}))
        return [TextBlock(text="\n".join(lines))]

    def terminal_send_display(args: Any, value: Any) -> dict[str, Any] | None:
        if value.get('error'):
            return None
        if value.get('kind') == 'background':
            return {'background': True, 'job_id': value['jobId'], 'sessionId': value['sessionId'], 'completionScope': 'terminal-wait'}
        return {'sessionId': value['sessionId'], 'terminal_wait': value['wait'], 'terminal_status': value['session']}

    async def terminal_read(args: Any, ctx: ToolRunContext) -> Any:
        session_id = str(args["session_id"])
        try:
            session = service.expect_owned(ctx.session_id, session_id)
        except LookupError as exc:
            return _error(exc)
        try:
            offset = int(args.get("offset", 0))
        except (TypeError, ValueError):
            offset = 0
        try:
            count = int(args.get("count", DEFAULT_READ_LINES))
        except (TypeError, ValueError):
            count = DEFAULT_READ_LINES
        window = session.read(offset, count)
        pending, lost = session.buffer.pending()
        if pending and window['lineEnd'] == window['totalLines']:
            # 提示符和进度通常没有换行；只有请求窗口到达实时尾部时，
            # 才将其纳入，并提供明确元数据，
            # 让已完成行的偏移量保持原有含义。
            window['text'] += ('\n' if window['text'] else '') + pending
            window['partialLine'] = window['totalLines']
            window['truncated'] |= lost
        window["sessionId"] = session_id
        window["session"] = session.status()
        snapshot = session.snapshot()
        window["encoding_errors"] = snapshot.get("encoding_errors", [])
        if "output_error" in snapshot:
            window["output_error"] = snapshot["output_error"]
        window["text"] = _bound_terminal_text(window["text"], service.config.max_read_bytes)
        return window

    def terminal_read_to_content(args: Any, value: Any) -> list[TextBlock]:
        if value.get("error"):
            return [TextBlock(text=str(value.get("message", tr('python.terminal_tools.6aa27f14bff1'))))]
        body = str(value.get("text") or "")
        if value.get('output_error'):
            body += '\n' + tr(value['output_error']['key'], **value['output_error']['params'])
        for issue in value.get('encoding_errors', []):
            body += '\n' + tr('encoding.failure', **{key: issue[key] for key in ('channel', 'encoding', 'offset')})
        header = (
            f"[lines: {value['lineBegin']}-{value['lineEnd']} of {value['totalLines']}]"
        )
        if 'partialLine' in value:
            header += f"\n[partial line: {value['partialLine']}]"
        if not body:
            return [TextBlock(text=f"{tr('terminal.feedback.no_retained_output')}\n{header}")]
        return [TextBlock(text=f"{body}\n{header}")]

    async def terminal_signal(args: Any, ctx: ToolRunContext) -> Any:
        session_id = str(args["session_id"])
        name = str(args["signal"]).upper()
        try:
            session = service.expect_owned(ctx.session_id, session_id)
        except LookupError as exc:
            return _error(exc)
        try:
            delivered = await session.signal(name)
        except TerminalError as exc:
            return _error(exc)
        return {
            "sessionId": session_id,
            "signal": name,
            "session": session.status(),
            **delivered,
        }

    def terminal_signal_to_content(args: Any, value: Any) -> list[TextBlock]:
        if value.get("error"):
            return [TextBlock(text=str(value.get("message", tr('python.terminal_tools.47fdad398aaf'))))]
        return [
            TextBlock(
                text=(
                    tr('python.terminal_tools.42b469ac1db3', p0=f"{value['sessionId']}", p1=f"{value['signal']}")
                    + _session_line(value.get("session") or {})
                )
            )
        ]

    async def terminal_close(args: Any, ctx: ToolRunContext) -> Any:
        session_id = str(args["session_id"])
        try:
            return await service.close(ctx.session_id, session_id)
        except LookupError as exc:
            return _error(exc)

    def terminal_close_to_content(args: Any, value: Any) -> list[TextBlock]:
        if value.get("error"):
            return [TextBlock(text=str(value.get("message", tr('python.terminal_tools.7e71fa88e1de'))))]
        if value["outcome"] == "already-closing":
            return [TextBlock(text=tr('python.terminal_tools.bc53f0f4f1ee', p0=f"{value['sessionId']}"))]
        if value['outcome'] == 'cleanup-failed':
            return [TextBlock(text=tr('python.terminal_tools.cbc1b89f9a05', p0=f"{value['sessionId']}", p1=f"{value.get('cleanupError', '')}"))]
        return [TextBlock(text=tr('python.terminal_tools.873edd1dd0ac', p0=f"{value['sessionId']}"))]

    async def terminal_list(args: Any, ctx: ToolRunContext) -> Any:
        return service.list(ctx.session_id)

    def terminal_list_to_content(args: Any, value: Any) -> list[TextBlock]:
        if not value:
            return [TextBlock(text=tr('feedback.f773c36f86d7'))]
        lines = []
        for item in value:
            head = str(item["sessionId"])
            if item.get("name"):
                head += f" ({item['name']})"
            status = item.get("status") or {}
            tail = (
                tr('fallback.running')
                if status.get("kind") == "running"
                else tr('terminal.feedback.exited', code=status.get('exit_code'))
            )
            lines.append(f"{head} [type: {item['type']}] {tail} pid={item['pid']}")
        return [TextBlock(text="\n".join(lines))]

    return [
        ToolDefinition(
            schema={
                "description_key": 'python.terminal_tools.1aafcc7f900f',
                "parameter_keys": {
                    ('properties', 'type', 'description'): 'python.terminal_tools.10927876b98b',
                    ('properties', 'output_encoding', 'description'): 'encoding.parameter',
                    ('properties', 'name', 'description'): 'python.terminal_tools.9094e1349b8b',
                    ('properties', 'cwd', 'description'): 'python.terminal_tools.446f3bd6558f',
                },
                "name": "terminal_open",
                "description": (
                    tr('python.terminal_tools.1aafcc7f900f')
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "output_encoding": {"type": "string", "description": tr("encoding.parameter")},
                        "type": {
                            "type": "string",
                            "description": (
                                tr('python.terminal_tools.10927876b98b')
                            ),
                        },
                        "name": {
                            "type": "string",
                            "description": tr('python.terminal_tools.9094e1349b8b'),
                        },
                        "cwd": {"type": "string", "description": tr('python.terminal_tools.446f3bd6558f')},
                    },
                    "required": ["type"],
                    "additionalProperties": False,
                },
            },
            execute=terminal_open,
            argument_semantics={'cwd': 'path', 'name': 'text'},
            execution_cwd=terminal_execution_cwd,
            to_content=terminal_open_to_content,
            describe=lambda args, value: {'sessionId': value['sessionId']} if value.get('sessionId') else None,
            timeout=60.0,
        ),
        ToolDefinition(
            schema={
                "description_key": 'python.terminal_tools.edaef679e639',
                "parameter_keys": {
                    ('properties', 'session_id', 'description'): 'python.terminal_tools.af3e3983d192',
                    ('properties', 'text', 'description'): 'python.terminal_tools.6f235d970a23',
                    ('properties', 'submit', 'description'): 'python.terminal_tools.7140a1337c04',
                    ('properties', 'run_in_background', 'description'): 'python.terminal_tools.190c02a891e8',
                },
                "name": "terminal_send",
                "description": (
                    tr('python.terminal_tools.edaef679e639')
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string", "description": tr('python.terminal_tools.af3e3983d192')},
                        "text": {"type": "string", "description": tr('python.terminal_tools.6f235d970a23')},
                        "submit": {
                            "type": "boolean",
                            "description": tr('python.terminal_tools.7140a1337c04'),
                        },
                        "run_in_background": {
                            "type": "boolean",
                            "description": tr('python.terminal_tools.190c02a891e8'),
                        },
                    },
                    "required": ["session_id", "text"],
                    "additionalProperties": False,
                },
            },
            execute=terminal_send,
            argument_semantics={'text': 'shell'},
            to_content=terminal_send_to_content,
            describe=terminal_send_display,
            timeout=None,
        ),
        ToolDefinition(
            schema={
                "description_key": 'python.terminal_tools.7cbe1738e976',
                "parameter_keys": {
                    ('properties', 'session_id', 'description'): 'python.terminal_tools.8a8b1f274e12',
                    ('properties', 'offset', 'description'): 'python.terminal_tools.85695cdc746e',
                    ('properties', 'count', 'description'): ('python.terminal_tools.9b17d9dcfe2e', {'p0': f'{DEFAULT_READ_LINES}'}),
                },
                "name": "terminal_read",
                "description": tr('python.terminal_tools.7cbe1738e976'),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string", "description": tr('python.terminal_tools.8a8b1f274e12')},
                        "offset": {"type": "integer", "description": tr('python.terminal_tools.85695cdc746e')},
                        "count": {
                            "type": "integer",
                            "description": tr('python.terminal_tools.9b17d9dcfe2e', p0=f'{DEFAULT_READ_LINES}'),
                        },
                    },
                    "required": ["session_id"],
                    "additionalProperties": False,
                },
            },
            execute=terminal_read,
            to_content=terminal_read_to_content,
            concurrency_safe=True,
        ),
        ToolDefinition(
            schema={
                "description_key": 'python.terminal_tools.f4aae699d11b',
                "parameter_keys": {
                    ('properties', 'session_id', 'description'): 'python.terminal_tools.8a8b1f274e12',
                    ('properties', 'signal', 'description'): 'python.terminal_tools.78379961b992',
                },
                "name": "terminal_signal",
                "description": tr('python.terminal_tools.f4aae699d11b'),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string", "description": tr('python.terminal_tools.8a8b1f274e12')},
                        "signal": {
                            "type": "string",
                            "enum": list(TERMINAL_SIGNALS),
                            "description": tr('python.terminal_tools.78379961b992'),
                        },
                    },
                    "required": ["session_id", "signal"],
                    "additionalProperties": False,
                },
            },
            execute=terminal_signal,
            to_content=terminal_signal_to_content,
        ),
        ToolDefinition(
            schema={
                "description_key": 'python.terminal_tools.4a3f0d76b7b7',
                "parameter_keys": {
                    ('properties', 'session_id', 'description'): 'python.terminal_tools.8a8b1f274e12',
                },
                "name": "terminal_close",
                "description": tr('python.terminal_tools.4a3f0d76b7b7'),
                "parameters": {
                    "type": "object",
                    "properties": {"session_id": {"type": "string", "description": tr('python.terminal_tools.8a8b1f274e12')}},
                    "required": ["session_id"],
                    "additionalProperties": False,
                },
            },
            execute=terminal_close,
            to_content=terminal_close_to_content,
        ),
        ToolDefinition(
            schema={
                "description_key": 'python.terminal_tools.8bd7bbe06249',
                "name": "terminal_list",
                "description": tr('python.terminal_tools.8bd7bbe06249'),
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
            execute=terminal_list,
            to_content=terminal_list_to_content,
            concurrency_safe=True,
        ),
    ]


__all__ = [
    "DEFAULT_HANDOFF_GRACE",
    "DEFAULT_IDLE_SILENCE",
    "DEFAULT_MAX_READ_BYTES",
    "DEFAULT_MAX_RESULT_BYTES",
    "DEFAULT_READ_LINES",
    "DEFAULT_SCROLLBACK_LINES",
    "DEFAULT_SEND_TIMEOUT",
    "MIN_MAX_RESULT_BYTES",
    "TERMINAL_SIGNALS",
    "TRUNCATED",
    "WAIT_REASONS",
    "TerminalError",
    "TerminalSendOperation",
    "TerminalSessionService",
    "build_terminal_tools",
]
