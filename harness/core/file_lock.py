"""文件锁共用原生适配，Windows允许直接锁定空文件的首字节区域。"""
from __future__ import annotations

import os


def lock_file(stream, *, blocking: bool = False) -> None:
    """先取得锁再访问内容；不能在锁外向空文件写入占位字节。"""
    if os.name == 'nt':
        import msvcrt
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))


def unlock_file(stream) -> None:
    """释放当前句柄的锁，保留稳定的锁文件身份。"""
    if os.name == 'nt':
        import msvcrt
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
