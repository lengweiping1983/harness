"""ESC 启用状态存储（``~/.harness/esc-state.json``）。

持久化纪律镜像 :class:`harness.web.api.settings.SettingsStore`：

* 信封格式 ``{"version": 1, "disabled": {...}}``，只接受当前格式；
* 临时文件 + ``os.replace`` 原子替换，写坏不会毁掉旧状态；
* ``RLock`` 保护并发读写；
* **只记录显式关闭项**（``disabled: {kind/name: true}``）——
  未记录的名字默认启用。这样新装一个技能不需要改这个文件，
  文件也永远不会和目录清单失去同步。
"""

from __future__ import annotations

from harness.i18n import tr
from ..core.fsatomic import atomic_write

import json
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

#: 状态文件名（位于 ``HARNESS_HOME``）
STATE_FILENAME = "esc-state.json"

#: 合法的体系种类
KINDS = ("skill", "agent", "connector")


class EscStateStore:
    """三体系的启用状态。

    :param home: ``HARNESS_HOME``；``None`` 表示纯内存（测试用）。
    """

    def __init__(self, home: str | os.PathLike[str] | None = None) -> None:
        self._path = Path(home).expanduser() / STATE_FILENAME if home is not None else None
        self._lock = threading.RLock()
        #: 显式关闭项：``{"kind/name": True}``。未记录 = 默认启用。
        self._disabled: dict[str, bool] = {}
        self._loaded = False

    # -- 键规范化 --------------------------------------------------------
    @staticmethod
    def _key(kind: str, name: str) -> str:
        if kind not in KINDS:
            raise ValueError(tr('python.state.52005ed42a66', p0=f'{kind!r}', p1=f"{'/'.join(KINDS)}"))
        return f"{kind}/{name}"

    # -- 持久化 ----------------------------------------------------------
    def _load(self) -> None:
        if self._loaded:
            return
        if self._path is None:
            self._loaded = True
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self._loaded = True
            return
        except (OSError, ValueError) as exc:
            raise ValueError(tr('plugins.state.invalid')) from exc
        if not isinstance(raw, dict) or type(raw.get('version')) is not int or raw['version'] != 1:
            raise ValueError(tr('plugins.state.invalid'))
        inner = raw.get("disabled")
        if not isinstance(inner, dict):
            raise ValueError(tr('plugins.state.invalid'))
        for key, value in inner.items():
            if not isinstance(key, str) or key.count('/') != 1 or key.partition('/')[0] not in KINDS or not key.partition('/')[2] or value is not True:
                raise ValueError(tr('plugins.state.invalid'))
        self._disabled = dict(inner)
        self._loaded = True

    def _persist(self, disabled: dict[str, bool]) -> None:
        if self._path is None:
            return
        payload = {"version": 1, "disabled": disabled}
        # 固定 UTF-8/LF 字节，避免共享发布器的文本模式在 Windows 转换换行。
        atomic_write(self._path, json.dumps(payload, ensure_ascii=False, indent=2).encode('utf-8'))

    # -- 查询与修改 ------------------------------------------------------
    def is_enabled(self, kind: str, name: str) -> bool:
        """是否启用（默认 True，除非被显式关闭）。"""
        with self._lock:
            self._load()
            return not self._disabled.get(self._key(kind, name), False)

    def set_enabled(self, kind: str, name: str, enabled: bool) -> None:
        """设置启用状态并原子落盘。

        落盘语义：``disabled`` 映射里 ``True`` = 已停用。存 True 而不是
        False——``dict.get(key, False)`` 的默认值就是 False，存 False 会让
        "显式停用"与"从未记录"无法区分。
        """
        with self._lock:
            self._load()
            key = self._key(kind, name)
            candidate = dict(self._disabled)
            if enabled:
                candidate.pop(key, None)
            else:
                candidate[key] = True
            self._persist(candidate)
            self._disabled = candidate

    def forget(self, kind: str, name: str) -> None:
        """移除一个名字的状态记录（卸载时调用）。"""
        with self._lock:
            self._load()
            candidate = dict(self._disabled)
            candidate.pop(self._key(kind, name), None)
            self._persist(candidate)
            self._disabled = candidate

    def snapshot(self) -> dict[str, bool]:
        """显式关闭项快照（``{kind/name: True}``）。"""
        with self._lock:
            self._load()
            return dict(self._disabled)

    @contextmanager
    def locked(self) -> Iterator[None]:
        """串行化一次"读改写"操作。"""
        with self._lock:
            yield


__all__ = ["EscStateStore", "STATE_FILENAME", "KINDS"]
