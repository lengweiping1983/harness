"""只读核对任务交付提交与目标分支，不把 Git 可达性当成测试通过。"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any

from harness.app.review import ReviewService
from harness.core.errors import HarnessError
from .board import BoardError


class GitEvidence:
    """复用工作区审阅的只读 Git 执行器与超时保护。"""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.review = ReviewService()
        self.deadline = time.monotonic() + 10

    def _run(self, *args: str):
        """禁止环境变量把证据检查重定向到其他仓库。"""
        overrides = ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_COMMON_DIR', 'GIT_INDEX_FILE',
                     'GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES', 'GIT_NAMESPACE')
        if any(name in os.environ for name in overrides):
            raise BoardError('board/git-environment-override')
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise BoardError('board/git-timeout')
        try:
            return self.review._git(self.root, *args, output_limit=65536, timeout=remaining)
        except HarnessError as exc:
            code = 'git-timeout' if exc.code == 'review/timeout' else 'git-unavailable'
            raise BoardError('board/' + code) from exc

    def snapshot(self) -> dict[str, Any]:
        """读取分支和 HEAD，状态预览有界，不写入索引。"""
        self.deadline = time.monotonic() + 10
        found = self._run('rev-parse', '--show-toplevel')
        if found.returncode:
            return {'available': False, 'reason': 'board/git-not-repository'}
        head = self._run('rev-parse', '--verify', 'HEAD')
        branch = self._run('symbolic-ref', '--quiet', '--short', 'HEAD')
        status = self._run('status', '--porcelain=v1', '-z', '--untracked-files=normal')
        if status.returncode:
            raise BoardError('board/git-read-failed')
        return {'available': True, 'root': found.stdout.decode('utf-8').strip(),
                'head': head.stdout.decode().strip() if head.returncode == 0 else None,
                'branch': branch.stdout.decode('utf-8').strip() if branch.returncode == 0 else None,
                'dirty': bool(status.stdout), 'status_truncated': len(status.stdout) > 65536}

    def _commit(self, value: Any) -> str:
        """只接受提交哈希，不解释用户提供的任意 revision 表达式。"""
        if not isinstance(value, str) or not re.fullmatch(r'[0-9a-fA-F]{7,64}', value):
            raise BoardError('board/git-invalid-commit')
        result = self._run('rev-parse', '--verify', '--end-of-options', value + '^{commit}')
        if result.returncode:
            raise BoardError('board/git-unknown-commit')
        return result.stdout.decode().strip()

    def _ancestor(self, older: str, newer: str, code: str) -> None:
        """提交必须在指定历史中，Git 错误不能当成正常的不包含。"""
        result = self._run('merge-base', '--is-ancestor', older, newer)
        if result.returncode == 1:
            raise BoardError('board/' + code)
        if result.returncode:
            raise BoardError('board/git-read-failed')

    def verify(self, task: dict[str, Any]) -> dict[str, Any]:
        """核对真实提交链；不执行测试、切换分支、合并或创建提交。"""
        snapshot = self.snapshot()
        if not snapshot['available']:
            raise BoardError(snapshot['reason'])
        if not isinstance(task, dict):
            raise BoardError('board/invalid-task')
        base = self._commit(task.get('base_commit'))
        delivery = self._commit(task.get('commit'))
        integration = self._commit(task.get('integration_commit'))
        target = task.get('target_branch')
        if not isinstance(target, str) or not target or len(target) > 255 or '\0' in target:
            raise BoardError('board/git-invalid-target')
        reference = 'refs/heads/' + target
        if self._run('check-ref-format', reference).returncode:
            raise BoardError('board/git-invalid-target')
        result = self._run('rev-parse', '--verify', '--end-of-options', reference + '^{commit}')
        if result.returncode:
            raise BoardError('board/git-target-missing')
        target_head = result.stdout.decode().strip()
        self._ancestor(base, delivery, 'git-base-unrelated')
        self._ancestor(delivery, integration, 'git-delivery-not-integrated')
        self._ancestor(integration, target_head, 'git-integration-not-on-target')
        # 核验期间目标分支移动时，不返回已经过期的成功快照。
        latest = self._run('rev-parse', '--verify', '--end-of-options', reference + '^{commit}')
        if latest.returncode or latest.stdout.decode().strip() != target_head:
            raise BoardError('board/git-target-changed')
        return {'verified': True, 'base_commit': base, 'commit': delivery,
                'integration_commit': integration, 'target_branch': target, 'target_head': target_head,
                'tests_verified': False}
