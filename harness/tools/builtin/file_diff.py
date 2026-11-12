"""从文件工具实际使用的文本中捕获有界的界面证据。"""
from __future__ import annotations

from harness.i18n import tr

import difflib
from pathlib import Path
from typing import Any
from .fs_text import text_lines

LIMIT = 262144


def before_write(path: Path) -> str | None:
    if not path.exists():
        return ''
    try:
        with path.open('rb') as stream:
            raw = stream.read(LIMIT + 1)
        if len(raw) > LIMIT or b'\0' in raw:
            return None
        return raw.decode('utf-8')
    except (OSError, UnicodeError):
        # 审阅不能改变原本已获准的写入是否执行。
        return None


def file_diff(before: str | None, after: str, path: Path, *, changed: bool, before_exists: bool = True) -> dict[str, Any]:
    if not changed:
        return {'diff': '', 'changed': False}
    if before is None or max(len(before), len(after)) > LIMIT or '\0' in before + after:
        return {'diff': None, 'changed': True, 'diff_unavailable': tr('python.file_diff.e07c6daa87d8')}
    old, new = text_lines(before), text_lines(after)
    patch: list[str] = []
    size = 0
    truncated = False
    for line in difflib.unified_diff(old, new, fromfile=f'a/{path}', tofile=f'b/{path}', lineterm='', n=3):
        size += len(line) + 1
        if size > LIMIT:
            truncated = True
            break
        patch.append(line)
    return {'diff': '\n'.join(patch), 'changed': True, 'diff_truncated': truncated,
            '_file_change': {'before': before, 'after': after, 'before_exists': before_exists}}
