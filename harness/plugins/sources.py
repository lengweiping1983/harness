"""插件源码的有界读取和版本比较保存，不修改安装缓存或运行状态。"""
from __future__ import annotations

from harness.i18n import tr

import hashlib
import os
from pathlib import Path
import stat
import uuid
import tempfile
from typing import Any

from .packages import MANIFESTS, MAX_FILES, PackageConflict, PackageError, _inside

TEXT_LIMIT = 1024 * 1024
SKIP = {'.git', '.hg', '.svn', 'node_modules', '__pycache__', '.venv'}


class PackageSources:
    """声明和源码文件仍归同一包服务；不保存第二份能力目录。"""

    def __init__(self, managed: Path):
        self.managed = managed.resolve()

    def root(self, source: str) -> Path:
        """允许修复损坏清单，但目录必须是已有插件而非任意文件根。"""
        if not isinstance(source, str) or not source.strip() or '\0' in source:
            raise PackageError(tr('plugin.validation.099ab9000a02'))
        root = Path(source).expanduser().resolve()
        if not root.is_dir() or self.managed == root or self.managed.is_relative_to(root):
            raise PackageError(tr('plugin.validation.099ab9000a02'))
        manifest = next((name for name in MANIFESTS if (root / name).exists()), None)
        if manifest is None:
            raise PackageError(tr('plugin.validation.777419984627'))
        _inside(root, manifest)
        return root

    def target(self, root: Path, name: str, *, missing: bool = False) -> Path:
        """编辑路径沿用包内路径规则；二进制和依赖目录不作为源码编辑入口。"""
        relative = Path(name)
        if any(part in SKIP for part in relative.parts):
            raise PackageError(tr('plugin.validation.49ffb4e14ea4'))
        return _inside(root, name, allow_missing=missing)

    def listing(self, source: str) -> dict[str, Any]:
        root = self.root(source)
        files = []
        examined = 0
        for directory, dirs, names in os.walk(root, followlinks=False):
            dirs[:] = sorted(name for name in dirs if name not in SKIP and not (Path(directory) / name).is_symlink())
            examined += len(dirs) + len(names)
            if examined > MAX_FILES:
                raise PackageError(tr('plugin.validation.ba2ad9b416d0'))
            for name in sorted(names):
                path = Path(directory) / name
                if path.is_symlink():
                    continue
                if path.is_file():
                    files.append(path.relative_to(root).as_posix())
        return {'root': str(root), 'files': sorted(files), 'read_only': root.is_relative_to(self.managed), 'executable_supported': os.name != 'nt'}

    def snapshot(self, source: str, name: str) -> dict[str, Any]:
        root = self.root(source)
        target = self.target(root, name)
        with target.open('rb') as stream:
            mode = stat.S_IMODE(os.fstat(stream.fileno()).st_mode)
            data = stream.read(TEXT_LIMIT + 1)
        if len(data) > TEXT_LIMIT or b'\0' in data:
            raise PackageError(tr('plugin.validation.a1f772f103d9'))
        try:
            content = data.decode('utf-8')
        except UnicodeError as exc:
            raise PackageError(tr('plugin.validation.c01a1b68ec6f')) from exc
        revision = hashlib.sha256(data + b'\0mode\0' + str(mode).encode()).hexdigest()
        return {'root': str(root), 'file': target.relative_to(root).as_posix(), 'content': content, 'revision': revision,
                'executable': bool(mode & 0o111), 'read_only': root.is_relative_to(self.managed)}

    def save(self, source: str, name: str, content: str, revision: str, executable: bool) -> dict[str, Any]:
        """保存用户明确编辑的字节，版本冲突保留原文件，权限位不隐式丢失。"""
        root = self.root(source)
        if root.is_relative_to(self.managed):
            raise PackageError(tr('plugin.validation.e92acf758792'))
        target = self.target(root, name, missing=revision == 'missing')
        if executable and os.name == 'nt':
            raise PackageError(tr('plugin.validation.d469c04a7910'))
        previous = None
        mode = 0o600
        if target.exists():
            previous = self.snapshot(str(root), name)
            if previous['revision'] != revision:
                raise PackageConflict(tr('plugin.validation.cded299936e1'))
            mode = stat.S_IMODE(target.stat().st_mode)
        elif revision != 'missing':
            raise PackageConflict(tr('plugin.validation.9ff845887da7'))
        if previous and '\r\n' in previous['content'] and '\n' not in previous['content'].replace('\r\n', ''):
            content = content.replace('\r\n', '\n').replace('\r', '\n').replace('\n', '\r\n')
        try:
            data = content.encode('utf-8')
        except UnicodeError as exc:
            raise PackageError(tr('plugin.validation.d06c2c5485c2')) from exc
        if len(data) > TEXT_LIMIT or b'\0' in data:
            raise PackageError(tr('plugin.validation.f3bff6dd2e79'))
        mode = mode | 0o111 if executable else mode & ~0o111
        target.parent.mkdir(parents=True, exist_ok=True)
        directory = None
        if os.open in os.supports_dir_fd and os.unlink in os.supports_dir_fd:
            directory = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            temporary = target.parent / ('.plugin-source-' + uuid.uuid4().hex)
            try:
                descriptor = os.open(temporary.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                                     0o600, dir_fd=directory)
            except BaseException:
                os.close(directory)
                raise
        else:
            descriptor, filename = tempfile.mkstemp(prefix='.plugin-source-', dir=target.parent)
            temporary = Path(filename)
        identity = os.fstat(descriptor)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(data)
                stream.flush()
                if directory is not None:
                    os.fchmod(stream.fileno(), mode)
                os.fsync(stream.fileno())
            if directory is None:
                temporary.chmod(mode)
            self.target(root, name, missing=revision == 'missing')
            if previous is not None:
                if self.snapshot(str(root), name)['revision'] != revision:
                    raise PackageConflict(tr('plugin.validation.6bafdf0cd746'))
                if directory is not None:
                    os.replace(temporary.name, target.name, src_dir_fd=directory, dst_dir_fd=directory)
                else:
                    os.replace(temporary, target)
            else:
                # 同目录硬链接使新文件完整出现；目标若已被创建则明确冲突，不覆盖。
                try:
                    if directory is not None:
                        os.link(temporary.name, target.name, src_dir_fd=directory, dst_dir_fd=directory,
                                follow_symlinks=False)
                    else:
                        os.link(temporary, target)
                except FileExistsError as exc:
                    raise PackageConflict(tr('plugin.validation.5f7b6f601ab5')) from exc
            return self.snapshot(str(root), name)
        finally:
            if directory is not None:
                try:
                    # 相对原目录句柄清理，父路径被替换后不能删到别人的同名文件。
                    try:
                        os.unlink(temporary.name, dir_fd=directory)
                    except FileNotFoundError:
                        pass
                finally:
                    os.close(directory)
            else:
                try:
                    current = temporary.stat(follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    if (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino):
                        temporary.unlink()

    def remove(self, source: str, name: str, revision: str) -> dict[str, Any]:
        """仅删除明确选中且版本未变化的源码，根清单保留以便继续编辑。"""
        root = self.root(source)
        if root.is_relative_to(self.managed):
            raise PackageError(tr('plugin.validation.e8dba42fc862'))
        if self.target(root, name).relative_to(root).as_posix() in MANIFESTS:
            raise PackageError(tr('plugin.validation.c5b3127cc149'))
        if self.snapshot(str(root), name)['revision'] != revision:
            raise PackageConflict(tr('plugin.validation.44a0093274ed'))
        target = self.target(root, name)
        if os.open in os.supports_dir_fd and os.unlink in os.supports_dir_fd:
            expected = target.parent.stat()
            directory = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                opened = os.fstat(directory)
                if (opened.st_dev, opened.st_ino) != (expected.st_dev, expected.st_ino):
                    raise PackageConflict(tr('plugin.validation.44a0093274ed'))
                with os.fdopen(os.open(target.name, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, 'O_NONBLOCK', 0),
                                       dir_fd=directory), 'rb') as stream:
                    info = os.fstat(stream.fileno())
                    if not stat.S_ISREG(info.st_mode):
                        raise PackageConflict(tr('plugin.validation.44a0093274ed'))
                    data = stream.read(TEXT_LIMIT + 1)
                mode = stat.S_IMODE(info.st_mode)
                current = hashlib.sha256(data + b'\0mode\0' + str(mode).encode()).hexdigest()
                if len(data) > TEXT_LIMIT or current != revision:
                    raise PackageConflict(tr('plugin.validation.44a0093274ed'))
                latest = os.stat(target.name, dir_fd=directory, follow_symlinks=False)
                if (latest.st_dev, latest.st_ino, latest.st_ctime_ns) != (info.st_dev, info.st_ino, info.st_ctime_ns):
                    raise PackageConflict(tr('plugin.validation.44a0093274ed'))
                os.unlink(target.name, dir_fd=directory)
            finally:
                os.close(directory)
        else:
            target.unlink()
        return self.listing(str(root))
