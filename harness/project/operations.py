"""后台文件或 Git 操作的取消收尾。"""
from __future__ import annotations
import asyncio


async def run_owned(operation, *, cancel_cleanup=None):
    """操作收尾前不报告取消；必要时释放已取得但未交给调用方的资源。"""
    task = asyncio.create_task(asyncio.to_thread(operation))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not task.cancelled() and task.exception() is None and cancel_cleanup is not None:
            cancel_cleanup(task.result())
        raise
