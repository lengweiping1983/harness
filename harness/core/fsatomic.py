"""会话与附件共用的同目录原子发布；不重放文件内容写入。"""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import time


def atomic_write(path: Path, content: str | bytes) -> None:
    """独占创建临时文件，刷新后发布；仅重试 Windows 共享冲突的替换。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(dir=str(path.parent), prefix='.tmp-', suffix='.tmp')
    temporary = Path(name)
    try:
        options = {'encoding': 'utf-8'} if isinstance(content, str) else {}
        with os.fdopen(descriptor, 'w' if isinstance(content, str) else 'wb', **options) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        for attempt in range(4):
            try:
                os.replace(temporary, path)
                break
            except PermissionError as exc:
                if os.name != 'nt' or getattr(exc, 'winerror', None) not in {5, 32} or attempt == 3:
                    raise
                time.sleep(.02 * (attempt + 1))
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
