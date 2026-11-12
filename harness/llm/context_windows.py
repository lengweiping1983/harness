"""按端点与模型保存真实上下文窗口；只接受当前格式，损坏原件不改写。"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from ..i18n import tr

_MIN_WINDOW = 1
_MAX_WINDOW = 10_000_000


def _validate(windows: object) -> dict[str, int]:
    """整表校验，不把损坏条目静默丢弃为成功缓存。"""
    if not isinstance(windows, dict) or any(
        not isinstance(key, str) or '|' not in key or not all(key.rsplit('|', 1))
        or type(value) is not int or not _MIN_WINDOW <= value <= _MAX_WINDOW
        for key, value in windows.items()
    ):
        raise ValueError(tr('context_windows.invalid'))
    return dict(windows)


def load_context_windows(path: Path | str) -> dict[str, int]:
    """仅文件不存在返回空表，损坏和读取失败明确传播。"""
    try:
        text = Path(path).read_text(encoding='utf-8')
    except FileNotFoundError:
        return {}
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ValueError(tr('context_windows.invalid')) from exc
    if not isinstance(data, dict) or type(data.get('version')) is not int or data['version'] != 1:
        raise ValueError(tr('context_windows.invalid'))
    return _validate(data.get('windows'))


def save_context_windows(path: Path | str, windows: dict[str, int]) -> None:
    """校验现有文件与输入后原子替换；失败不伪装保存成功。"""
    path = Path(path)
    payload = json.dumps({'version': 1, 'windows': _validate(windows)}, ensure_ascii=False)
    load_context_windows(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix=path.name + '.', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


__all__ = ['load_context_windows', 'save_context_windows']
