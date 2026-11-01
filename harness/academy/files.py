"""课程文件的有界快照与版本核对。"""
from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath

from .errors import AcademyError

MAX_FILE_BYTES = 256 * 1024
MAX_PROJECT_BYTES = 4 * 1024 * 1024
MAX_FILES = 128
PRIVATE = {'.venv', '.tmp', '__pycache__', '.git'}


def file_path(root: Path, raw: str) -> Path:
    """只接收项目内普通相对路径，拒绝符号链接及保留目录。"""
    if not isinstance(raw, str) or not raw or '\0' in raw or '\\' in raw:
        raise AcademyError('invalid-path')
    parts = PurePosixPath(raw).parts
    if raw.startswith('/') or ':' in raw or '..' in parts or not parts or any(part in PRIVATE for part in parts):
        raise AcademyError('invalid-path')
    current = root
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise AcademyError('invalid-path')
    if not current.resolve().is_relative_to(root.resolve()):
        raise AcademyError('invalid-path')
    return current


def read_files(root: Path) -> dict[str, str]:
    """快照只接收有限个 UTF-8 普通文件，不追随目录链接。"""
    files: dict[str, str] = {}
    total = 0
    def visit(directory: Path, depth=0):
        nonlocal total
        if depth > 16:
            raise AcademyError('project-limit')
        for path in sorted(directory.iterdir()):
            if path.name in PRIVATE:
                continue
            if path.is_symlink():
                raise AcademyError('invalid-path')
            if path.is_dir():
                visit(path, depth + 1)
            elif path.is_file():
                size = path.stat().st_size
                total += size
                if size > MAX_FILE_BYTES or total > MAX_PROJECT_BYTES or len(files) >= MAX_FILES:
                    raise AcademyError('project-limit')
                try:
                    files[path.relative_to(root).as_posix()] = path.read_text(encoding='utf-8')
                except UnicodeError as exc:
                    raise AcademyError('invalid-file') from exc
            else:
                raise AcademyError('invalid-file')
    visit(root)
    return files


def version(content: str | None) -> str:
    return 'absent' if content is None else hashlib.sha256(content.encode('utf-8')).hexdigest()


def fingerprint(files: dict[str, str]) -> str:
    digest = hashlib.sha256()
    for path, content in sorted(files.items()):
        digest.update(path.encode('utf-8') + b'\0' + content.encode('utf-8') + b'\0')
    return digest.hexdigest()
