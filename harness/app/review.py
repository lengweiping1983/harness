"""只读工作区审阅，核心不依赖 HTTP 或前端。"""
from __future__ import annotations

from harness.core.text_encoding import utf8_prefix

from harness.i18n import tr

import os
import subprocess
from pathlib import Path
from typing import Any

from ..core.diff import unified_patch, patch_counts
from ..core.errors import HarnessError
from .git_process import run_git


class ReviewService:
    def __init__(self, *, file_limit: int = 200, patch_limit: int = 262144) -> None:
        self.file_limit = file_limit
        self.patch_limit = patch_limit

    def _git(self, cwd: Path, *args: str, output_limit: int | None = None, timeout: float = 15) -> subprocess.CompletedProcess:
        try:
            return run_git(cwd, '-c', 'core.fsmonitor=false', '-c', 'status.relativePaths=false', *args,
                           env={'GIT_OPTIONAL_LOCKS': '0'}, timeout=timeout, output_limit=output_limit)
        except FileNotFoundError as exc:
            raise HarnessError(tr('python.review.4fc399d50804'), code="review/git-unavailable") from exc
        except subprocess.TimeoutExpired as exc:
            raise HarnessError(tr('python.review.55cf73fa5b15'), code="review/timeout") from exc

    def scan(self, cwd: str | Path) -> dict[str, Any]:
        workspace = Path(cwd).resolve()
        if not workspace.is_dir():
            return {"available": False, "files": [], "message": tr('python.review.d643c1c92565')}
        try:
            found = self._git(workspace, "rev-parse", "--show-toplevel")
        except HarnessError as exc:
            return {"available": False, "files": [], "message": str(exc)}
        if found.returncode:
            return {"available": False, "files": [], "message": tr('python.review.2b259a0f7c47')}
        repo = Path(found.stdout.decode('utf-8').strip()).resolve()
        status = self._git(workspace, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignore-submodules=all", "--", ".")
        if status.returncode:
            raise HarnessError(tr('python.review.741bbfed3b08'), code="review/git-error")
        entries = status.stdout.decode('utf-8').split("\0")
        files = []
        index = 0
        while index < len(entries):
            entry = entries[index]
            index += 1
            if not entry:
                continue
            state, git_path = entry[:2], entry[3:]
            old_path = None
            if "R" in state or "C" in state:
                old_path = entries[index] if index < len(entries) else None
                index += 1
            candidate = repo / git_path
            try:
                relative = candidate.relative_to(workspace).as_posix()
            except ValueError:
                continue
            files.append({"path": relative, "repo_path": git_path, "old_path": old_path, "status": state, "untracked": state == "??"})
        files.sort(key=lambda item: item["path"])
        return {"available": True, "root": str(repo), "workspace": str(workspace), "scope": "workspace", "files": files[:self.file_limit], "total": len(files), "truncated": len(files) > self.file_limit}

    def patch(self, cwd: str | Path, path: str) -> dict[str, Any]:
        snapshot = self.scan(cwd)
        if not snapshot["available"]:
            raise HarnessError(snapshot["message"], code="review/unavailable")
        file = next((item for item in snapshot["files"] if item["path"] == path), None)
        if file is None:
            raise HarnessError(tr('python.review.597d7cc4eebd'), code="review/file-changed")
        workspace = Path(snapshot["workspace"])
        repo = Path(snapshot["root"])
        target = repo / file["repo_path"]
        if not target.parent.resolve().is_relative_to(workspace):
            raise HarnessError(tr('python.review.23fdb78c6917'), code="review/outside-workspace")
        head = self._git(workspace, "rev-parse", "--verify", "HEAD")
        binary = False
        truncated = False
        if file["untracked"] or head.returncode:
            if target.is_symlink():
                raw = os.readlink(target).encode("utf-8")
            elif target.is_file():
                with target.open("rb") as handle:
                    raw = handle.read(self.patch_limit + 1)
            else:
                raw = b""
            binary = b"\0" in raw
            truncated = len(raw) > self.patch_limit
            text = utf8_prefix(raw[:self.patch_limit], truncated=True)
            patch = "" if binary else unified_patch("", text, fromfile="/dev/null", tofile="b/" + path)
        else:
            paths = [":(top,literal)" + file["repo_path"]]
            if file["old_path"]:
                paths.append(":(top,literal)" + file["old_path"])
            result = self._git(repo, "diff", "--no-ext-diff", "--no-textconv", "--no-color", "HEAD", "--", *paths, output_limit=self.patch_limit)
            if result.returncode:
                raise HarnessError(tr('python.review.667df25b8e04'), code="review/git-error")
            truncated = len(result.stdout) > self.patch_limit
            patch = utf8_prefix(result.stdout[:self.patch_limit], truncated=True)
            binary = any(line.startswith("Binary files ") or line == "GIT binary patch" for line in patch.split("\n"))
        added, deleted = patch_counts(patch)
        return {**file, "patch": patch, "binary": binary, "truncated": truncated,
                "added": added,
                "deleted": deleted}
