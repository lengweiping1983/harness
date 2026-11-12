"""前台进程输出采集：管道读取与 UI 发布分离，保留超时交接字节。"""
from __future__ import annotations

from harness.i18n import tr

import asyncio
import time
from typing import Any

from .processes import ProcessCleanupError, stop_process
from .registry import ToolRunContext
from ..core.text_encoding import TextDecoder

PREVIEW_CHARS = 32768
PUBLISH_INTERVAL = 0.2


class OutputPreview:
    def __init__(self, ctx: ToolRunContext | None, output_encoding: str = "utf-8") -> None:
        self.ctx = ctx
        self.text = {"stdout": "", "stderr": ""}
        self.total = {"stdout": 0, "stderr": 0}
        self.decoders = {name: TextDecoder(output_encoding) for name in self.text}
        self.changed = asyncio.Event()
        self.closed = False
        self.task = asyncio.create_task(self._publish()) if ctx and ctx.services.get("tool_output") else None

    def feed(self, name: str, value: bytes, *, final: bool = False) -> None:
        text = self.decoders[name].decode(value, final=final)
        if self.decoders[name].error:
            self.changed.set()
        if text:
            self.total[name] += len(text)
            self.text[name] = (self.text[name] + text)[-PREVIEW_CHARS:]
            self.changed.set()

    def _snapshot(self) -> tuple:
        return tuple(self.text.values()) + tuple(self.total.values()) + tuple(bool(decoder.error) for decoder in self.decoders.values())

    async def _publish(self) -> None:
        last_at = 0.0
        last = None
        while True:
            await self.changed.wait()
            delay = PUBLISH_INTERVAL - (time.monotonic() - last_at)
            if delay > 0 and not self.closed:
                await asyncio.sleep(delay)
            self.changed.clear()
            snapshot = self._snapshot()
            if snapshot != last and (any(self.total.values()) or any(decoder.error for decoder in self.decoders.values())):
                await self.ctx.report_output(**self.text,
                    stdout_dropped=self.total['stdout'] - len(self.text['stdout']),
                    stderr_dropped=self.total['stderr'] - len(self.text['stderr']),
                    encoding_errors=[{'channel': name, **decoder.error} for name, decoder in self.decoders.items() if decoder.error])
                last, last_at = snapshot, time.monotonic()
            # finish() 可能发生在 report_output 等待慢监听器期间；只有最新
            # 窗口已经发布才结束，不能用旧快照跳过最后的输出和截断标记。
            if self.closed and snapshot == self._snapshot():
                return

    async def finish(self) -> None:
        self.closed = True
        self.changed.set()
        if self.task:
            await self.task


async def cleanup_process(proc: Any, ctx: ToolRunContext | None = None, *, registry: Any = None,
                          kind: str = "command", label: str | None = None,
                          stdout: bytes | bytearray = b"", stderr: bytes | bytearray = b"",
                          close: bool = True, output_encoding: str = "utf-8") -> None:
    """停止所属进程；失败时保留可观察、可重试的所有权和执行门禁。"""
    if label is None:
        label = tr('python.process_output.39dbf0bcf402')
    try:
        await stop_process(proc)
    except BaseException as exc:
        jobs = registry if registry is not None else ctx.services.get("jobs") if ctx else None
        if jobs is None or ctx is None:
            raise
        job = jobs.retain_cleanup(proc, ctx, exc, kind=kind, label=label, stdout=stdout, stderr=stderr, output_encoding=output_encoding)
        raise ProcessCleanupError(tr('python.process_output.b7919a7d3771'),
                                  session_id=ctx.session_id, call_id=ctx.call_id, job_id=job.id,
                                  cleanup_error=job.cleanup_error) from exc
    if close and getattr(proc, 'stop_owned', None) is not None:
        proc.close()


async def collect_process(proc: Any, timeout: float, ctx: ToolRunContext | None = None, *,
                          registry: Any = None, kind: str = "command", label: str | None = None,
                          output_encoding: str = "utf-8") -> tuple[bytes, bytes, int, bool]:
    """并发读取 stdout/stderr，超时保留已消费的字节供后台接手。

    预览仅保留尾部并合并发布；完整结果的编码和截断继续由各工具处理。
    取消或提交失败会清理所属进程树，不能悄悄转入后台。
    """
    captured = {"stdout": bytearray(), "stderr": bytearray()}
    preview = OutputPreview(ctx, output_encoding)

    async def drain(name: str) -> None:
        stream = getattr(proc, name, None)
        if stream is None:
            return
        while True:
            chunk = await stream.read(8192)
            if not chunk:
                preview.feed(name, b'', final=True)
                return
            captured[name].extend(chunk)
            # Windows PowerShell 的 CLIXML 错误交由最终结果解包；
            # 运行时不把未完成的 XML 当作可读错误展示。
            if not (name == 'stderr' and captured[name].startswith(b'#< CLIXML')):
                preview.feed(name, chunk)

    tasks = [asyncio.create_task(drain(name)) for name in captured]
    tasks.append(asyncio.create_task(proc.wait()))
    completion = asyncio.gather(*tasks)
    timed_out = True
    try:
        watching = {completion}
        if preview.task:
            watching.add(preview.task)
        done, _ = await asyncio.wait(watching, timeout=max(0.0, timeout), return_when=asyncio.FIRST_COMPLETED)
        if preview.task in done:
            await preview.task  # 发布/持久化失败必须中止，不能被当作 UI 警告吞掉。
        timed_out = completion not in done
        if timed_out:
            completion.cancel()
            await asyncio.gather(completion, return_exceptions=True)
        else:
            await completion
        await preview.finish()
        return bytes(captured['stdout']), bytes(captured['stderr']), (-1 if timed_out else proc.returncode), timed_out
    except BaseException as original:
        completion.cancel()
        if preview.task and not preview.task.done():
            preview.task.cancel()
        async def reap() -> None:
            # 旧读取者必须先退出，才能把同一 StreamReader 交给清理记录接手。
            await asyncio.gather(completion, *([preview.task] if preview.task else []), return_exceptions=True)
            await cleanup_process(proc, ctx, registry=registry, kind=kind, label=label,
                                  stdout=captured['stdout'], stderr=captured['stderr'], output_encoding=output_encoding)

        # 重复停止请求不能中断归属提交边界。
        # 保留强引用，完成清理后再传播取消。
        cleanup = asyncio.create_task(reap())
        cancelled_again = False
        while True:
            try:
                # wait() 只取消自身观察者，不取消传入的清理任务。
                await asyncio.wait({cleanup})
                cleanup.result()
                break
            except asyncio.CancelledError:
                cancelled_again = True
                if cleanup.done():
                    cleanup.result()  # 内部已取消的清理不能无限循环。
                    break
        if cancelled_again and not isinstance(original, asyncio.CancelledError):
            raise asyncio.CancelledError from original
        raise
    finally:
        completion.cancel()
        if preview.task and not preview.task.done():
            preview.task.cancel()
        await asyncio.gather(completion, *([preview.task] if preview.task else []), return_exceptions=True)
        if not timed_out and getattr(proc, 'stop_owned', None) is not None and proc.cleanup_confirmed:
            proc.close()
