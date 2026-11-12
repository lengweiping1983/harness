"""受控读取、搜索和带版本前提的批量文件修改。"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import tempfile
import uuid

from ..core.errors import HarnessError
from ..i18n import tr
from ..tools.builtin.fs_tools import DEFAULT_IGNORES
from .policy import PathPolicy, fail, file_version


MAX_SNAPSHOT_FILE_BYTES = 128 * 1024 * 1024
MAX_SNAPSHOT_TOTAL_BYTES = 512 * 1024 * 1024


def eligible_files(policy: PathPolicy, start: str = "."):
    """不跟随链接，不向搜索工具暴露私密文件内容。"""
    root = policy.resolve(start)
    if root.is_file():
        yield root
        return
    for directory, dirs, files in os.walk(root, followlinks=False):
        allowed = []
        for name in dirs:
            if name in DEFAULT_IGNORES or name in {".run", ".workbuddy", ".harness", ".harness-executor"}:
                continue
            try:
                policy.resolve(str(Path(directory) / name))
            except HarnessError:
                continue
            allowed.append(name)
        dirs[:] = sorted(allowed)
        for name in sorted(files):
            try:
                target = policy.resolve(str(Path(directory) / name))
            except HarnessError:
                continue
            if target.is_file():
                yield target


def search(policy: PathPolicy, query: str, path: str, limit: int, max_bytes: int):
    """有界字面量搜索；返回相对路径与命中行。"""
    matches = []
    scanned = 0
    truncated = False
    for target in eligible_files(policy, path):
        scanned += 1
        if scanned > 10000:
            truncated = True
            break
        try:
            if target.stat().st_size > max_bytes:
                continue
            # 文件可能在大小检查后增长，实际读取也必须受同一上限约束。
            with target.open("rb") as stream:
                data = stream.read(max_bytes + 1)
            if len(data) > max_bytes:
                continue
            if b"\x00" in data:
                continue
            text = data.decode("utf-8")
        except (OSError, UnicodeError):
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if query in line:
                matches.append({"path": str(target.relative_to(policy.root)), "line": number, "text": line[:2000]})
                if len(matches) >= limit:
                    return {"matches": matches, "truncated": True, "scanned": scanned}
    return {"matches": matches, "truncated": truncated, "scanned": scanned}


def snapshot(policy: PathPolicy, max_bytes: int, *, snapshot_max_bytes: int | None = None,
             snapshot_max_total_bytes: int = MAX_SNAPSHOT_TOTAL_BYTES):
    """对完整工作树计算内容指纹；快照限额独立于普通文件操作限额。"""
    file_limit = max_bytes if snapshot_max_bytes is None else snapshot_max_bytes
    digest = hashlib.sha256()
    count = 0
    total_bytes = 0
    for target in eligible_files(policy):
        count += 1
        if count > 100000:
            fail("execution/project-too-large", tr('execution.files.fe8bea3d2bce'))
        size = target.stat().st_size
        if size > file_limit:
            fail("execution/file-too-large", tr('execution.files.d39845014a8a'))
        total_bytes += size
        if total_bytes > snapshot_max_total_bytes:
            fail("execution/project-too-large", tr('execution.files.fe8bea3d2bce'))
        digest.update(str(target.relative_to(policy.root)).replace("\\", "/").encode("utf-8"))
        digest.update(b"\x00")
        digest.update(file_version(target).encode("ascii"))
        digest.update(b"\x00")
    return digest.hexdigest()


def apply_changes(policy: PathPolicy, changes: list[dict], home: Path, max_bytes: int):
    """全部预检后修改；保存原文备份，失败时回滚已改文件。"""
    plan = []
    seen = set()
    total_bytes = 0
    for change in changes:
        target = policy.resolve(change["path"])
        key = os.path.normcase(str(target))
        if key in seen:
            fail("execution/duplicate-path", tr('execution.files.7510b8966d3f'))
        seen.add(key)
        current = file_version(target)
        if current != change["expected_version"]:
            fail("execution/file-conflict", tr('execution.files.bcfeee0cc217'))
        if target.exists() and target.stat().st_size > max_bytes:
            fail("execution/file-too-large", tr('execution.files.ca88314c00d0'))
        before = target.read_bytes() if target.exists() else None
        total_bytes += len(before or b"")
        after = change.get("content")
        if after is not None:
            after = after.encode("utf-8") if isinstance(after, str) else after
            if len(after) > max_bytes:
                fail("execution/file-too-large", tr('execution.files.8da9388c14fe'))
            total_bytes += len(after)
        elif before is None:
            fail("execution/file-not-found", tr('execution.files.b7288675e598'))
        if total_bytes > max_bytes * 4:
            fail("execution/patch-too-large", tr('execution.files.e7e5f8efde53'))
        plan.append((target, before, after, current))
    backup_id = "patch_" + uuid.uuid4().hex
    backup = home / "backups" / backup_id
    backup.mkdir(parents=True)
    manifest = [{"path": target.relative_to(policy.root).as_posix(), "before": base64.b64encode(before).decode() if before is not None else None,
                 "expected_version": version} for target, before, _, version in plan]
    (backup / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    applied = []
    try:
        for target, before, after, version in plan:
            policy.resolve(str(target))
            if file_version(target) != version:
                fail("execution/file-conflict", tr('execution.files.88e8b9a71488'))
            if before == after:
                continue
            if after is None:
                target.unlink()
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                _replace(target, after)
            applied.append((target, before, hashlib.sha256(after).hexdigest() if after is not None else "absent"))
    except BaseException as failure:
        conflicts = []
        for target, before, written_version in reversed(applied):
            try:
                policy.resolve(str(target))
                if file_version(target) != written_version:
                    conflicts.append(target.relative_to(policy.root).as_posix())
                    continue
                if before is None:
                    target.unlink(missing_ok=True)
                else:
                    _replace(target, before)
            except (OSError, HarnessError):
                conflicts.append(target.relative_to(policy.root).as_posix())
        if conflicts:
            message = tr('execution.files.rollback_incomplete', backup=backup_id,
                         paths=json.dumps(conflicts, ensure_ascii=False))
            if isinstance(failure, Exception):
                raise HarnessError(message, code='execution/rollback-incomplete') from failure
            # 取消与中断仍传播原异常，附带未完整回滚的诊断。
            failure.add_note(message)
        raise
    # 对外路径统一使用正斜杠，与 git 输出保持一致；Windows 下 Path 的
    # 反斜杠会破坏提交前基线比对（runtime.py）和跨平台 API 契约。
    return {"backup_id": backup_id, "changed_files": [target.relative_to(policy.root).as_posix() for target, _, _ in applied],
            "versions": {target.relative_to(policy.root).as_posix(): file_version(target) for target, _, _, _ in plan}}


def _replace(target: Path, content: bytes):
    """同目录暂存后替换，避免半写文件和跨磁盘移动。"""
    descriptor, name = tempfile.mkstemp(prefix=".he-", dir=target.parent)
    try:
        with os.fdopen(descriptor, "wb") as out:
            out.write(content)
            out.flush()
            os.fsync(out.fileno())
        if target.exists():
            os.chmod(name, target.stat().st_mode)
        os.replace(name, target)
    finally:
        Path(name).unlink(missing_ok=True)
