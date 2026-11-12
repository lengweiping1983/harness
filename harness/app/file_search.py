"""有界文件名查找，不读取源码内容，也不执行模型工具。"""
from __future__ import annotations

from harness.i18n import tr
from harness.tools.process_env import execution_env

import os
import stat
import subprocess
import tempfile
import threading
import time
from collections import OrderedDict, deque
from concurrent.futures import Future
from pathlib import Path
from typing import Any

MAX_FILES = 20000
MAX_ENTRIES = 40000
MAX_BYTES = 2 * 1024 * 1024
SCAN_SECONDS = 2
CACHE_SECONDS = 2
SKIP_DIRS = frozenset({'.git', '.hg', '.svn', 'node_modules', '.venv', 'venv', '__pycache__', '.run', '.pytest_cache', '.mypy_cache'})


class FileSearchError(Exception):
    pass


def _name(raw: str) -> bool:
    try:
        raw.encode('utf-8')
    except UnicodeError:
        return False
    return bool(raw) and len(raw) <= 4096 and not any(ord(char) < 32 for char in raw)


def _score(path: str, query: str) -> tuple[int, int] | None:
    target, name = path.casefold(), path.rsplit('/', 1)[-1].casefold()
    if not query:
        return (0, -path.count('/'))
    if name == query:
        return (1000, -len(path))
    if name.startswith(query):
        return (800, -len(path))
    if target.startswith(query):
        return (700, -len(path))
    if query in target:
        return (600, -len(path))
    position, gaps = 0, 0
    for char in query:
        at = target.find(char, position)
        if at < 0:
            return None
        gaps += at - position
        position = at + 1
    return (400 - min(gaps, 300), -len(path))


def _safe_entry(root: Path, relative: str, kind: str) -> dict[str, str] | None:
    if not _name(relative) or relative.startswith('/') or any(part in {'', '.', '..'} for part in relative.split('/')):
        return None
    target = root
    try:
        for part in relative.split('/'):
            target = target / part
            mode = target.lstat().st_mode
            if stat.S_ISLNK(mode) or getattr(target, 'is_junction', lambda: False)():
                return None
        if not target.resolve().is_relative_to(root):
            return None
        if not (stat.S_ISREG(mode) if kind == 'file' else stat.S_ISDIR(mode)):
            return None
    except (OSError, RuntimeError, ValueError):
        return None
    return {'path': relative, 'absolute_path': str(target), 'kind': kind}


class ProjectFileSearch:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cache: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()
        self._pending: dict[str, Future] = {}

    def _index(self, root: Path, refresh: bool) -> dict[str, Any]:
        key = str(root)
        with self._lock:
            cached = self._cache.get(key)
            if not refresh and cached and time.monotonic() - cached[0] < CACHE_SECONDS:
                self._cache.move_to_end(key)
                return cached[1]
            pending = self._pending.get(key)
            owner = pending is None
            if owner:
                pending = Future()
                self._pending[key] = pending
        if not owner:
            return pending.result()
        try:
            index = self._scan(root)
            with self._lock:
                self._cache[key] = (time.monotonic(), index)
                self._cache.move_to_end(key)
                while len(self._cache) > 8:
                    self._cache.popitem(last=False)
            pending.set_result(index)
            return index
        except BaseException as exc:
            pending.set_exception(exc)
            raise
        finally:
            with self._lock:
                self._pending.pop(key, None)

    def _scan(self, root: Path) -> dict[str, Any]:
        if not root.is_dir():
            raise FileSearchError(tr('python.file_search.694fcbd7a6a4'))
        git = any((path / '.git').exists() for path in (root, *root.parents))
        files: set[str] = set()
        directories: set[str] = set()
        truncated = False
        file_bytes = directory_bytes = 0

        def keep(name: str, kind: str) -> bool:
            nonlocal file_bytes, directory_bytes, truncated
            target = files if kind == 'file' else directories
            if name in target:
                return True
            size = len(name.encode('utf-8'))
            used = file_bytes if kind == 'file' else directory_bytes
            if not _name(name) or len(target) >= MAX_FILES or used + size > MAX_BYTES:
                truncated = True
                return False
            target.add(name)
            if kind == 'file':
                file_bytes += size
            else:
                directory_bytes += size
            return True
        if git:
            # 使用临时输出文件，使大型仓库中的 Python 内存占用保持有界。
            # Git 枚举失败时，不能退回到不遵守忽略规则的目录遍历。
            try:
                with tempfile.TemporaryFile() as output:
                    env = execution_env({'GIT_TERMINAL_PROMPT': '0'})
                    result = subprocess.run(['git', '-c', 'core.fsmonitor=false', '-C', str(root),
                        'ls-files', '--cached', '--others', '--exclude-standard', '-z', '--', '.'],
                        stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.DEVNULL, timeout=SCAN_SECONDS, env=env)
                    if result.returncode:
                        raise FileSearchError(tr('python.file_search.4ef3485e3454'))
                    output.seek(0)
                    raw = output.read(MAX_BYTES + 1)
                truncated = len(raw) > MAX_BYTES
                names = raw[:MAX_BYTES].split(b'\0')
                if truncated:
                    names.pop()  # 字节边界可能截断最后一个路径。
                for item in names:
                    if len(files) >= MAX_FILES:
                        truncated = True
                        break
                    try:
                        name = item.decode('utf-8')
                    except UnicodeError:
                        truncated = True
                        continue
                    if name:
                        keep(name, 'file')
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise FileSearchError(tr('python.file_search.d92ff8dd09df')) from exc
        else:
            queue = deque([(root, 0)])
            visited, started = 0, time.monotonic()
            while queue:
                folder, depth = queue.popleft()
                if time.monotonic() - started >= SCAN_SECONDS or visited >= MAX_ENTRIES or len(files) >= MAX_FILES:
                    truncated = True
                    break
                try:
                    with os.scandir(folder) as entries:
                        for entry in entries:
                            visited += 1
                            if visited > MAX_ENTRIES or len(files) >= MAX_FILES or time.monotonic() - started >= SCAN_SECONDS:
                                truncated = True
                                break
                            if not _name(entry.name) or entry.is_symlink():
                                continue
                            path = Path(entry.path)
                            if getattr(path, 'is_junction', lambda: False)():
                                continue
                            if entry.is_dir(follow_symlinks=False):
                                if entry.name in SKIP_DIRS:
                                    continue
                                kept = keep(path.relative_to(root).as_posix(), 'directory')
                                if kept and depth < 32:
                                    queue.append((path, depth + 1))
                                else:
                                    truncated = True
                            elif entry.is_file(follow_symlinks=False):
                                if not keep(path.relative_to(root).as_posix(), 'file'):
                                    break
                except OSError:
                    truncated = True
        for name in files:
            parts = name.split('/')
            for position in range(1, min(len(parts), 33)):
                if not keep('/'.join(parts[:position]), 'directory'):
                    break
            if len(parts) > 33:
                truncated = True
        return {'entries': [(name, 'directory') for name in sorted(directories)] + [(name, 'file') for name in sorted(files)],
                'source': 'git' if git else 'filesystem', 'truncated': truncated}

    def search(self, root: Path, query: str = '', limit: int = 40, *, refresh: bool = False) -> dict[str, Any]:
        if not isinstance(query, str) or len(query) > 256 or '\0' in query or any(ord(char) < 32 for char in query):
            raise ValueError(tr('python.file_search.36149433f92f'))
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError(tr('python.file_search.8ee7948d5a56'))
        try:
            root = root.expanduser().resolve()
        except (OSError, RuntimeError) as exc:
            raise FileSearchError(tr('python.file_search.3ab1ba3e0b0b')) from exc
        if not root.is_dir():
            raise FileSearchError(tr('python.file_search.694fcbd7a6a4'))
        index = self._index(root, refresh)
        q = query.strip().replace('\\', '/').casefold()
        scored = [(score, name, kind) for name, kind in index['entries'] if (score := _score(name, q)) is not None]
        scored.sort(key=lambda row: (-row[0][0], -row[0][1], row[2] != 'directory', row[1].casefold(), row[1]))
        matches = []
        truncated = index['truncated']
        started = time.monotonic()
        for _, name, kind in scored:
            if time.monotonic() - started >= SCAN_SECONDS:
                truncated = True
                break
            entry = _safe_entry(root, name, kind)
            if entry:
                matches.append(entry)
                if len(matches) > limit:
                    break
        return {'root': str(root), 'query': query, 'matches': matches[:limit], 'has_more': len(matches) > limit,
                'truncated': truncated, 'source': index['source']}
