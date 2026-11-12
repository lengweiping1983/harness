"""可纳入 Git 的任务板，使用内容版本和原子替换防止覆盖更新。"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
from pathlib import Path, PureWindowsPath
from typing import Any


from .contracts import STATES, TRANSITIONS, POLICIES, FIELDS
_LOCK = threading.RLock()
MAX_BOARD_BYTES = 2_000_000


class BoardError(ValueError):
    """任务合同、版本或状态迁移不满足约束。"""


def _unique_object_fields(pairs):
    """重复 JSON 字段没有唯一含义，不能选取后值后再覆盖原文件。"""
    result = dict(pairs)
    if len(result) != len(pairs):
        raise BoardError('board/invalid-schema')
    return result


class TaskBoard:
    """每个工作区固定一个事实文件，不触发模型或 Git 写操作。"""

    def __init__(self, root: Path, *, verify_artifacts=None):
        self.root = root.resolve()
        self.verify_artifacts = verify_artifacts
        self.path = self.root / 'docs' / 'harness-task-board.json'

    def _check_path(self) -> None:
        """拒绝通过符号链接写到工作区之外或替换已有链接。"""
        if self.path.is_symlink() or not self.path.resolve().is_relative_to(self.root):
            raise BoardError('board/unsafe-path')

    def read(self) -> dict[str, Any]:
        """返回文件内容与版本；缺失时只读返回空板，不创建文件。"""
        self._check_path()
        try:
            with self.path.open('rb') as stream:
                raw = stream.read(MAX_BOARD_BYTES + 1)
        except FileNotFoundError:
            raw = None
        if raw is not None and len(raw) > MAX_BOARD_BYTES:
            raise BoardError('board/too-large')
        try:
            board = json.loads(raw, object_pairs_hook=_unique_object_fields) if raw is not None else {'schema': 2, 'tasks': []}
            self._validate(board)
        except (ValueError, TypeError, KeyError) as exc:
            raise BoardError('board/invalid-file') from exc
        return {**board, 'revision': hashlib.sha256(raw or b'').hexdigest(),
                'path': 'docs/harness-task-board.json', 'file_exists': raw is not None}

    @staticmethod
    def _validate(board: dict[str, Any], *, completing: str | None = None) -> None:
        """校验磁盘内容，拒绝重复编号、悬空依赖和依赖环。"""
        if (not isinstance(board, dict) or set(board) != {'schema', 'tasks'}
                or type(board.get('schema')) is not int or board['schema'] != 2
                or not isinstance(board.get('tasks'), list)):
            raise BoardError('board/invalid-schema')
        tasks = board['tasks']
        if len(tasks) > 1000:
            raise BoardError('board/too-many-tasks')
        ids: set[str] = set()
        for task in tasks:
            if not isinstance(task, dict) or set(task) - set(FIELDS):
                raise BoardError('board/invalid-task')
            ident = task.get('id', '')
            if not isinstance(ident, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', ident) or ident in ids:
                raise BoardError('board/invalid-id')
            ids.add(ident)
            for key in ('title',):
                if not isinstance(task.get(key), str) or not task[key].strip() or len(task[key]) > 10000:
                    raise BoardError('board/missing-contract')
            criteria = task.get('acceptance')
            if (not isinstance(criteria, list) or not 1 <= len(criteria) <= 32
                    or any(not isinstance(value, str) or not value.strip() or len(value) > 200 for value in criteria)
                    or len(set(criteria)) != len(criteria)):
                raise BoardError('board/missing-contract')
            if task.get('verification_policy') not in POLICIES:
                raise BoardError('board/invalid-policy')
            artifacts = task.get('artifacts', [])
            if (not isinstance(artifacts, list) or len(artifacts) > 16
                    or any(not isinstance(value, str) or not value.strip() or len(value) > 1000 for value in artifacts)
                    or len(set(artifacts)) != len(artifacts)):
                raise BoardError('board/invalid-artifacts')
            if task['verification_policy'] == 'artifact' and not artifacts:
                raise BoardError('board/missing-artifacts')
            if task.get('status') not in STATES:
                raise BoardError('board/invalid-status')
            for key in ('depends_on', 'allowed_paths'):
                value = task.get(key)
                if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
                    raise BoardError('board/invalid-contract')
                if len(value) != len(set(value)):
                    raise BoardError('board/duplicate-value')
            if not task['allowed_paths']:
                raise BoardError('board/missing-paths')
            for path in [*task['allowed_paths'], *artifacts]:
                if path.startswith(('/', '\\')) or PureWindowsPath(path).drive or '..' in path.replace('\\', '/').split('/'):
                    raise BoardError('board/unsafe-scope')
            if 'auto_ready' in task and type(task['auto_ready']) is not bool:
                raise BoardError('board/invalid-contract')
            if 'execution_prepared' in task and type(task['execution_prepared']) is not bool:
                raise BoardError('board/invalid-evidence')
            for key in ('owner', 'branch', 'base_commit', 'blocker', 'resume_condition', 'commit', 'target_branch', 'integration_commit', 'verification', 'execution_session_id', 'execution_run_id', 'execution_key', 'review_error', 'review_session_id', 'review_run_id', 'review_key'):
                if key in task and (not isinstance(task[key], str) or len(task[key]) > 10000):
                    raise BoardError('board/invalid-evidence')
            if task['status'] in {'IN_PROGRESS', 'REVIEW', 'DONE'} and not all(task.get(key, '').strip() for key in (('owner', 'branch', 'base_commit') if task['verification_policy'] == 'git' else ('owner',))):
                raise BoardError('board/missing-claim')
            if task['status'] == 'BLOCKED' and not all(task.get(key, '').strip() for key in ('blocker', 'resume_condition')):
                raise BoardError('board/missing-blocker')
            if task['status'] == 'DONE' and task['verification_policy'] == 'git' and not all(task.get(key, '').strip() for key in ('commit', 'target_branch', 'integration_commit', 'verification')):
                raise BoardError('board/missing-integration-evidence')
            if task['status'] == 'DONE' and task['verification_policy'] == 'artifact' and task['id'] != completing:
                proof = task.get('verification_result')
                if (not isinstance(proof, dict) or proof.get('verified') is not True or proof.get('policy') != 'artifact'
                        or proof.get('review_run_id') != task.get('review_run_id') or proof.get('criteria') != task['acceptance']):
                    raise BoardError('board/review-required')
        by_id = {task['id']: task for task in tasks}
        visited: set[str] = set()
        active: set[str] = set()

        for ident in by_id:
            stack = [(ident, False)]
            while stack:
                node, leaving = stack.pop()
                if leaving:
                    active.remove(node)
                    visited.add(node)
                    continue
                if node in active:
                    raise BoardError('board/dependency-cycle')
                if node in visited:
                    continue
                active.add(node)
                stack.append((node, True))
                task = by_id[node]
                for dependency in reversed(task['depends_on']):
                    if dependency not in by_id:
                        raise BoardError('board/unknown-dependency')
                    if task['status'] in {'READY', 'IN_PROGRESS', 'REVIEW', 'DONE'} and by_id[dependency]['status'] != 'DONE':
                        raise BoardError('board/dependency-incomplete')
                    stack.append((dependency, False))

    def update(self, revision: str, task: dict[str, Any], *, create: bool = False) -> dict[str, Any]:
        """按内容版本新增或更新一项任务，失败时原文件保持不变。"""
        with _LOCK:
            board = self.read()
            if isinstance(task, dict) and 'verification_result' in task:
                raise BoardError('board/host-evidence-only')
            if not isinstance(revision, str) or revision != board['revision']:
                raise BoardError('board/stale-revision')
            if not isinstance(task, dict):
                raise BoardError('board/invalid-task')
            tasks = board['tasks']
            old = next((item for item in tasks if item['id'] == task.get('id')), None)
            if create:
                if old is not None:
                    raise BoardError('board/duplicate-id')
                if task.get('status', 'BACKLOG') not in {'BACKLOG', 'READY'}:
                    raise BoardError('board/invalid-initial-status')
                tasks.append({'status': 'BACKLOG', 'depends_on': [], 'verification_policy': 'git', **task})
            else:
                if old is None:
                    raise BoardError('board/unknown-task')
                merged = {**old, **task}
                if old['status'] == 'DONE' and merged != old:
                    raise BoardError('board/completed-immutable')
                if old['status'] not in {'BACKLOG', 'READY'} and any(merged.get(key) != old.get(key) for key in ('title', 'acceptance', 'allowed_paths', 'depends_on', 'verification_policy', 'artifacts')):
                    raise BoardError('board/contract-locked')
                if not isinstance(merged.get('status'), str) or merged['status'] not in STATES:
                    raise BoardError('board/invalid-status')
                if merged['status'] != old['status'] and merged['status'] not in TRANSITIONS[old['status']]:
                    raise BoardError('board/invalid-transition')
                if old['status'] != 'BACKLOG' and merged['status'] == 'BACKLOG' and 'auto_ready' not in task:
                    merged['auto_ready'] = False
                tasks[tasks.index(old)] = merged
            payload = {'schema': 2, 'tasks': tasks}
            completing = merged['id'] if not create and old is not None and old['status'] != 'DONE' and merged['status'] == 'DONE' else None
            self._validate(payload, completing=completing)
            if not create and old is not None and old['status'] != 'DONE' and merged['status'] == 'DONE':
                if merged['verification_policy'] == 'git':
                    from .git_evidence import GitEvidence
                    verified = GitEvidence(self.root).verify(merged)
                    for key in ('base_commit', 'commit', 'integration_commit'):
                        merged[key] = verified[key]
                else:
                    if self.verify_artifacts is None:
                        raise BoardError('board/review-context-required')
                    merged['verification_result'] = self.verify_artifacts(self.root, merged)
            self._promote_dependencies(payload)
            self._validate(payload)
            return self._write(payload, revision)

    @staticmethod
    def _promote_dependencies(payload: dict[str, Any]) -> None:
        """只推进显式等待依赖的合同，不领取任务、不绕过验收或解除阻塞。"""
        by_id = {task['id']: task for task in payload['tasks']}
        for task in payload['tasks']:
            if task['status'] == 'BACKLOG' and task.get('auto_ready') is True and all(
                    by_id.get(dependency, {}).get('status') == 'DONE' for dependency in task['depends_on']):
                task['status'] = 'READY'

    def _write(self, payload: dict[str, Any], revision: str) -> dict[str, Any]:
        """持有任务板锁时原子保存，并在替换前再次核对磁盘版本。"""
        raw = (json.dumps(payload, ensure_ascii=False, indent=2) + '\n').encode()
        if hashlib.sha256(raw).hexdigest() == revision:
            current = self.read()
            if current['revision'] != revision:
                raise BoardError('board/stale-revision')
            return current
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._check_path()
        if len(raw) > MAX_BOARD_BYTES:
            raise BoardError('board/too-large')
        descriptor, temporary = tempfile.mkstemp(prefix='.task-board-', dir=self.path.parent)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            if self.read()['revision'] != revision:
                raise BoardError('board/stale-revision')
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return self.read()

    def append_plan(self, revision: str, proposed: list[dict[str, Any]]) -> dict[str, Any]:
        """追加完整计划；自动推导初始状态，不覆盖已有任务或伪造执行证据。"""
        with _LOCK:
            current = self.read()
            if not isinstance(revision, str) or revision != current['revision']:
                raise BoardError('board/stale-revision')
            if not isinstance(proposed, list) or not 1 <= len(proposed) <= 100:
                raise BoardError('board/invalid-plan')
            fields = {'id', 'title', 'allowed_paths', 'acceptance', 'depends_on', 'verification_policy', 'artifacts'}
            existing = {task['id']: task for task in current['tasks']}
            additions = []
            for task in proposed:
                if not isinstance(task, dict) or set(task) - fields:
                    raise BoardError('board/invalid-plan')
                if not isinstance(task.get('id'), str):
                    raise BoardError('board/invalid-id')
                if task['id'] in existing:
                    raise BoardError('board/duplicate-id')
                dependencies = task.get('depends_on', [])
                if not isinstance(dependencies, list) or any(not isinstance(value, str) for value in dependencies):
                    raise BoardError('board/invalid-contract')
                ready = all(existing.get(value, {}).get('status') == 'DONE' for value in dependencies)
                additions.append({'verification_policy': 'git', **task, 'depends_on': dependencies, 'status': 'READY' if ready else 'BACKLOG', 'auto_ready': not ready})
            payload = {'schema': 2, 'tasks': current['tasks'] + additions}
            self._validate(payload)
            self._promote_dependencies(payload)
            self._validate(payload)
            return self._write(payload, revision)
