"""内置后台作业工具：``job_output`` / ``job_list`` / ``job_kill``。

同时为 ``bash`` 的 ``run_in_background`` 分支提供收集能力。

关键语义：

* 作业按会话（owner）隔离，模型只能看到自己会话启动的作业；
* 输出用「有界尾部缓冲 + 单调游标」保存，对应 ``fromByte`` 游标与
  ``lossy`` 标记；
* ``job_output`` 传 ``wait: true`` 时，超时只返回作业当前状态而**不**抛工具
  超时错误，作业本身继续运行；
* 省略 ``wait`` 时等新输出或状态变化，``wait: false`` 才是立即查询；
* 非零退出码记为 ``completed``（而非失败），只有启动失败才是 ``failed``，
  与原 ``processOutcome`` 的映射一致。
"""

from __future__ import annotations

from harness.i18n import tr

import asyncio
import contextlib
import logging
import os
import signal
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from ...core.cleanup import raise_cleanup_errors
from ...llm.types import TextBlock
from ..registry import ToolDefinition, ToolRunContext
from ...core.text_encoding import TextDecoder
from ...core.text_encoding import output_encoding as resolve_output_encoding
from ..job_archive import JobArchive, OUTPUT_CHARS, completion_view

#: 终止作业后等待子进程真正退出的上限（秒）
KILL_REAP_TIMEOUT = 5.0

#: 单个通道保留的最大字符数
DEFAULT_MAX_RESULT_BYTES = 256 * 1024
#: ``job_output`` 的默认等待毫秒
WAIT_DEFAULT_MS = 30_000
#: ``job_output`` 的等待上限毫秒
WAIT_CAP_MS = 600_000
logger = logging.getLogger(__name__)

#: 已经落定的作业终态
_SETTLED = frozenset({"completed", "killed", "failed"})


class TextTail:
    """只保留尾部若干字符的有界文本缓冲。

    用 ``codecs`` 的增量解码器按块喂入字节，避免多字节字符（中文）被
    块边界切成两半而变成乱码——这正是控制台编码问题最容易复现的地方。
    """

    __slots__ = ("_chunks", "_max", "_total", "_dropped", "_decoder")

    def __init__(self, max_chars: int, encoding: str = "utf-8") -> None:
        self._chunks: list[str] = []
        self._max = max(1, int(max_chars))
        self._total = 0
        self._dropped = 0
        self._decoder = TextDecoder(encoding)

    def append_bytes(self, data: bytes) -> None:
        text = self._decoder.decode(data)
        if text:
            self.append_text(text)

    def flush(self) -> None:
        """把解码器里残留的半个字符吐出来。"""
        text = self._decoder.decode(b"", True)
        if text:
            self.append_text(text)

    def append_text(self, text: str) -> None:
        if not text:
            return
        self._chunks.append(text)
        self._total += len(text)
        overflow = self._total - self._max
        while overflow > 0 and self._chunks:
            head = self._chunks[0]
            if len(head) <= overflow:
                overflow -= len(head)
                self._total -= len(head)
                self._dropped += len(head)
                self._chunks.pop(0)
            else:
                self._chunks[0] = head[overflow:]
                self._total -= overflow
                self._dropped += overflow
                overflow = 0

    @property
    def total(self) -> int:
        """累计写入的字符数（单调递增，用作游标）。"""
        return self._total + self._dropped

    def read_from(self, offset: int) -> tuple[str, int, bool]:
        """从 ``offset`` 处读取增量。

        返回 ``(文本, 新的游标, 是否发生过丢弃)``。当游标早于仍保留的窗口
        时，缺失的部分已经无法补回，``lossy`` 为 ``True``。
        """
        lossy = offset < self._dropped
        actual = max(offset, self._dropped)
        parts: list[str] = []
        pos = self._dropped
        for chunk in self._chunks:
            end = pos + len(chunk)
            if end > actual:
                parts.append(chunk[max(0, actual - pos) :])
            pos = end
        return "".join(parts), self.total, lossy


@dataclass
class JobRecord:
    """一个后台作业的运行时记录。"""

    id: str
    kind: str
    label: str
    owner: str
    started_at: int
    status: str = "running"
    detail: str | None = None
    finished_at: int | None = None
    exit_code: int | None = None
    stdout: TextTail = field(default_factory=lambda: TextTail(DEFAULT_MAX_RESULT_BYTES))
    stderr: TextTail = field(default_factory=lambda: TextTail(DEFAULT_MAX_RESULT_BYTES))
    cursors: dict[str, int] = field(default_factory=lambda: {"stdout": 0, "stderr": 0})
    process: Any = None
    settled: asyncio.Event | None = None
    task: asyncio.Task[Any] | None = None
    control_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    changed: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    cleanup_error: str | None = None
    cleanup_context: dict[str, Any] | None = None
    archive_error: str | None = None
    completion: dict[str, Any] | None = None

    @property
    def is_settled(self) -> bool:
        return self.status in _SETTLED

    def public(self) -> dict[str, Any]:
        """对外暴露的作业视图（PUBLIC_JOB_SCHEMA）。"""
        view: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "status": self.status,
            "startedAt": self.started_at,
        }
        view['output_encoding'] = self.stdout._decoder.encoding
        errors = [{'channel': name, **tail._decoder.error} for name, tail in
                  (('stdout', self.stdout), ('stderr', self.stderr)) if tail._decoder.error]
        if errors:
            view['encoding_errors'] = errors
        if self.detail:
            view["detail"] = self.detail
        if self.finished_at is not None:
            view["finishedAt"] = self.finished_at
        if self.exit_code is not None:
            view["exitCode"] = self.exit_code
        if self.cleanup_error:
            view["cleanupError"] = self.cleanup_error
        if self.cleanup_context is not None:
            view.update(self.cleanup_context)
        if self.archive_error:
            view['archiveError'] = self.archive_error
        if self.completion:
            view.update({key: dict(value) if isinstance(value, dict) else value for key, value in self.completion.items()})
        return completion_view(view)

def _now_ms() -> int:
    return int(time.time() * 1000)


class JobRegistry:
    """会话隔离的后台作业注册表。

    生命周期与 harness 相同：作业由 ``spawn`` 启动后由后台任务持续泵取输出，
    不随触发它的那次工具调用结束而中断。
    """

    def __init__(self, *, max_result_bytes: int = DEFAULT_MAX_RESULT_BYTES, archive: JobArchive | None = None) -> None:
        self._max_chars = max(1, int(max_result_bytes))
        self._jobs: dict[str, JobRecord] = {}
        self._counter = 0
        self._instance = uuid.uuid4().hex
        self.archive = archive

    # -- 内部 ------------------------------------------------------------
    def _next_id(self) -> str:
        self._counter += 1
        return f"job_{self._instance}_{self._counter}"

    async def _save(self, job: JobRecord) -> None:
        if self.archive is None:
            return
        snapshot = self.peek(job.id, job.owner, limit=OUTPUT_CHARS)
        # 观察者取消后，不能让延迟的启动快照覆盖较新的最终快照。
        # 输出泵继续前先完成本次写入。
        writer = asyncio.create_task(asyncio.to_thread(self.archive.save, job.owner, snapshot))
        cancelled = False
        while not writer.done():
            try:
                await asyncio.wait({writer})
            except asyncio.CancelledError:
                cancelled = True
        try:
            writer.result()
            job.archive_error = None
        except Exception:
            job.archive_error = tr('python.job_tools.a2180f96db34')
            logger.exception(tr('log.9da39c960c40'), job.id)
        if cancelled:
            raise asyncio.CancelledError

    async def history_page(self, owner: str | None, *, before: str | None = None, limit: int = 50) -> dict[str, Any]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError(tr('python.job_tools.efebacc0cde9'))
        try:
            page = await asyncio.to_thread(self.archive.page, owner, before, limit) if self.archive else {'jobs': [], 'has_more': False, 'next_before': None}
        except LookupError:
            raise
        except Exception:
            if before:
                raise
            # 存储失败不能隐藏仍活动的句柄或其停止操作。
            page = {'jobs': [], 'has_more': False, 'next_before': None,
                    'history_error': tr('python.job_tools.36383deb2333')}
        rows = {row['id']: row for row in page['jobs']}
        # 每个活动句柄都必须可见，实时证据优先于快照。
        for job in self._jobs.values():
            if (owner is None or job.owner == owner) and (self.archive is None or page.get('history_error') or not job.is_settled or job.id in rows or job.archive_error):
                rows[job.id] = job.public()
        page['jobs'] = sorted(rows.values(), key=lambda row: (row['status'] in _SETTLED or row.get('archived', False), -row['startedAt'], row['id']))
        return page

    async def output_snapshot(self, job_id: str, owner: str, *, limit: int = OUTPUT_CHARS) -> dict[str, Any]:
        if type(limit) is not int or not 1 <= limit <= OUTPUT_CHARS:
            raise ValueError(tr('python.job_tools.6d4d8c488423'))
        try:
            return self.peek(job_id, owner, limit=limit)
        except LookupError:
            if self.archive is None:
                raise
            return await asyncio.to_thread(self.archive.output, owner, job_id, limit)

    def _require(self, job_id: str, owner: str) -> JobRecord:
        job = self._jobs.get(str(job_id))
        # 作业按会话隔离：别人的 id 视同不存在，避免跨会话泄漏执行状态。
        if job is None or job.owner != owner:
            raise LookupError(str(job_id))
        return job

    # -- 启动 ------------------------------------------------------------
    def retain_cleanup(self, proc: Any, ctx: ToolRunContext, exc: BaseException, *,
                       kind: str, label: str, stdout: bytes | bytearray = b"", stderr: bytes | bytearray = b"",
                       output_encoding: str = "utf-8") -> JobRecord:
        """保留停止失败的前台进程；只接管输出和清理，绝不重放原命令。"""
        job = JobRecord(id=self._next_id(), kind=kind, label=label, owner=ctx.session_id,
                        started_at=_now_ms(), stdout=TextTail(self._max_chars, output_encoding),
                        stderr=TextTail(self._max_chars, output_encoding), process=proc,
                        settled=asyncio.Event(), cleanup_context={
                            "scope": "foreground-cleanup", "callId": ctx.call_id,
                            "turn": ctx.turn, "step": ctx.step,
                            **({"pid": proc.pid} if getattr(proc, "pid", None) is not None else {}),
                            **({"runId": ctx.services["run_id"]} if ctx.services.get("run_id") else {}),
                        })
        self._cleanup_failed(job, exc)
        self._jobs[job.id] = job

        async def existing() -> Any:
            return proc

        try:
            # 先保留所有权，再按小块解码；不为了恢复输出复制完整采集缓冲。
            for tail, data in ((job.stdout, stdout), (job.stderr, stderr)):
                for offset in range(0, len(data), 8192):
                    tail.append_bytes(bytes(data[offset:offset + 8192]))
        finally:
            job.task = asyncio.create_task(self._pump(job, existing))
        return job

    def assert_ready(self, owner: str) -> None:
        """前台清理尚未确认时阻止新任务；独立后台作业不会触发此门禁。"""
        blocked = [job.id for job in self._jobs.values()
                   if job.owner == owner and job.cleanup_context is not None and not job.is_settled]
        if blocked:
            from ..processes import ProcessCleanupError
            raise ProcessCleanupError(tr('python.job_tools.9a82add11b75'),
                                      session_id=owner, job_ids=blocked)

    def guard(self, definition: ToolDefinition, args: Any, ctx: ToolRunContext) -> str | None:
        if definition.schema.name not in {"job_output", "job_list", "job_kill"}:
            self.assert_ready(ctx.session_id)
        return None

    def record_progress(self, definition, args, value, ctx, result) -> None:
        """仅经运行时确认的输出推进或有意义的实时等待，才能重置重复计数。"""
        if definition.schema.name != 'job_output' or result.is_error or not isinstance(value, dict):
            return
        try:
            job = self._require(str(args['job_id']), ctx.session_id)
        except LookupError:
            return
        evidence = value.get('_progress', {})
        advanced = evidence.get('output_advanced') is True
        live = job.status == 'running' and job.task is not None and not job.task.done()
        result.progress = advanced or (live and evidence.get('wait_enabled') is True
                                      and evidence.get('waited_ms', 0) >= 1000)

    async def spawn(
        self,
        *,
        kind: str,
        label: str,
        owner: str,
        spawn: Callable[[], Awaitable[Any]],
        output_encoding: str = "utf-8",
        initial_stdout: bytes = b"",
        initial_stderr: bytes = b"",
    ) -> JobRecord:
        """启动一个后台作业并立即返回（不等待结束）。

        ``spawn`` 是一个无参异步可调用，返回带 ``stdout`` / ``stderr``
        字节流与 ``wait()`` 的进程对象。POSIX 的 OS 进程应使用
        ``start_new_session=True`` 建立所属进程组；工作流适配器保留无 PID 接口。
        """
        encoding = resolve_output_encoding(output_encoding)
        job = JobRecord(
            id=self._next_id(),
            kind=kind,
            label=label,
            owner=owner,
            started_at=_now_ms(),
            stdout=TextTail(self._max_chars, encoding),
            stderr=TextTail(self._max_chars, encoding),
        )
        job.settled = asyncio.Event()
        # 前台超时交接已经读出的字节。TextTail 的增量解码器也保留
        # 最后未完成的 UTF-8 字符，后台读取剩余字节后不会乱码或重复。
        job.stdout.append_bytes(initial_stdout)
        job.stderr.append_bytes(initial_stderr)
        self._jobs[job.id] = job
        job.task = asyncio.create_task(self._pump(job, spawn))
        return job

    async def _pump(self, job: JobRecord, spawn: Callable[[], Awaitable[Any]]) -> None:
        """持续读取子进程输出，并把进程收敛到终态。"""
        readers: list[asyncio.Task[Any]] = []
        try:
            proc = await spawn()
            job.process = proc
            if job.kind == 'pty-send' and hasattr(proc, 'completion_snapshot'):
                job.completion = proc.completion_snapshot()
            readers = [
                asyncio.create_task(self._pump_stream(job, job.stdout, getattr(proc, "stdout", None))),
                asyncio.create_task(self._pump_stream(job, job.stderr, getattr(proc, "stderr", None))),
            ]
            await self._save(job)
            code = await proc.wait()
            job.exit_code = code
            if job.kind == 'pty-send' and hasattr(proc, 'completion_snapshot'):
                job.completion = proc.completion_snapshot()
            for reader in readers:
                with contextlib.suppress(Exception):
                    await reader
            job.stdout.flush()
            job.stderr.flush()
            # 已被 kill() 标记为 stopping/killed 的作业不再被覆盖。
            # runner 可以在 spawn() 里先写入更具体的 detail（例如 workflow 的
            # "1 agent"），它比通用的退出码更有信息量，所以不覆盖。
            if job.status == "running":
                job.status = "completed"
                job.detail = job.detail or f"exit code: {code}"
            # runner 可能已经把终态和更具体的 detail 写进去了（例如 workflow
            # 的 "N agents" 或失败原因），这时不再覆盖，只补结束时间。
            if job.finished_at is None and job.status != "cleanup-failed":
                job.finished_at = _now_ms()
        except asyncio.CancelledError:
            if job.cleanup_context is not None:
                self._cleanup_failed(job, RuntimeError(tr('python.job_tools.c9b6645d9e37')))
            else:
                job.status = "stopping"
            raise
        except Exception as exc:  # noqa: BLE001 - 作业失败要以状态回报给模型
            job.stdout.flush()
            job.stderr.flush()
            if job.status == "running":
                job.status = "failed"
                job.detail = f"{type(exc).__name__}: {exc}"
                job.finished_at = _now_ms()
        finally:
            for reader in readers:
                if not reader.done():
                    reader.cancel()
            for reader in readers:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await reader
            proc = job.process
            if proc is not None and getattr(proc, "returncode", None) is None and job.cleanup_context is None:
                # 泻任被强制取消时，``proc.wait()`` 一定还没记录退出码；
                # 这时直接关传输层会落下「Close running child process」。
                try:
                    await asyncio.wait_for(self._stop_owned_process(proc), timeout=KILL_REAP_TIMEOUT)
                except Exception as exc:
                    self._cleanup_failed(job, exc)
            if job.status == "stopping" and (proc is None or getattr(proc, "returncode", None) is not None):
                job.status = "killed"
                job.detail = job.detail or tr('python.job_tools.3ddb29cf572e')
                job.finished_at = _now_ms()
            if proc is not None and getattr(proc, "returncode", None) is not None and job.is_settled:
                self._close_transport(proc)
            try:
                await self._save(job)
            finally:
                if job.settled is not None and job.is_settled:
                    job.settled.set()
                job.changed.set()

    @staticmethod
    def _close_transport(proc: Any) -> None:
        """显式关闭子进程的传输层。

        ``Process.wait()`` 只回收进程本身，不会关掉 stdout/stderr 的
        PIPE 传输层；不显式关闭就会在对象回收时抛
        ``ResourceWarning``、并打出 ``unclosed transport`` / ``Close running child
        process`` 的噪声，那等同于一次句柄泄漏。
        """
        transport = getattr(proc, "_transport", None)
        if transport is None:
            return
        with contextlib.suppress(Exception):
            transport.close()

    async def _pump_stream(self, job: JobRecord, tail: TextTail, stream: Any) -> None:
        """把一个字节流不断灌进尾部缓冲，直到 EOF。"""
        if stream is None:
            return
        while True:
            chunk = await stream.read(65536)
            if not chunk:
                return
            previous = tail.total
            tail.append_bytes(chunk)
            if tail.total != previous:
                job.changed.set()

    # -- 读取 ------------------------------------------------------------
    def read(self, job_id: str, owner: str) -> tuple[str, dict[str, Any]]:
        """读取自上次读取以来的增量输出，并推进该作业的游标。"""
        job = self._require(job_id, owner)
        out, cursor, lossy_out = job.stdout.read_from(job.cursors["stdout"])
        err, err_cursor, lossy_err = job.stderr.read_from(job.cursors["stderr"])
        job.cursors["stdout"] = cursor
        job.cursors["stderr"] = err_cursor

        parts: list[str] = []
        for error in job.public().get('encoding_errors', []):
            parts.append(tr('encoding.failure', **{key: error[key] for key in ('channel', 'encoding', 'offset')}) + '\n')
        if out:
            parts.append(out)
        if err:
            if out and not out.endswith("\n"):
                parts.append("\n")
            parts.append("[stderr]\n" + err)
        text = "".join(parts)
        if lossy_out or lossy_err:
            parts.append(tr('python.job_tools.1126c46be77b'))
            text = "".join(parts)
        return text, job.public()

    async def wait(self, job_id: str, owner: str, timeout_ms: int) -> None:
        """等待作业落定。等待超时只是返回，作业继续运行。"""
        job = self._require(job_id, owner)
        assert job.settled is not None
        try:
            await asyncio.wait_for(job.settled.wait(), timeout=max(0.0, timeout_ms / 1000.0))
        except asyncio.TimeoutError:
            # 超时的 wait 不视为错误，只让模型继续轮询。
            return

    async def wait_for_output(self, job_id: str, owner: str, timeout_ms: int) -> None:
        """已有输出立即返回，否则等待变化通知，不使用轮询定时器。"""
        job = self._require(job_id, owner)
        deadline = asyncio.get_running_loop().time() + max(0, timeout_ms) / 1000
        while job.status == 'running' and job.stdout.total <= job.cursors['stdout'] and job.stderr.total <= job.cursors['stderr']:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0 or job.task is None or job.task.done():
                return
            # 检查并清除状态时不让出执行权，避免丢失通知。
            job.changed.clear()
            try:
                async with asyncio.timeout(remaining):
                    await job.changed.wait()
            except TimeoutError:
                return

    def active_owners(self) -> set[str]:
        """提供尚未结算作业的归属，不向公开作业视图添加内部字段。"""
        return {job.owner for job in self._jobs.values() if not job.is_settled}

    def list_all_jobs(self) -> list[dict[str, Any]]:
        """跨 owner 的全量视图，供 Web 可观测面板总览用。

        工具层（job_list）必须按会话隔离，否则一个会话能取消另一个会话的
        作业；但运行时面板要回答"整台机器上现在有哪些后台作业在跑"。
        这里只读不写，且返回的是 :meth:`JobRecord.public` 的拷贝。
        """
        return [job.public() for job in self._jobs.values()]

    def peek(self, job_id: str, owner: str, *, limit: int = 32768) -> dict[str, Any]:
        """只读输出窗口；不推进模型的 job_output 游标，多端读取互不消耗。"""
        job = self._require(job_id, owner)
        result: dict[str, Any] = {"job": job.public(), "truncated": False}
        for name in ("stdout", "stderr"):
            tail = getattr(job, name)
            start = max(0, tail.total - max(1, min(32768, limit)))
            text, end, lossy = tail.read_from(start)
            result[name] = text
            result[name + "_offset"] = end - len(text)
            result[name + "_end"] = end
            result["truncated"] |= lossy or start > 0
        return result

    # -- 控制 ------------------------------------------------------------
    async def kill(self, job_id: str, owner: str, reason: str | None = None) -> tuple[str, dict[str, Any]]:
        """请求取消一个运行中的作业。

        这里不只是发信号：还要真正把子进程回收掉。等 ``proc.wait()`` 返回后
        泵任务会自然读完管道退出，Windows 上才不会在对象回收时打出
        ``Close running child process`` 的传输层告警（那等同于一次句柄泄漏）。
        """
        job = self._require(job_id, owner)
        async with job.control_lock:
            if job.is_settled:
                return "already-finished", job.public()
            job.status = "stopping"
            job.changed.set()
            # 清理一旦被接受，即使 HTTP 或工具观察者已取消也须继续。
            # 在获得权威结果前保持控制锁。
            cleanup = asyncio.create_task(self._reap(job))
            cancelled = False
            while not cleanup.done():
                try:
                    await asyncio.wait({cleanup})
                except asyncio.CancelledError:
                    cancelled = True
            try:
                cleanup.result()
            except asyncio.CancelledError:
                self._cleanup_failed(job, RuntimeError(tr('python.job_tools.27e6c1bbe983')))
                raise
            except Exception as exc:
                self._cleanup_failed(job, exc)
                await self._save(job)
                if cancelled:
                    raise asyncio.CancelledError from exc
                return "cleanup-failed", job.public()
            job.status = "killed"
            job.cleanup_error = None
            job.detail = reason or tr('python.job_tools.3ddb29cf572e')
            job.finished_at = job.finished_at or _now_ms()
            try:
                await self._save(job)
            finally:
                if job.settled is not None:
                    job.settled.set()
            if cancelled:
                raise asyncio.CancelledError
            return "cancellation-requested", job.public()

    @staticmethod
    def _cleanup_failed(job: JobRecord, exc: BaseException) -> None:
        job.status = "cleanup-failed"
        job.cleanup_error = f"{type(exc).__name__}: {exc}" or tr('python.job_tools.69f0b431903a')
        job.detail = tr('python.job_tools.72c79038cc81')
        job.finished_at = None
        job.changed.set()
        if job.settled is not None:
            job.settled.clear()
        logger.warning(tr('log.bbc3ab430b6f'), job.id, job.cleanup_error)

    @staticmethod
    def _terminate(proc: Any) -> None:
        """结束进程：先 terminate，POSIX 上再退回 SIGKILL。"""
        try:
            proc.terminate()
        except ProcessLookupError:
            return
        if os.name != "nt":
            with contextlib.suppress(Exception):
                proc.send_signal(signal.SIGKILL)

    async def _stop_owned_process(self, proc: Any) -> None:
        if getattr(proc, 'stop_owned', None) is not None:
            await proc.stop_owned()
        elif getattr(proc, 'pid', None) is not None:
            from ..processes import stop_process
            await stop_process(proc)
        else:
            # 没有所属异步停止接口的工作流适配器继续沿原生命周期收尾。
            self._terminate(proc)
            await proc.wait()

    async def _ensure_process(self, job: JobRecord, timeout: float = 2.0) -> Any:
        """等待泻任把子进程创建出来。

        ``cancel_all()`` 可能在 ``spawn()`` 还没返回时就到达，此时
        ``job.process`` 还是 ``None``。若不等一下，就会漏掉终止，
        只能强制取消泻任，进而在取消的瞬间留下仍在运行的传输层。
        """
        if job.process is not None or job.task is None or job.task.done():
            return job.process
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.0, timeout)
        while job.process is None and loop.time() < deadline and not job.task.done():
            await asyncio.sleep(0.02)
        return job.process

    async def _reap(self, job: JobRecord) -> None:
        """终止并等待子进程真正退出，避免留下未回收的传输层。"""
        proc = await self._ensure_process(job)
        if proc is not None:
            if job.cleanup_context is not None and getattr(proc, "returncode", None) is not None and not getattr(proc, "cleanup_confirmed", False):
                from ..processes import ProcessCleanupError
                raise ProcessCleanupError(tr('python.job_tools.084a58110c24'),
                                          job_id=job.id)
            await asyncio.wait_for(self._stop_owned_process(proc), timeout=KILL_REAP_TIMEOUT)
        elif job.task is not None and not job.task.done():
            raise TimeoutError(tr('python.job_tools.1d44cebb2022'))
        # 让泵任务把管道读完并自行收敛。
        if job.task is not None and not job.task.done():
            await asyncio.wait_for(asyncio.shield(job.task), timeout=KILL_REAP_TIMEOUT)
        if job.task is not None and job.task.done():
            with contextlib.suppress(asyncio.CancelledError):
                job.task.result()
        if proc is not None and getattr(proc, "returncode", None) is None:
            raise RuntimeError(tr('python.job_tools.567e3ff04ada'))
        if proc is not None:
            self._close_transport(proc)

    async def cancel_all(self) -> None:
        """逐项收掉未结算作业，保留清理失败而不伪报关闭成功。"""
        from ..processes import ProcessCleanupError

        errors: list[BaseException] = []
        for job in list(self._jobs.values()):
            if job.is_settled:
                continue
            try:
                outcome, _ = await self.kill(job.id, job.owner)
                if outcome == 'cleanup-failed':
                    errors.append(ProcessCleanupError(
                        tr('jobs.cleanup.incomplete', job=job.id),
                        job_id=job.id, detail=job.cleanup_error,
                    ))
            except BaseException as exc:
                errors.append(exc)
        raise_cleanup_errors(errors)


def _job_error(job_id: str) -> dict[str, Any]:
    return {"error": True, "message": tr('python.job_tools.035fd111eb73', p0=f'{job_id}')}


def build_job_tools(registry: JobRegistry) -> list[ToolDefinition]:
    """构造后台作业工具（job_output / job_list / job_kill）。"""

    async def job_output(args: Any, ctx: ToolRunContext) -> Any:
        job_id = str(args["job_id"])
        try:
            record = registry._require(job_id, ctx.session_id)
            raw_timeout = int(args.get('timeout_ms', WAIT_DEFAULT_MS))
            timeout_ms = max(0, min(raw_timeout, WAIT_CAP_MS))
            started = time.monotonic()
            if args.get('wait') is True:
                await registry.wait(job_id, ctx.session_id, timeout_ms)
            elif 'wait' not in args:
                await registry.wait_for_output(job_id, ctx.session_id, timeout_ms)
            waited_ms = int((time.monotonic() - started) * 1000)
            # 游标消费是原子的，包括并发 SDK 读取者。
            before = dict(record.cursors)
            text, job = registry.read(job_id, ctx.session_id)
        except LookupError:
            try:
                snapshot = await registry.output_snapshot(job_id, ctx.session_id)
            except LookupError:
                return _job_error(job_id)
            text = snapshot['stdout'] + ('\n[stderr]\n' + snapshot['stderr'] if snapshot['stderr'] else '')
            text += tr('python.job_tools.e98914a689c6')
            if snapshot['truncated']:
                text += tr('python.job_tools.588fecf722cd')
            return {'text': text, 'job': snapshot['job']}
        return {"text": text, "job": job, "error": bool(job.get("encoding_errors")),
                **({"message": text} if job.get("encoding_errors") else {}),
                '_progress': {'waited_ms': waited_ms, 'wait_enabled': args.get('wait') is not False and timeout_ms > 0,
                              'output_advanced': record.cursors != before}}

    def job_output_to_content(args: Any, value: Any) -> list[TextBlock]:
        if value.get("error"):
            return [TextBlock(text=str(value.get("message", tr('python.job_tools.0c783bba3847'))))]
        body = value.get("text") or tr('python.job_tools.39f24075c749')
        separator = "" if body.endswith("\n") else "\n"
        job = value["job"]
        line = f"[{job['id']}] {job['status']}"
        if job.get("detail"):
            line += f" — {job['detail']}"
        return [TextBlock(text=f"{body}{separator}{line}")]

    async def job_list(args: Any, ctx: ToolRunContext) -> Any:
        page = await registry.history_page(ctx.session_id)
        if page.get('history_error'):
            return {'error': True, 'message': page['history_error'], 'jobs': page['jobs']}
        return page['jobs']

    def job_list_to_content(args: Any, value: Any) -> list[TextBlock]:
        warning = ''
        if isinstance(value, dict):
            warning = value.get('message', '') + '\n'
            value = value.get('jobs', [])
        if not value:
            return [TextBlock(text=warning or tr('python.job_tools.cf07d4d6bc5c'))]
        lines = [f"[{job['id']}] [{job['kind']}] {job['status']} — {job['label']}" for job in value]
        return [TextBlock(text=warning + "\n".join(lines))]

    async def job_kill(args: Any, ctx: ToolRunContext) -> Any:
        job_id = str(args["job_id"])
        reason = args.get("reason")
        try:
            outcome, job = await registry.kill(job_id, ctx.session_id, reason)
        except LookupError:
            return _job_error(job_id)
        return {"outcome": outcome, "job": job}

    def job_kill_to_content(args: Any, value: Any) -> list[TextBlock]:
        if value.get("error"):
            return [TextBlock(text=str(value.get("message", tr('python.job_tools.0c783bba3847'))))]
        job = value["job"]
        if value["outcome"] == "already-finished":
            detail = f" — {job['detail']}" if job.get("detail") else ""
            return [TextBlock(text=tr('python.job_tools.d40d53a16bfb', p0=f"{job['id']}", p1=f"{job['status']}", p2=f'{detail}'))]
        if value["outcome"] == "cleanup-failed":
            return [TextBlock(text=tr('python.job_tools.785196020b3e', p0=f"{job['id']}", p1=f"{job.get('cleanupError', '')}"))]
        return [TextBlock(text=tr('python.job_tools.606cc24d5fbb', p0=f"{job['id']}"))]

    return [
        ToolDefinition(
            schema={
                "description_key": 'python.job_tools.c27ba93cafcb',
                "parameter_keys": {
                    ('properties', 'job_id', 'description'): 'python.job_tools.4253929ced4a',
                    ('properties', 'wait', 'description'): 'python.job_tools.35cfa1793e7c',
                    ('properties', 'timeout_ms', 'description'): ('python.job_tools.3eb32fb0613e', {'p0': f'{WAIT_DEFAULT_MS}', 'p1': f'{WAIT_CAP_MS}'}),
                },
                "name": "job_output",
                "description": (
                    tr('python.job_tools.c27ba93cafcb')
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "job_id": {"type": "string", "description": tr('python.job_tools.4253929ced4a')},
                        "wait": {"type": "boolean", "description": tr('python.job_tools.35cfa1793e7c')},
                        "timeout_ms": {"type": "integer", "description": tr('python.job_tools.3eb32fb0613e', p0=f'{WAIT_DEFAULT_MS}', p1=f'{WAIT_CAP_MS}')},
                    },
                    "required": ["job_id"],
                    "additionalProperties": False,
                },
            },
            execute=job_output,
            describe=lambda args, value: {"job": value.get("job")},
            to_content=job_output_to_content,
            timeout=WAIT_CAP_MS / 1000 + 5,
            effect='read',
            concurrency_safe=True,
        ),
        ToolDefinition(
            schema={
                "description_key": 'python.job_tools.f0ac0f550ecb',
                "name": "job_list",
                "description": tr('python.job_tools.f0ac0f550ecb'),
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
            execute=job_list,
            to_content=job_list_to_content,
            effect='read', idempotent=True,
            concurrency_safe=True,
        ),
        ToolDefinition(
            schema={
                "description_key": 'python.job_tools.a8e4ba0055d0',
                "parameter_keys": {
                    ('properties', 'job_id', 'description'): 'python.job_tools.7eb37b30ba5b',
                    ('properties', 'reason', 'description'): 'python.job_tools.6a4456f4bbc8',
                },
                "name": "job_kill",
                "description": tr('python.job_tools.a8e4ba0055d0'),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "job_id": {"type": "string", "description": tr('python.job_tools.7eb37b30ba5b')},
                        "reason": {"type": "string", "description": tr('python.job_tools.6a4456f4bbc8')},
                    },
                    "required": ["job_id"],
                    "additionalProperties": False,
                },
            },
            execute=job_kill,
            to_content=job_kill_to_content,
            concurrency_safe=False,
        ),
    ]


__all__ = [
    "DEFAULT_MAX_RESULT_BYTES",
    "WAIT_CAP_MS",
    "WAIT_DEFAULT_MS",
    "JobRecord",
    "JobRegistry",
    "TextTail",
    "build_job_tools",
]
