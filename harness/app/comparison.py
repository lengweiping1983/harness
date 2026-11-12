"""带版本校验的只读 Git 比较；文件摘要分页，补丁按需读取。"""
from __future__ import annotations

from harness.core.text_encoding import utf8_prefix

import hashlib
import json
import os
import re
from pathlib import Path

from harness.core.errors import HarnessError
from harness.i18n import tr
from ..core.diff import unified_patch
from .review import ReviewService


class ComparisonService(ReviewService):
    page_size = 200

    def fail(self, key="invalid", code="review/invalid"):
        raise HarnessError(tr("comparison." + key), code=code)

    def git(self, cwd, *args):
        result = self._git(cwd, *args)
        if result.returncode:
            self.fail("unavailable", "review/unavailable")
        return result.stdout

    def root(self, cwd):
        workspace = Path(cwd).resolve()
        result = self._git(workspace, "rev-parse", "--show-toplevel")
        if result.returncode:
            self.fail("no_git", "review/unavailable")
        return workspace, Path(os.fsdecode(result.stdout).strip()).resolve()

    def resolve(self, root, ref):
        if not isinstance(ref, str) or not ref or ref.startswith("-") or "\0" in ref:
            self.fail()
        result = self._git(root, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}")
        if result.returncode:
            self.fail("ref", "review/invalid-ref")
        return result.stdout.decode("ascii").strip()

    def options(self, cwd, *, search="", offset=0):
        workspace, root = self.root(cwd)
        if offset < 0 or offset > 2**31 - 1 or len(search) > 500:
            self.fail()
        refs = self.git(root, "for-each-ref", "--format=%(refname)", "refs/heads", "refs/remotes").decode('utf-8').splitlines()
        refs = [ref for ref in refs if not ref.endswith("/HEAD")]
        current = self._git(root, "symbolic-ref", "--quiet", "HEAD")
        branch = current.stdout.decode().strip() if current.returncode == 0 else "HEAD"
        upstream = self._git(root, "rev-parse", "--symbolic-full-name", "@{upstream}")
        head = self._git(root, "rev-parse", "--verify", "HEAD")
        commits = []
        if head.returncode == 0:
            args = ["log", "--all", "--format=%H%x00%s", "--max-count=55", "--skip=" + str(offset)]
            if search:
                args += ["--fixed-strings", "--regexp-ignore-case", "--grep=" + search]
            raw = self.git(root, *args).decode('utf-8')
            commits = [dict(zip(("id", "subject"), line.split("\0", 1))) for line in raw.splitlines() if "\0" in line]
            if offset == 0 and not commits and re.fullmatch(r"[0-9a-fA-F]{4,64}", search):
                # 输入提交哈希也可直接定位，不把用户输入当成 Git 参数。
                candidate = self._git(root, "rev-parse", "--verify", "--end-of-options", search + "^{commit}")
                if candidate.returncode == 0:
                    sha = candidate.stdout.decode().strip()
                    if all(c["id"] != sha for c in commits):
                        commits.insert(0, {"id": sha, "subject": self.git(root, "show", "-s", "--format=%s", sha).decode('utf-8').strip()})
        return {"branches": refs, "current": branch, "upstream": upstream.stdout.decode().strip() if upstream.returncode == 0 else None,
                "commits": commits if len(commits) < 55 else commits[:50], "next_offset": offset + 50 if len(commits) >= 55 else None}

    def _endpoints(self, root, scope, base, target):
        empty = self.git(root, "hash-object", "-t", "tree", "--stdin").decode().strip()
        head_result = self._git(root, "rev-parse", "--verify", "HEAD")
        head = head_result.stdout.decode().strip() if head_result.returncode == 0 else empty
        if scope == "branch":
            return self.resolve(root, base), self.resolve(root, target)
        if scope == "commit":
            right = self.resolve(root, target)
            parents = self.git(root, "rev-list", "--parents", "-n", "1", right).decode().split()
            return parents[1] if len(parents) > 1 else empty, right
        if scope == "staged":
            return head, "INDEX"
        if scope == "unstaged":
            return "INDEX", "WORKTREE"
        if scope == "uncommitted":
            return self.resolve(root, base) if base else head, "WORKTREE"
        self.fail()

    @staticmethod
    def _args(left, right):
        if right == "INDEX":
            return ["--cached", left]
        if left == "INDEX":
            return []
        if right == "WORKTREE":
            return [left]
        return [left, right]

    def _content(self, workspace, target, *, preview=False):
        # 不跟随目录链接，防止读取工作区以外的内容；符号链接本身比较链接文本。
        if not target.parent.resolve().is_relative_to(workspace):
            self.fail("outside", "review/outside-workspace")
        if target.is_symlink():
            raw = os.fsencode(os.readlink(target))
            return raw if preview else hashlib.sha256(raw).hexdigest()
        if not target.is_file():
            return b"" if preview else "missing"
        with target.open("rb") as stream:
            if preview:
                return stream.read(self.patch_limit + 1)
            return str(target.stat().st_mode) + ":" + hashlib.file_digest(stream, "sha256").hexdigest()

    def _snapshot(self, workspace, root, left, right):
        args = self._args(left, right)
        # 使用仓库绝对路径语义，避免 Git 的相对路径和路径通配符影响选择。
        relative = workspace.relative_to(root).as_posix()
        pathspec = ":(top,literal)" + (relative if relative != "." else "")
        common = ["--no-ext-diff", "--no-textconv", "--find-renames"]
        raw = self.git(root, "diff", *common, "--name-status", "-z", *args, "--", pathspec)
        parts = os.fsdecode(raw).split("\0")
        by_path = {}
        index = 0
        while index < len(parts) and parts[index]:
            status, path = parts[index], parts[index + 1]
            index += 2
            old = None
            if status.startswith(("R", "C")):
                old, path = path, parts[index]
                index += 1
            relative_path = Path(root / path).relative_to(workspace).as_posix()
            # 冲突文件可能同时返回 U 和 M，目录中只能保留一个冲突条目。
            if relative_path not in by_path or status[0] == "U":
                by_path[relative_path] = {"path": relative_path, "repo_path": path, "old_path": old, "status": status[0], "added": None, "deleted": None}
        files = list(by_path.values())
        stats = self.git(root, "diff", *common, "--numstat", "-z", *args, "--", pathspec)
        chunks = os.fsdecode(stats).split("\0")
        counts = {}
        index = 0
        while index < len(chunks) and chunks[index]:
            added, deleted, path = chunks[index].split("\t", 2)
            index += 1
            if not path:
                path = chunks[index + 1]
                index += 2
            counts[path] = (int(added), int(deleted)) if added != "-" and deleted != "-" else (None, None)
        for file in files:
            if file["status"] != "U":
                file["added"], file["deleted"] = counts.get(file["repo_path"], (None, None))
        if right == "WORKTREE":
            unknown = os.fsdecode(self.git(root, "ls-files", "--others", "--exclude-standard", "-z", "--", pathspec)).split("\0")
            for path in filter(None, unknown):
                raw_content = self._content(workspace, root / path, preview=True)
                complete = len(raw_content) <= self.patch_limit and b"\0" not in raw_content
                added = raw_content.count(b"\n") + int(bool(raw_content) and not raw_content.endswith(b"\n"))
                files.append({"path": (root / path).relative_to(workspace).as_posix(), "repo_path": path,
                              "old_path": None, "status": "A", "untracked": True,
                              "added": added if complete else None, "deleted": 0 if complete else None})
        files.sort(key=lambda file: file["path"])
        state = {"left": left, "right": right, "workspace": str(workspace), "files": files}
        state["raw"] = self.git(root, "diff", *common, "--raw", "--no-abbrev", *args, "--", pathspec).hex()
        if right in {"INDEX", "WORKTREE"}:
            state["index"] = hashlib.sha256(self.git(root, "ls-files", "--stage", "-z")).hexdigest()
        if right == "WORKTREE":
            state["contents"] = [(path, self._content(workspace, root / path)) for path in sorted({f[key] for f in files for key in ("repo_path", "old_path") if f.get(key)})]
        revision = hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()
        return files, revision

    def compare(self, cwd, *, scope="uncommitted", base="", target="", offset=0, revision="", path=None):
        if offset < 0 or offset > 2**31 - 1:
            self.fail()
        workspace, root = self.root(cwd)
        left, right = self._endpoints(root, scope, base, target)
        files, version = self._snapshot(workspace, root, left, right)
        if revision and revision != version:
            self.fail("changed", "review/changed")
        if path is not None and not revision:
            self.fail()
        result = {"available": True, "scope": scope, "left": left, "right": right, "revision": version,
                  "workspace": str(workspace), "total": len(files), "files": files[offset:offset + self.page_size],
                  "next_offset": offset + self.page_size if offset + self.page_size < len(files) else None,
                  "added": sum(f["added"] for f in files) if all(f["added"] is not None for f in files) else None,
                  "deleted": sum(f["deleted"] for f in files) if all(f["deleted"] is not None for f in files) else None}
        if path is not None:
            file = next((file for file in files if file["path"] == path), None)
            if file is None:
                self.fail("changed", "review/changed")
            binary = truncated = False
            if file.get("untracked"):
                content = self._content(workspace, root / file["repo_path"], preview=True)
                binary, truncated = b"\0" in content, len(content) > self.patch_limit
                patch = "" if binary else unified_patch("", utf8_prefix(content[:self.patch_limit], truncated=True), fromfile="/dev/null", tofile="b/" + path)
            else:
                paths = [":(top,literal)" + file["repo_path"]]
                if file["old_path"]:
                    paths.append(":(top,literal)" + file["old_path"])
                output = self._git(root, "diff", "--no-ext-diff", "--no-textconv", "--no-color", "--find-renames", *self._args(left, right), "--", *paths, output_limit=self.patch_limit)
                if output.returncode:
                    self.fail("unavailable", "review/unavailable")
                truncated = len(output.stdout) > self.patch_limit
                patch = utf8_prefix(output.stdout[:self.patch_limit], truncated=True)
                binary = any(line.startswith("Binary files ") or line == "GIT binary patch" for line in patch.split("\n"))
            if len(patch.encode("utf-8")) > self.patch_limit:
                patch = utf8_prefix(patch.encode("utf-8")[:self.patch_limit], truncated=True)
                truncated = True
            result["selection"] = {"path": path, "patch": patch, "binary": binary, "truncated": truncated, "conflict": file["status"] == "U"}
        # 摘要和补丁必须来自同一版本；读取过程中发生写入则要求刷新。
        after = self._endpoints(root, scope, base, target)
        if after != (left, right) or self._snapshot(workspace, root, left, right)[1] != version:
            self.fail("changed", "review/changed")
        return result
