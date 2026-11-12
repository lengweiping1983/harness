"""远程工具的真实路径边界；不将文件边界宣称为命令沙箱。"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat

from ..core.errors import HarnessError
from ..i18n import tr


def fail(code: str, message: str):
    """用稳定错误码终止操作。"""
    raise HarnessError(message, code=code)


class PathPolicy:
    """拒绝私密存储、敏感文件及任何重解析路径。"""

    def __init__(self, root: Path, private_home: Path):
        self.root = root.resolve()
        self.private_home = private_home.resolve()

    def resolve(self, raw: str, *, allow_git: bool = False) -> Path:
        if not isinstance(raw, str) or not raw or "\x00" in raw:
            fail("execution/path-invalid", tr('execution.path.invalid'))
        path = Path(raw)
        candidate = Path(os.path.abspath(self.root / path))
        if not candidate.is_relative_to(self.root):
            fail("execution/path-denied", tr('execution.path.outside'))
        if candidate == self.private_home or candidate.is_relative_to(self.private_home):
            fail("execution/private-path", tr('execution.path.private'))
        parts = candidate.relative_to(self.root).parts
        for part in parts:
            lower = part.casefold()
            if os.name == "nt" and (part.endswith((".", " ")) or lower.split(".", 1)[0] in {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}):
                fail("execution/path-invalid", tr('execution.path.windows'))
            if ":" in part or lower in {".harness", ".harness-executor", ".ssh", ".aws", ".azure", "connection-secrets.json", "api-keys.txt"} or lower.startswith(".env") or lower.endswith((".key", ".pem")) or (lower == ".git" and not allow_git):
                fail("execution/private-path", tr('execution.path.sensitive'))
        for parent in (*candidate.parents, candidate):
            if not parent.is_relative_to(self.root):
                continue
            try:
                info = parent.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
                fail("execution/reparse-denied", tr('execution.path.reparse'))
        resolved = candidate.resolve()
        if not resolved.is_relative_to(self.root):
            fail("execution/path-denied", tr('execution.path.resolved'))
        return resolved


def file_version(path: Path) -> str:
    """对完整文件计算版本；缺失文件使用明确标识。"""
    if not path.exists():
        return "absent"
    if not path.is_file():
        fail("execution/not-file", tr('execution.path.not_file'))
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()
