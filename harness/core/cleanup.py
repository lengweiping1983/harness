"""共享异步资源回收等待，不重放有副作用的操作。"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from typing import Any

from ..i18n import tr


def raise_cleanup_errors(errors: list[BaseException]) -> None:
    """逐项回收后保留原始异常；单项失败不改变异常类型。"""
    if len(errors) == 1:
        raise errors[0]
    if errors:
        raise BaseExceptionGroup(tr('cleanup.failed'), errors)


async def join_cleanup(cleanup: asyncio.Future[Any]) -> Any:
    """等待已拥有的清理结束，再传递重复取消，避免遗落后台资源。"""
    cancelled_again = False
    while True:
        try:
            if not cleanup.done():
                await asyncio.wait((cleanup,))
            result = cleanup.result()
            break
        except asyncio.CancelledError:
            if cleanup.cancelled():
                raise
            cancelled_again = True
    if cancelled_again:
        raise asyncio.CancelledError
    return result


@contextmanager
def preserve_cleanup_errors(errors: list[BaseException], primary: BaseException | None = None):
    """后续协议收尾失败时仍保留此前清理与主异常，不重放未知写入。"""
    originals = ([primary] if primary is not None else []) + errors
    try:
        yield
    except BaseException as exc:
        if not errors:
            raise
        raise_cleanup_errors([*originals, exc])
    else:
        if errors:
            raise_cleanup_errors(originals)
