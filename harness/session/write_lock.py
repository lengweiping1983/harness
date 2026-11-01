"""会话日志的跨进程提交锁；锁文件保留以维持固定身份。"""
from __future__ import annotations

from contextlib import contextmanager

from ..core.file_lock import lock_file, unlock_file


@contextmanager
def event_write_lock(path):
    """非阻塞获取提交锁，竞争时拒绝写入而不阻塞事件循环。"""
    with path.with_suffix('.lock').open('a+b') as stream:
        lock_file(stream)
        try:
            yield
        finally:
            unlock_file(stream)
