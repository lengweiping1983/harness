"""会话日志的跨进程提交锁；锁文件保留以维持固定身份。"""
from __future__ import annotations

from contextlib import contextmanager
import os


@contextmanager
def event_write_lock(path):
    """非阻塞获取提交锁，竞争时拒绝写入而不阻塞事件循环。"""
    with path.with_suffix('.lock').open('a+b') as stream:
        if os.name == 'nt':
            import msvcrt
            if os.fstat(stream.fileno()).st_size == 0:
                stream.write(b'0')
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            if os.name == 'nt':
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
