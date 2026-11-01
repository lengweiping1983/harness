"""跨进程仓库执行锁，显式关闭或进程退出时释放。"""
from __future__ import annotations

from pathlib import Path
from ..core.file_lock import lock_file
from .board import BoardError


class RepositoryLease:
    """锁文件位于 Git 元数据目录，不污染项目改动列表。"""

    def __init__(self, git_dir: Path):
        self.stream = (git_dir / 'harness-development.lock').open('a+b')
        try:
            lock_file(self.stream)
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
