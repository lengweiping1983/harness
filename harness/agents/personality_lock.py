"""人格目录的跨进程写锁；锁文件固定保留，避免删除后出现两个锁身份。"""
from __future__ import annotations

from ..i18n import tr

from contextlib import contextmanager
import errno
import time

from ..core.errors import HarnessError
from ..core.file_lock import lock_file, unlock_file


@contextmanager
def personality_write_lock(path, timeout=5.0):
    """有界等待操作系统文件锁，进程退出时由系统释放。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix('.lock').open('a+b') as stream:
        deadline = time.monotonic() + timeout
        while True:
            try:
                lock_file(stream)
                break
            except OSError as exc:
                if exc.errno not in {errno.EAGAIN, errno.EACCES, errno.EDEADLK}:
                    raise
                if time.monotonic() >= deadline:
                    raise HarnessError(tr('runtime.message.265794ea75f2'), code='personality/busy') from exc
                time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        try:
            yield
        finally:
            unlock_file(stream)
