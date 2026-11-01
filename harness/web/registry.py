r"""项目目录注册表：路径身份、读写及原子持久化。

当前格式使用 version: 1 封装。新目录正常初始化，已有文件格式错误时
明确报错并保留原文件，不能通过重新播种覆盖用户的项目目录。
"""

from __future__ import annotations

from harness.i18n import tr

from copy import deepcopy

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

#: 注册表文件名（落在 ``HARNESS_HOME`` 下，与 sessions 同级）。
REGISTRY_FILENAME = "workspaces.json"

#: 单次注册表最多容纳的项目数。防止误把 ``C:\\`` 当项目塞进来之后列表没法看。
MAX_WORKSPACES = 64

#: 项目可选的标识色；前端侧栏与标题栏共用这一组。
WORKSPACE_COLORS: tuple[str, ...] = ("brand", "violet", "emerald", "amber", "sky", "rose")


def normalize_id(raw: str | os.PathLike[str]) -> str:
    r"""把任意路径写法归一化成稳定的项目 id。

    Windows 下 ``C:\Work`` 与 ``c:/work`` 是同一个目录，必须落到同一个 id，
    否则用户会看到两条一模一样的项目。
    """
    path = Path(raw).expanduser()
    try:
        resolved = path.resolve()
    except OSError:  # pragma: no cover - 极端权限/断链路径
        resolved = path.absolute()
    text = str(resolved)
    # 去掉尾部分隔符（根目录 ``C:\`` 不能被削成 ``C:``）。
    if len(text) > 1 and text.endswith((os.sep, "/")):
        text = text.rstrip("\\/") or text[0]
    return text.casefold() if os.name == "nt" else text


class WorkspaceRegistry:
    """项目注册表。

    :param home: 持久化目录（``HARNESS_HOME``）；``None`` 表示纯内存（测试用）。
    :param seed: 首次读取时若注册表为空，用它播种一条记录。
    """

    def __init__(self, home: str | os.PathLike[str] | None = None, *, seed: str | None = None) -> None:
        self._path = Path(home).expanduser() / REGISTRY_FILENAME if home is not None else None
        self._lock = threading.RLock()
        self._items: list[dict[str, Any]] = []
        self._seed = seed
        self._loaded = False

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------
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
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(tr('workspace.registry.current_format')) from exc
        invalid = tr('workspace.registry.current_format')
        if not isinstance(raw, dict) or type(raw.get("version")) is not int or raw["version"] != 1 or not isinstance(raw.get("workspaces"), list):
            raise ValueError(invalid)
        items = raw["workspaces"]
        seen: set[str] = set()
        candidate: list[dict[str, Any]] = []
        for entry in items:
            if (not isinstance(entry, dict)
                    or any(not isinstance(entry.get(key), str) or not entry[key].strip() for key in ("id", "path", "name"))
                    or entry.get("color") not in WORKSPACE_COLORS
                    or any(type(entry.get(key)) is not int or entry[key] < 0 for key in ("created_at", "last_used_at"))
                    or entry["id"] in seen
                    or not Path(entry["path"]).is_absolute()
                    or entry["id"] != normalize_id(entry["path"])):
                raise ValueError(invalid)
            seen.add(entry["id"])
            # 目录是否存在是当前文件系统事实，不修复或推断其他持久字段。
            candidate.append({**entry, "exists": Path(entry["path"]).is_dir()})
        if len(candidate) > MAX_WORKSPACES:
            raise ValueError(invalid)
        self._items = candidate
        self._loaded = True

    def _persist(self, items: list[dict[str, Any]]) -> None:
        if self._path is None:
            return
        payload = {"version": 1, "workspaces": items}
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="\n",
            dir=str(self._path.parent),
            prefix=".workspaces-",
            suffix=".tmp",
            delete=False,
        )
        try:
            with handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(handle.name, self._path)
        except BaseException:
            # 临时文件残留不致命，但别让它一直堆在 HOME 里。
            try:
                os.unlink(handle.name)
            except OSError:
                pass
            raise

    def _commit(self, items: list[dict[str, Any]]) -> None:
        """调用方持有注册表锁，只发布已持久化的候选值。"""
        self._persist(items)
        self._items = items

    # ------------------------------------------------------------------
    # 读
    # ------------------------------------------------------------------
    def ensure_seeded(self) -> None:
        """注册表为空时用当前工作区根播种一条，保证首屏就有项目可分组。"""
        with self._lock:
            self._load()
            if self._items or not self._seed:
                return
            try:
                self.register(self._seed)
            except (OSError, ValueError):
                pass

    @staticmethod
    def _view(item: dict[str, Any]) -> dict[str, Any]:
        """返回隔离快照，存在性按当前文件系统计算而不改写持久记录。"""
        return {**deepcopy(item), 'exists': Path(item['path']).is_dir()}

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            self._load()
            return [self._view(item) for item in self._items]

    def ensure_storage(self) -> Path | None:
        """建立注册表文件供独立服务订阅，不添加用户未登记的目录。"""
        with self._lock:
            self._load()
            if self._path is not None and not self._path.exists():
                self._persist(self._items)
            return self._path

    def get(self, workspace_id: str) -> dict[str, Any] | None:
        target = normalize_id(workspace_id)
        with self._lock:
            self._load()
            for item in self._items:
                if item["id"] == target:
                    return self._view(item)
        return None

    def matches(self, path: str | os.PathLike[str]) -> dict[str, Any] | None:
        """按路径反查项目 —— 会话归组走这条。"""
        return self.get(str(path))

    # ------------------------------------------------------------------
    # 写
    # ------------------------------------------------------------------
    def register(
        self,
        raw_path: str,
        *,
        name: str | None = None,
        color: str | None = None,
    ) -> dict[str, Any]:
        """注册一个项目；已存在则只更新可变字段。"""
        path = Path(raw_path).expanduser()
        resolved = path.resolve() if path.exists() else path.absolute()
        if not path.exists():
            raise FileNotFoundError(tr('python.registry.d11988849c67', p0=f'{raw_path}'))
        if not path.is_dir():
            raise NotADirectoryError(tr('python.registry.69207f2370de', p0=f'{raw_path}'))
        identifier = normalize_id(raw_path)
        now = int(time.time() * 1000)
        with self._lock:
            self._load()
            for index, item in enumerate(self._items):
                if item["id"] != identifier:
                    continue
                updated = dict(item)
                updated["exists"] = True
                updated["path"] = str(resolved)
                if name and name.strip():
                    updated["name"] = name.strip()
                if color in WORKSPACE_COLORS:
                    updated["color"] = color
                candidate = list(self._items)
                candidate[index] = updated
                self._commit(candidate)
                return self._view(updated)
            if len(self._items) >= MAX_WORKSPACES:
                raise ValueError(tr('python.registry.445d73d43f57', p0=f'{MAX_WORKSPACES}'))
            label = (name or "").strip() or resolved.name or resolved.drive or str(resolved)
            record = {
                "id": identifier,
                "path": str(resolved),
                "name": label,
                "color": color if color in WORKSPACE_COLORS else WORKSPACE_COLORS[len(self._items) % len(WORKSPACE_COLORS)],
                "created_at": now,
                "last_used_at": now,
                "exists": True,
            }
            self._commit([*self._items, record])
            return self._view(record)

    def update(self, workspace_id: str, **changes: Any) -> dict[str, Any] | None:
        with self._lock:
            self._load()
            identifier = normalize_id(workspace_id)
            for index, item in enumerate(self._items):
                if item["id"] != identifier:
                    continue
                updated = dict(item)
                name = changes.get("name")
                if isinstance(name, str) and name.strip():
                    updated["name"] = name.strip()
                color = changes.get("color")
                if color in WORKSPACE_COLORS:
                    updated["color"] = color
                candidate = list(self._items)
                candidate[index] = updated
                self._commit(candidate)
                return self._view(updated)
        return None

    def touch(self, workspace_id: str) -> None:
        """记一次"最近使用"，供将来按活跃度排序用。"""
        with self._lock:
            self._load()
            identifier = normalize_id(workspace_id)
            for index, item in enumerate(self._items):
                if item["id"] == identifier:
                    updated = dict(item)
                    updated["last_used_at"] = int(time.time() * 1000)
                    candidate = list(self._items)
                    candidate[index] = updated
                    self._commit(candidate)
                    return

    def unregister(self, workspace_id: str) -> bool:
        with self._lock:
            self._load()
            identifier = normalize_id(workspace_id)
            remaining = [item for item in self._items if item["id"] != identifier]
            if len(remaining) == len(self._items):
                return False
            self._commit(remaining)
            return True


__all__ = [
    "WorkspaceRegistry",
    "normalize_id",
    "REGISTRY_FILENAME",
    "WORKSPACE_COLORS",
    "MAX_WORKSPACES",
]
