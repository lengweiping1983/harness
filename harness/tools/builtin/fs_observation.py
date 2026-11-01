"""文件系统观察策略。

策略开关由每组文件工具持有，默认启用，不修改其他宿主实例。
给写操作加一道**接地性护栏**：

* ``read`` 记录一次权威观察——文件"存在 + 版本号"或"不存在"；
* ``edit`` 必须建立在这个观察之上，否则报 ``FS_NOT_OBSERVED``；
* ``write`` 对"已观察存在"的文件做版本 CAS，避免覆盖观察之后发生的改动；
* ``write`` 对"未观察 / 已观察不存在"的文件走 create-if-absent。

观察按会话隔离，Agent 将工具生成的版本证据提交到会话日志；
恢复时重放这些证据，不将当前磁盘内容冒充历史观察。
无 Session 的 SDK 调用按工具组保留内存记录，成功写回后刷新版本。
"""

from __future__ import annotations

from harness.i18n import tr

import hashlib
import os
import threading
import weakref
from pathlib import Path

from ...core.errors import ToolExecutionError

_LOCK = threading.Lock()
_CACHES: weakref.WeakSet = weakref.WeakSet()


class ObservationCache:
    """一组文件工具独有的 SDK 观察，不与其他工具组或持久会话共享。"""

    def __init__(self):
        self.records: dict[str, dict[str, tuple[str, str]]] = {}
        with _LOCK:
            _CACHES.add(self)


_DEFAULT_CACHE = ObservationCache()


def clear_all() -> None:
    """显式清空 SDK 内存观察，不修改持久会话日志。"""
    with _LOCK:
        for cache in tuple(_CACHES):
            cache.records.clear()


def forget_session(session_id: str) -> None:
    """显式丢弃各工具组中同名 SDK 会话的观察，不修改持久会话。"""
    with _LOCK:
        for cache in tuple(_CACHES):
            cache.records.pop(session_id, None)


def forget_target(path: Path) -> None:
    """用户还原文件后，使所有会话对此文件的观察失效。"""
    key = target_key(path)
    with _LOCK:
        for cache in tuple(_CACHES):
            for observations in cache.records.values():
                observations.pop(key, None)
    from ...session.file_observations import invalidate_target
    invalidate_target(key)


def target_key(path: Path) -> str:
    """把路径归一成稳定比较键。

    先 ``resolve()`` 展开 8.3 短路径（``C:\\Users\\LENGWE~1\\...``）与符号链接，
    否则同一个文件会因为写法不同被当成两个目标；Windows 上再折叠大小写。
    """
    try:
        text = str(Path(path).resolve())
    except (OSError, ValueError):
        text = str(path)
    if os.name == "nt":
        return text.replace("/", "\\").rstrip("\\").casefold()
    return text.rstrip("/") or "/"


def file_version(path: Path) -> str:
    """计算文件版本号（内容 SHA-256 前 16 位）。"""
    try:
        with path.open('rb') as source:
            digest = hashlib.file_digest(source, 'sha256')
    except (OSError, ValueError):
        return ""
    return digest.hexdigest()[:16]


def lookup(session_id: str, path: Path, *, session=None, enabled: bool = True,
           cache: ObservationCache = _DEFAULT_CACHE):
    """返回该会话对目标的观察记录，未观察过时为 ``None``。"""
    if not enabled or not session_id:
        return None
    if session is not None:
        # 会话重放是权威依据，不使用进程全局缓存作依据。
        return session.file_observations.records.get(target_key(path))
    with _LOCK:
        return cache.records.get(session_id, {}).get(target_key(path))


def observe(session_id: str, path: Path, kind: str, version: str = "", *, session=None, enabled: bool = True,
            cache: ObservationCache = _DEFAULT_CACHE):
    """记录或撤销权威观察。策略关闭时是空操作。"""
    if not enabled or not session_id:
        return
    proof = {'path': target_key(path), 'kind': kind, 'version': version}
    if session is not None:
        session.file_observations.record(proof)
    else:
        with _LOCK:
            records = cache.records.setdefault(session_id, {})
            if kind == 'invalidated':
                records.pop(proof['path'], None)
            else:
                records[proof['path']] = (kind, version)
    return proof


def observe_existing(session_id: str, path: Path, *, session=None, enabled: bool = True,
                     cache: ObservationCache = _DEFAULT_CACHE):
    """按磁盘真实状态记录观察（存在记 present，不存在记 absent）。"""
    if not enabled or not session_id:
        return
    try:
        present = path.is_file()
    except OSError:
        present = False
    return observe(session_id, path, "present" if present else "absent", file_version(path) if present else "",
                   session=session, enabled=enabled, cache=cache)


def check_fresh(path: Path, expected: str) -> None:
    """CAS：文件当前版本必须仍等于观察到的版本。"""
    if not expected:
        return
    current = file_version(path)
    if current != expected:
        raise ToolExecutionError(
            tr('python.fs_observation.0236ad311efb', p0=f'{path}', p1=f'{expected}', p2=f'{current}'),
            code="FS_STALE",
            path=str(path),
        )


def require_observed(session_id: str, path: Path, *, session=None, enabled: bool = True,
                     cache: ObservationCache = _DEFAULT_CACHE) -> str:
    """edit 的前置检查：必须先观察过该文件，返回观察到的版本号。"""
    if not enabled or not session_id:
        return ""
    prior = lookup(session_id, path, session=session, enabled=enabled, cache=cache)
    if prior is None:
        raise ToolExecutionError(
            tr('python.fs_observation.ec89f2803c61', p0=f'{path}'),
            code="FS_NOT_OBSERVED",
            path=str(path),
        )
    kind, version = prior
    if kind == "absent":
        raise ToolExecutionError(
            tr('python.fs_observation.e636f9bc334f', p0=f'{path}'),
            code="FS_NOT_FOUND",
            path=str(path),
        )
    check_fresh(path, version)
    return version


def write_exists_error(path: Path) -> ToolExecutionError:
    """预检查和独占创建竞态使用相同的可执行恢复提示。"""
    return ToolExecutionError(
        tr('python.fs_observation.121b5f3d0af3', p0=f'{path}'),
        code="FS_EXISTS",
        path=str(path),
        changed=False,
        recovery={'tool': 'read', 'arguments': {'path': str(path)}},
    )


def guard_write(session_id: str, path: Path, *, session=None, enabled: bool = True,
                cache: ObservationCache = _DEFAULT_CACHE) -> None:
    """write 的前置检查：按观察状态选择 CAS 覆盖或 create-if-absent。"""
    if not enabled or not session_id:
        return
    prior = lookup(session_id, path, session=session, enabled=enabled, cache=cache)
    exists_now = path.exists()
    if prior is not None and prior[0] == "present":
        if not exists_now:
            raise ToolExecutionError(
                tr('python.fs_observation.ed2206026374', p0=f'{path}'),
                code="FS_STALE",
                path=str(path),
            )
        check_fresh(path, prior[1])
        return
    if exists_now:
        raise write_exists_error(path)


__all__ = [
    "ObservationCache",
    "clear_all",
    "check_fresh",
    "file_version",
    "forget_session",
    "forget_target",
    "guard_write",
    "lookup",
    "observe",
    "observe_existing",
    "require_observed",
    "target_key",
    "write_exists_error",
]
