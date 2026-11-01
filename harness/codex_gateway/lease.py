"""隔离网关的运行租约，防止两个进程同时刷新同一组账号。"""
from contextlib import contextmanager
import os
from pathlib import Path

from .quota import QuotaError


@contextmanager
def runtime_lease(home: Path):
    """只锁定本机登记目录；凭据辅助程序不取得写入租约。"""
    home.mkdir(parents=True, exist_ok=True)
    with (home / 'codex-gateway-runtime.lock').open('a+b') as stream:
        try:
            if os.name == 'nt':
                import msvcrt
                if stream.seek(0, 2) == 0:
                    stream.write(b'0')
                    stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise QuotaError('ui.codexGateway.runtimeBusy') from None
        try:
            yield
        finally:
            if os.name == 'nt':
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
