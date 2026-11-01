"""隔离网关的运行租约，防止两个进程同时刷新同一组账号。"""
from contextlib import contextmanager
from pathlib import Path

from ..core.file_lock import lock_file, unlock_file
from .quota import QuotaError


@contextmanager
def runtime_lease(home: Path):
    """只锁定本机登记目录；凭据辅助程序不取得写入租约。"""
    home.mkdir(parents=True, exist_ok=True)
    with (home / 'codex-gateway-runtime.lock').open('a+b') as stream:
        try:
            lock_file(stream)
        except OSError:
            raise QuotaError('ui.codexGateway.runtimeBusy') from None
        try:
            yield
        finally:
            unlock_file(stream)
