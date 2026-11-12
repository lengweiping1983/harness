"""显式 Git 工作树管理与无提交的归档快照。"""
from __future__ import annotations

from harness.core.text_encoding import diagnostic_text

from harness.i18n import tr

import json
import os
import base64
import subprocess
import uuid
import zipfile
import threading
from functools import wraps
from pathlib import Path
from typing import Any

from ..core.errors import HarnessError
from ..session.store import _atomic_write
from .git_process import run_git as run_git_process


def serialized(fn):
    @wraps(fn)
    def run(self, *args, **kwargs):
        with self._mutex:
            return fn(self, *args, **kwargs)
    return run


class WorktreeService:
    def __init__(self, root: Path) -> None:
        self._mutex = threading.RLock()
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.index = self.root / "index.json"

    def git(self, cwd: Path, *args: str) -> str:
        return self._git_bytes(cwd, *args).decode('utf-8')

    def _git_bytes(self, cwd: Path, *args: str, input: bytes | None = None) -> bytes:
        return self.run_git(cwd, *args, input=input).stdout

    def run_git(self, cwd: Path, *args: str, input: bytes | None = None,
                env: dict[str, str] | None = None, allowed_returncodes: tuple[int, ...] = (0,),
                output_limit: int | None = None) -> subprocess.CompletedProcess[bytes]:
        """宿主 Git 共用隔离环境；特殊退出码和环境覆盖由内部调用方明确声明。"""
        proc = run_git_process(cwd, *args, input=input, env=env, output_limit=output_limit)
        if proc.returncode not in allowed_returncodes:
            raise HarnessError(diagnostic_text(proc.stderr).strip(), code="git/failed")
        return proc

    @serialized
    def list(self) -> list[dict[str, Any]]:
        rows = json.loads(self.index.read_text("utf-8")) if self.index.exists() else []
        invalid = lambda: HarnessError(tr('ui.git.error.invalid'), code='git/storage-invalid')
        if not isinstance(rows, list):
            raise invalid()
        seen: set[str] = set()
        for row in rows:
            if (not isinstance(row, dict)
                    or any(not isinstance(row.get(key), str) or not row[key] for key in ('id', 'repo', 'path', 'base_commit'))
                    or row.get('status') not in ('active', 'archived') or row['id'] in seen):
                raise invalid()
            seen.add(row['id'])
            if row['status'] == 'archived' and any(not isinstance(row.get(key), str) or not row[key] for key in ('archive', 'bundle', 'head', 'staged')):
                raise invalid()
            if row["status"] == "archived" and self._target(row).exists():
                row["status"] = "active"
        return rows

    def _save(self, rows):
        _atomic_write(self.index, json.dumps(rows, ensure_ascii=False))

    @serialized
    def conversation_options(self, cwd: str) -> dict:
        """新对话与添加项目页面共用只读目录探测。"""
        from .conversation_workspace import options
        return options(self, cwd)

    @serialized
    def prepare_conversation(self, cwd: str, selection: dict) -> tuple[str, dict]:
        """预检与实际检出共用同一次仓库写入锁。"""
        from .conversation_workspace import prepare
        return prepare(self, cwd, selection)

    @serialized
    def repository(self, cwd: str) -> dict[str, Any]:
        """读取当前仓库真实状态，使用 NUL 分隔以保留特殊文件名。"""
        target = Path(cwd).resolve()
        try:
            root = self.git(target, "rev-parse", "--show-toplevel").strip()
        except HarnessError as exc:
            if target.is_dir() and exc.code == 'git/failed' and exc.message.startswith('fatal: not a git repository'):
                if any(os.path.lexists(parent / '.git') for parent in (target, *target.parents)):
                    raise HarnessError(tr('ui.git.error.metadataPresent'), code='git/metadata-present') from exc
                raise HarnessError(tr('ui.git.error.notRepository'), code='git/not-repository', cwd=str(target)) from exc
            raise
        # 聊天可以位于仓库子目录，审查版本与冲突检查必须覆盖完整暂存区。
        target = Path(root)
        branch = self.git(target, "branch", "--show-current").strip() or None
        entries = self._git_bytes(target, "status", "--porcelain=v1", "-z", "--untracked-files=normal").decode('utf-8').split("\0")
        files = []
        cursor = 0
        while cursor < len(entries) and entries[cursor]:
            entry = entries[cursor]
            cursor += 1
            item = {"status": entry[:2], "path": entry[3:]}
            if "R" in entry[:2] or "C" in entry[:2]:
                item["original_path"] = entries[cursor]
                cursor += 1
            files.append(item)
        refs = self.git(target, "for-each-ref", "--format=%(refname:short)", "refs/heads", "refs/remotes").splitlines()
        from .git_operations import snapshot
        return {"root": root, "branch": branch, "files": files, "refs": refs, **snapshot(self, target, branch)}

    @serialized
    def initialize(self, cwd: str, expected_cwd: str) -> dict[str, Any]:
        """在已展示的聊天目录初始化 Git，已有仓库直接复用。"""
        target = Path(cwd).resolve()
        if not isinstance(expected_cwd, str) or not expected_cwd or '\0' in expected_cwd:
            raise HarnessError(tr('ui.git.error.invalid'), code='git/invalid')
        if Path(expected_cwd).resolve() != target:
            raise HarnessError(tr('ui.git.error.directoryChanged'), code='git/directory-changed')
        # Git 环境覆盖可能把写入重定向到聊天目录之外，初始化时必须拒绝。
        overrides = ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_COMMON_DIR', 'GIT_INDEX_FILE', 'GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES')
        if any(name in os.environ for name in overrides):
            raise HarnessError(tr('ui.git.error.environmentOverride'), code='git/environment-override')
        try:
            return self.repository(str(target))
        except HarnessError as exc:
            if exc.code != 'git/not-repository':
                raise
        # 损坏的目录、gitfile 和失效符号链接需要修复，不能当成新项目覆盖。
        if any(os.path.lexists(parent / '.git') for parent in (target, *target.parents)):
            raise HarnessError(tr('ui.git.error.metadataPresent'), code='git/metadata-present')
        self.git(target, 'init', '--initial-branch=main')
        return self.repository(str(target))

    @serialized
    def stage(self, cwd: str, paths: list[str], expected_revision: str, *, unstage: bool = False) -> dict:
        """仅暂存或取消暂存选中文件，不改工作区正文。"""
        from .git_operations import stage
        return stage(self, cwd, paths, expected_revision, unstage=unstage)

    @serialized
    def commit(self, cwd: str, message: str, expected_revision: str) -> dict:
        """提交当前暂存内容，不自动暂存、推送或修改 Git 身份配置。"""
        from .git_operations import commit
        return commit(self, cwd, message, expected_revision)

    @serialized
    def staged_patch(self, cwd: str, path: str, expected_revision: str) -> dict:
        """查看实际暂存差异，而不是工作区中尚未暂存的版本。"""
        from .git_operations import staged_patch
        return staged_patch(self, cwd, path, expected_revision)

    def _target(self, row):
        target = Path(row["path"]).resolve()
        if target == self.root or not target.is_relative_to(self.root):
            raise HarnessError(tr('python.worktrees.102555e5721a'), code="git/unsafe-path")
        return target

    @serialized
    def create(self, repo: str, *, ref: str = "HEAD") -> dict[str, Any]:
        if ref.startswith("-"):
            raise HarnessError(tr('python.worktrees.3569ec9a4fc3'), code="git/ref")
        source = Path(repo).resolve()
        source = Path(self.git(source, "rev-parse", "--show-toplevel").strip()).resolve()
        commit = self.git(source, "rev-parse", "--verify", ref + "^{commit}").strip()
        wid = "wt_" + uuid.uuid4().hex[:12]
        target = self.root / wid
        branch = "codex/harness-" + wid
        self.git(source, "worktree", "add", "-b", branch, str(target), commit)
        row = {"id": wid, "repo": str(source), "path": str(target), "branch": branch, "status": "active", "base_commit": commit}
        try:
            self._save(self.list() + [row])
        except Exception:
            # 索引写失败时撤销空工作树，避免产生不可管理的目录与分支。
            self.git(source, "worktree", "remove", str(target))
            self.git(source, "branch", "-D", branch)
            raise
        return row

    @serialized
    def create_team(self, repo: str, members: list[str], *, team: str, owner_session_id: str, run_id: str, ref: str = "HEAD") -> list[dict[str, Any]]:
        """同一提交创建完整成员工作树，失败仅撤回本次未改变的资源。"""
        if not isinstance(members, list) or not 1 <= len(members) <= 8 or any(not isinstance(name, str) or not name.strip() or len(name) > 64 for name in members) or len(set(members)) != len(members):
            raise HarnessError(tr('worktree.team.members_invalid'), code="git/team-members")
        if any(not isinstance(value, str) or not value.strip() or len(value) > 128 for value in (team, owner_session_id, run_id)):
            raise HarnessError(tr('worktree.team.identity_invalid'), code="git/team-identity")
        if not isinstance(ref, str) or ref.startswith("-"):
            raise HarnessError(tr('python.worktrees.3569ec9a4fc3'), code="git/ref")
        source = Path(self.git(Path(repo).resolve(), "rev-parse", "--show-toplevel").strip()).resolve()
        commit = self.git(source, "rev-parse", "--verify", ref + "^{commit}").strip()
        created: list[dict[str, Any]] = []
        try:
            for member in members:
                row = self.create(str(source), ref=commit)
                created.append(row)
                row.update(team=team, team_member=member, owner_session_id=owner_session_id, team_run_id=run_id)
                self._save([row if item['id'] == row['id'] else item for item in self.list()])
            return created
        except Exception as failure:
            retained: list[str] = []
            for row in reversed(created):
                try:
                    target = self._target(row)
                    # 不强制删除：忽略文件、外部提交和用户新增内容也必须保留。
                    dirty = self.git(target, "status", "--porcelain", "--untracked-files=all", "--ignored")
                    head = self.git(target, "rev-parse", "HEAD").strip()
                    branch_head = self.git(source, "rev-parse", "refs/heads/" + row['branch']).strip()
                    if dirty or head != commit or branch_head != commit:
                        retained.append(row['id'])
                        continue
                    self.git(source, "worktree", "remove", str(target))
                    self._save([item for item in self.list() if item['id'] != row['id']])
                    self.git(source, "branch", "-d", row['branch'])
                except Exception:
                    retained.append(row['id'])
                    # 资源标识进入错误信息，便于定位未能撤回的工作树或分支。
                    retained.append(row['branch'])
            if retained:
                raise HarnessError(tr('worktree.team.cleanup_partial', resources=', '.join(retained)), code="git/team-cleanup") from failure
            raise

    @serialized
    def diff(self, wid: str) -> dict[str, Any]:
        row = self.get(wid)
        target = self._target(row)
        return {"status": self.git(target, "status", "--short"), "diff": self.git(target, "diff", "HEAD", "--"), "path": str(target)}

    @serialized
    def review(self, wid: str, *, path: str | None = None) -> dict[str, Any]:
        """按创建提交审查整个工作树；逐文件限制预览，不改变索引与工作区。"""
        from .worktree_review import review
        row = self.get(wid)
        if row['status'] != 'active':
            raise HarnessError(tr('python.worktrees.3b34e0c29769'), code='git/archived')
        return review(self, row, path=path)

    @serialized
    def preview_environment(self, repo: str) -> dict[str, Any]:
        """列出明确配置的本地环境文件，不创建工作树或读取文件正文。"""
        from .worktree_environment import preview_environment
        return preview_environment(self, repo)

    @serialized
    def plan_environment(self, wid: str) -> dict[str, Any]:
        """查看明确源文件在指定工作树中的可复制与保留状态。"""
        from .worktree_environment_copy import plan_environment
        return plan_environment(self, self.get(wid))

    @serialized
    def copy_environment(self, wid: str, *, expected_fingerprint: str, before_publish=None) -> dict[str, Any]:
        """导入复核过的本地环境文件，不覆盖任何已有目标。"""
        from .worktree_environment_copy import copy_environment
        return copy_environment(self, self.get(wid), expected_fingerprint=expected_fingerprint, before_publish=before_publish)

    @serialized
    def freeze_team_source(self, repo: str, *, team: str, owner_session_id: str, run_id: str) -> dict[str, Any]:
        """保存团队共同源工作副本，成员创建仍由宿主独立控制。"""
        from .worktree_source import freeze_source
        return freeze_source(self, repo, team=team, owner_session_id=owner_session_id, run_id=run_id)

    @serialized
    def initialize_team_source(self, wid: str, snapshot: dict) -> dict[str, Any]:
        """将冻结源内容导入一位尚未执行的可信团队成员。"""
        from .worktree_source import initialize_source
        return initialize_source(self, self.get(wid), snapshot)

    @serialized
    def capture_delivery(self, wid: str) -> dict[str, Any]:
        """冻结受管理工作树的交付文件树，不创建用户提交。"""
        from .worktree_delivery import capture
        return capture(self, self.get(wid))

    @serialized
    def create_team_integration(self, sources: list[str]) -> dict[str, Any]:
        """在新工作树中整合已冻结的同轮成员交付，不改变源项目。"""
        from .worktree_integration import create_integration
        return create_integration(self, sources)

    @serialized
    def plan_delivery_merge(self, wid: str, sources: list[str]) -> dict[str, Any]:
        """仅预检同一团队运行中的依赖交付，不修改工作区。"""
        from .worktree_delivery import plan_merge
        return plan_merge(self, self.get(wid), sources)

    @serialized
    def apply_delivery_merge(self, wid: str, sources: list[str], *, expected_target_tree: str, expected_tree: str) -> dict[str, Any]:
        """仅导入仍匹配预检版本的交付，真实暂存区保持不变。"""
        from .worktree_delivery import apply_merge
        return apply_merge(self, self.get(wid), sources,
                           expected_target_tree=expected_target_tree, expected_tree=expected_tree)

    def get(self, wid: str):
        for row in self.list():
            if row["id"] == wid:
                return row
        raise HarnessError(tr('python.worktrees.cc781614f831'), code="git/not-found")

    @serialized
    def archive(self, wid: str) -> dict[str, Any]:
        row = self.get(wid)
        target = self._target(row)
        if row["status"] != "active":
            raise HarnessError(tr('python.worktrees.3b34e0c29769'), code="git/archived")
        if (target / ".gitmodules").exists() or any(p.name == ".git" and p.parent != target for p in target.rglob(".git")):
            raise HarnessError(tr('python.worktrees.07f2ac213a0d'), code="git/nested-repository")
        head = self.git(target, "rev-parse", "HEAD").strip()
        # 未解决冲突无法用普通补丁还原索引，必须在删除现场前拒绝归档。
        if self.git(target, "ls-files", "--unmerged"):
            raise HarnessError(tr('python.worktrees.unmerged'), code="git/unmerged")
        branch = self.git(target, "branch", "--show-current").strip() or None
        staged = self.root / (wid + ".staged.json")
        # 原样保留补丁字节，避免 Windows 换行与非 UTF-8 源文件被文本写入破坏。
        patch = self._git_bytes(target, "diff", "--cached", "--binary", "--full-index", "--no-ext-diff", "--no-textconv", "--no-color", "--src-prefix=a/", "--dst-prefix=b/", "HEAD", "--")
        _atomic_write(staged, json.dumps(base64.b64encode(patch).decode("ascii")))
        bundle = self.root / (wid + ".bundle")
        self.git(target, "bundle", "create", str(bundle), "--all", "HEAD")
        archive = self.root / (wid + ".zip")
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
            for path in target.rglob("*"):
                if path.name == ".git":
                    continue
                if path.is_symlink():
                    raise HarnessError(tr('python.worktrees.1619c2b212aa'), code="git/symlink")
                if path.is_file():
                    output.write(path, path.relative_to(target).as_posix())
        with zipfile.ZipFile(archive) as saved:
            if saved.testzip():
                raise HarnessError(tr('python.worktrees.e184c61e906b'), code="git/archive-corrupt")
        # 先提交已验证快照索引，写失败时保留原工作树。
        row.update(status="archived", head=head, branch=branch, archive=str(archive), bundle=str(bundle), staged=str(staged))
        self._save([row if item["id"] == wid else item for item in self.list()])
        try:
            self.git(Path(row["repo"]), "worktree", "remove", "--force", str(target))
        except Exception:
            row["status"] = "active"
            self._save([row if item["id"] == wid else item for item in self.list()])
            raise
        return row

    @serialized
    def restore(self, wid: str) -> dict[str, Any]:
        row = self.get(wid)
        if row["status"] != "archived":
            raise HarnessError(tr('python.worktrees.a82dcf939621'), code="git/not-archived")
        if not isinstance(row.get('staged'), str) or not row['staged']:
            raise HarnessError(tr('ui.git.error.invalid'), code='git/archive-invalid')
        target = self._target(row)
        repo = Path(row["repo"])
        with zipfile.ZipFile(row["archive"]) as saved:
            for name in saved.namelist():
                if not (target / name).resolve().is_relative_to(target) or ".git" in Path(name).parts:
                    raise HarnessError(tr('python.worktrees.4088c84ca878'), code="git/unsafe-path")
            patch = base64.b64decode(json.loads(Path(row["staged"]).read_text("utf-8")), validate=True)
            self.git(repo, "fetch", str(row["bundle"]), "HEAD")
            self.git(repo, "worktree", "add", "--detach", str(target), row["head"])
            # 补丁只恢复暂存区，工作区文件随后独立恢复；两者可以有不同内容。
            if patch:
                try:
                    self._git_bytes(target, "apply", "--cached", "--binary", input=patch)
                except Exception:
                    self.git(repo, "worktree", "remove", "--force", str(target))
                    raise
            names = set(saved.namelist())
            for path in target.rglob("*"):
                if path.is_file() and path.name != ".git" and path.relative_to(target).as_posix() not in names:
                    path.unlink()
            saved.extractall(target)
        # 分支可能已在其它工作区使用；保留分离 HEAD 并如实报告实际状态。
        if row.get("branch"):
            try:
                occupied = any(
                    line == "branch refs/heads/" + row["branch"]
                    for line in self.git(repo, "worktree", "list", "--porcelain").splitlines()
                )
                unchanged = self.git(repo, "rev-parse", "--verify", "refs/heads/" + row["branch"]).strip() == row["head"]
                if not occupied and unchanged:
                    self.git(target, "symbolic-ref", "HEAD", "refs/heads/" + row["branch"])
            except HarnessError:
                pass
        row["branch"] = self.git(target, "branch", "--show-current").strip() or None
        row["status"] = "active"
        self._save([row if item["id"] == wid else item for item in self.list()])
        return row
