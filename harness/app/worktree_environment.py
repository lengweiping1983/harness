"""只读解析工作树本地环境文件选择，不读取所选文件正文。"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from pathlib import Path

from harness.i18n import tr
from ..core.errors import HarnessError

MAX_MANIFEST_BYTES = 65_536
MAX_FILES = 50_000


def _linked(info) -> bool:
    """符号链接和 Windows 重解析点均不跟随。"""
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def _manifest(root: Path) -> bytes | None:
    """限制配置大小并核对打开前后身份，拒绝链接和并发替换。"""
    file = root / '.worktreeinclude'
    try:
        before = file.lstat()
    except FileNotFoundError:
        return None
    invalid = lambda: HarnessError(tr('worktree.environment.manifest_invalid'), code='git/include-invalid')
    if _linked(before) or not stat.S_ISREG(before.st_mode) or before.st_size > MAX_MANIFEST_BYTES:
        raise invalid()
    identity = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
    try:
        flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
        with os.fdopen(os.open(file, flags), 'rb') as stream:
            if identity(os.fstat(stream.fileno())) != identity(before):
                raise invalid()
            content = stream.read(MAX_MANIFEST_BYTES + 1)
            if identity(os.fstat(stream.fileno())) != identity(before) or identity(file.lstat()) != identity(before):
                raise invalid()
        if len(content) > MAX_MANIFEST_BYTES or b'\0' in content:
            raise invalid()
        content.decode('utf-8-sig')
    except (OSError, UnicodeError) as exc:
        raise invalid() from exc
    return content


def _regular_file(root: Path, name: str):
    """逐级检查仓库相对路径，不进入链接、Git 管理目录或外部路径。"""
    relative = Path(name)
    if relative.is_absolute() or relative.drive or any(part in ('.', '..') or part.lower() == '.git' for part in relative.parts):
        return None, 'unsafe'
    cursor = root
    for index, part in enumerate(relative.parts):
        cursor /= part
        try:
            info = cursor.lstat()
        except (FileNotFoundError, NotADirectoryError):
            return None, 'missing'
        if _linked(info):
            return None, 'symlink'
        if index < len(relative.parts) - 1 and not stat.S_ISDIR(info.st_mode):
            return None, 'unsupported'
    if not relative.parts or not stat.S_ISREG(info.st_mode):
        return None, 'unsupported'
    return info, None


def preview_environment(service, repo: str) -> dict:
    """使用 Git 原生规则选择明确列出的忽略文件，生成只读版本。"""
    root = Path(service.git(Path(repo).resolve(), 'rev-parse', '--show-toplevel').strip()).resolve()
    content = _manifest(root)
    files = []
    skipped = []
    if content is not None:
        # 两份原生 Git 列表相交：必须既被项目忽略，又匹配用户选择。
        with tempfile.TemporaryDirectory(prefix='include-', dir=service.root) as temporary:
            patterns = Path(temporary) / 'patterns'
            patterns.write_bytes(content)
            def names(*args):
                return {name for name in service._git_bytes(root, 'ls-files', '--others', '--ignored', '-z', *args).decode('utf-8').split('\0') if name}
            selected = names('--exclude-standard') & names('--exclude-from=' + str(patterns))
        if len(selected) > MAX_FILES:
            raise HarnessError(tr('worktree.environment.too_many', limit=MAX_FILES), code='git/include-limit')
        for name in sorted(selected):
            info, reason = _regular_file(root, name)
            if reason:
                skipped.append({'path': name, 'reason': reason})
            else:
                files.append({'path': name, 'size': info.st_size, 'mtime_ns': info.st_mtime_ns,
                              'mode': stat.S_IMODE(info.st_mode), 'device': info.st_dev, 'inode': info.st_ino})
    if _manifest(root) != content:
        raise HarnessError(tr('ui.git.error.fileChanged'), code='git/file-changed')
    head = service.git(root, 'rev-parse', 'HEAD').strip()
    manifest_hash = hashlib.sha256(content).hexdigest() if content is not None else None
    result = {'repo': str(root), 'base_commit': head, 'configured': content is not None,
              'manifest_hash': manifest_hash, 'files': files, 'skipped': skipped,
              'total_bytes': sum(row['size'] for row in files)}
    result['fingerprint'] = hashlib.sha256(json.dumps(result, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()
    return result
