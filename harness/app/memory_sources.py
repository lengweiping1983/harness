"""以已观察文件版本记录来源，并有界核验当前工作树。"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path, PureWindowsPath

from harness.i18n import tr
from ..core.errors import HarnessError

PREFIX = 'harness:file-sources:v1\n'
MAX_SOURCES = 3
MAX_FILE_BYTES = 512 * 1024
MAX_CHECK_BYTES = 2 * 1024 * 1024
MAX_CHECK_FILES = 8


def _source_object(pairs):
    """当前证据对象不接受重复字段，不能用后值替代来源身份。"""
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('memory/invalid-file-sources')
        value[key] = item
    return value


def parse_sources(evidence: str) -> dict | None:
    """统一读取当前文件来源格式，未知证据保持普通文本语义。"""
    if not evidence.startswith(PREFIX):
        return None
    try:
        if len(evidence) > 1000:
            raise ValueError
        value = json.loads(evidence[len(PREFIX):], object_pairs_hook=_source_object)
        if (not isinstance(value, dict) or set(value) != {'note', 'files'}
                or not isinstance(value['note'], str) or not isinstance(value['files'], list)
                or not 1 <= len(value['files']) <= MAX_SOURCES):
            raise ValueError
        for source in value['files']:
            if (not isinstance(source, dict) or set(source) != {'path', 'version'}
                    or not isinstance(source['path'], str) or len(source['path']) > 500
                    or not isinstance(source['version'], str) or not re.fullmatch('[0-9a-f]{16}', source['version'])):
                raise ValueError
    except (ValueError, TypeError):
        raise ValueError('memory/invalid-file-sources') from None
    return value


def workspace_root(cwd: str | Path) -> Path:
    """来源属于当前工作树，不能用共享记忆库所在主工作树替代。"""
    target = Path(cwd).resolve()
    return next((parent for parent in (target, *target.parents) if (parent / '.git').exists()), target)


def capture_sources(cwd: str | Path, paths: list[str], note: str, session, session_id: str) -> str:
    """只复制当前持久会话的文件观察版本，不按当前磁盘补造历史证据。"""
    if (not isinstance(paths, list) or not 1 <= len(paths) <= MAX_SOURCES
            or not isinstance(note, str) or session is None or session.id != session_id):
        raise HarnessError(tr('python.memory.sources_unobserved'), code='memory/source-not-observed')
    root = workspace_root(cwd)
    files = []
    seen = set()
    for name in paths:
        if not isinstance(name, str) or not name or len(name) > 500:
            raise HarnessError(tr('python.memory.invalid'), code='memory/invalid')
        target = (Path(cwd) / name).resolve()
        if not target.is_relative_to(root):
            raise HarnessError(tr('python.memory.unsafe'), code='memory/unsafe-path')
        relative = target.relative_to(root).as_posix()
        prior = session.file_observations.lookup(str(target))
        if prior is None or prior[0] != 'present' or not re.fullmatch('[0-9a-f]{16}', prior[1]):
            raise HarnessError(tr('python.memory.sources_unobserved'), code='memory/source-not-observed')
        if relative not in seen:
            seen.add(relative)
            files.append({'path': relative, 'version': prior[1]})
    value = PREFIX + json.dumps({'note': note, 'files': files}, ensure_ascii=False, separators=(',', ':'))
    if len(value) > 1000:
        raise HarnessError(tr('python.memory.sources_full'), code='memory/source-too-large')
    return value


class SourceChecker:
    """一次召回内共享有限读预算，不持久化结果或刷新文件观察。"""

    def __init__(self, cwd: str | Path) -> None:
        self.root = workspace_root(cwd)
        self.remaining = MAX_CHECK_BYTES
        self.cache: dict[str, tuple[str, str]] = {}

    def _read(self, relative: str) -> tuple[str, str]:
        if relative in self.cache:
            return self.cache[relative]
        name = Path(relative)
        if (not relative or name.is_absolute() or PureWindowsPath(relative).drive
                or '\\' in relative or any(part in {'..', '.'} for part in relative.split('/'))):
            return 'unsafe', ''
        target = self.root / name
        if len(self.cache) >= MAX_CHECK_FILES:
            return 'unchecked', ''
        try:
            if not target.resolve().is_relative_to(self.root):
                return 'unsafe', ''
            before = target.stat()
            if not stat.S_ISREG(before.st_mode):
                return 'unsafe', ''
            if before.st_size > min(MAX_FILE_BYTES, self.remaining):
                result = ('unchecked', '')
            else:
                with target.open('rb') as stream:
                    opened = os.fstat(stream.fileno())
                    if not stat.S_ISREG(opened.st_mode):
                        return 'unsafe', ''
                    limit = min(MAX_FILE_BYTES, self.remaining)
                    raw = stream.read(limit)
                    after = os.fstat(stream.fileno())
                self.remaining = max(0, self.remaining - len(raw))
                # Windows 的 stat/fstat 对 ctime 的解释可能不同；跨接口比较稳定身份，
                # ctime 只在同一打开句柄的前后快照之间比较。
                identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)
                if after.st_size > limit:
                    result = ('unchecked', '')
                elif (len(raw) != after.st_size or identity(before) != identity(opened) or identity(opened) != identity(after)
                      or opened.st_ctime_ns != after.st_ctime_ns or identity(after) != identity(target.stat())):
                    result = ('unavailable', '')
                elif not target.resolve().is_relative_to(self.root):
                    result = ('unsafe', '')
                else:
                    result = ('read', hashlib.sha256(raw).hexdigest()[:16])
        except FileNotFoundError:
            result = ('missing', '')
        except (OSError, ValueError):
            result = ('unavailable', '')
        self.cache[relative] = result
        return result

    def check(self, evidence: str) -> dict | None:
        """指纹相同只说明与记录版本一致，不证明记忆结论正确。"""
        try:
            value = parse_sources(evidence)
        except ValueError:
            return {'status': 'invalid', 'files': []}
        if value is None:
            return None
        files = []
        for source in value['files']:
            status, version = self._read(source['path'])
            if status == 'read':
                status = 'unchanged' if version == source['version'] else 'changed'
            files.append({'path': source['path'], 'status': status})
        priority = ('unsafe', 'missing', 'changed', 'unavailable', 'unchecked', 'unchanged')
        status = next(state for state in priority if any(row['status'] == state for row in files))
        return {'status': status, 'files': files}
