"""Hooks 长上下文的会话隔离文件与首尾预览，复用统一词元估算。"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import uuid

from ..llm.context_accounting import text_tokens
from ..i18n import tr

DEFAULT_CONTEXT_LIMIT = 2500
CAPTURE_LIMIT = 4 * 1024 * 1024


def context_preview(text: str, home: Path, session_id: str, limit: int = DEFAULT_CONTEXT_LIMIT) -> str:
    """超过近似词元阈值时完整保存；失败只返回明确的截断预览。"""
    if limit == 0 or text_tokens(text) <= limit:
        return text
    directory = Path(home) / 'hook_outputs' / hashlib.sha256(session_id.encode()).hexdigest()[:24]
    path = directory / (uuid.uuid4().hex + '.txt')
    temporary = path.with_suffix('.tmp')
    try:
        if directory.parent.is_symlink() or directory.is_symlink():
            raise OSError(tr('hook.feedback.d299d4866c5c'))
        directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w', encoding='utf-8', newline='') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        notice = tr('hook.output.preview', path=path)
    except (OSError, UnicodeError):
        notice = tr('hook.output.unsaved')
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            # 本次临时文件不可清理时仍保留明确的保存失败预览，不触碰其他会话文件。
            pass
    # 为文件提示保留空间；极小阈值也必须保留明确的截断／文件证据。
    budget = max(0, limit - text_tokens(notice))
    low, high = 0, min(len(text) // 2, budget * 2 + 8)
    while low < high:
        middle = (low + high + 1) // 2
        if text_tokens(text[:middle] + text[-middle:]) <= budget:
            low = middle
        else:
            high = middle - 1
    return text[:low] + notice + (text[-low:] if low else '')
