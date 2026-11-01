"""跨进程仓库执行锁，显式关闭或进程退出时释放。"""
from __future__ import annotations

import os
from pathlib import Path
from .board import BoardError


class RepositoryLease:
    """锁文件位于 Git 元数据目录，不污染项目改动列表。"""

    def __init__(self, git_dir: Path):
        self.stream = (git_dir / 'harness-development.lock').open('a+b')
        try:
            if os.name == 'nt':
                import msvcrt
                if self.stream.seek(0, 2) == 0:
                    self.stream.write(b'0')
                    self.stream.flush()
                self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as exc:
            self.stream.close()
            raise BoardError('board/project-busy') from exc

    def close(self):
        """关闭文件描述符同时释放锁，重复调用不影响其他执行。"""
        if not self.stream.closed:
            self.stream.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
