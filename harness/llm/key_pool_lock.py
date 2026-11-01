"""Key 池状态的跨进程事务锁，锁文件不能在释放时删除。"""
from contextlib import contextmanager
from pathlib import Path

from ..core.file_lock import lock_file, unlock_file


@contextmanager
def state_lock(path: Path):
    """锁定稳定的旁路文件，避免原子替换状态文件后锁住旧inode。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_name(path.name + '.lock').open('a+b') as stream:
        lock_file(stream, blocking=True)
        try:
            yield
        finally:
            unlock_file(stream)
