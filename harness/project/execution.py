"""从已保存任务建立正式 Agent 运行，复用持久队列与工作区 Git。"""
from __future__ import annotations

import json
import asyncio
import logging
import fnmatch
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

from ..core.cleanup import join_cleanup, raise_cleanup_errors
from ..i18n import tr
from ..tools.process_env import execution_env

from .board import BoardError, TaskBoard
from .error_messages import board_error_message
from .git_evidence import GitEvidence
from .lease import RepositoryLease
from .operations import run_owned

from .agent_selection import bind_project_agent, project_agent_selection
ACTIVE = {'queued', 'running'}


class ProjectExecution:
    """专用会话与仓库锁绑定到真实运行句柄，不按时间猜测运行结束。"""

    def __init__(self, harness):
        self.harness = harness
        self.leases: dict[str, tuple[str, RepositoryLease]] = {}
        self.preparing: set[str] = set()
        self.reviews: dict[str, asyncio.Task] = {}
        self._lease_failures: list[BaseException] = []
        self._close_task: asyncio.Task | None = None

    def _existing(self, root: Path, task: dict[str, Any], *, review=False):
        prefix = 'review' if review else 'execution'
        identifier = task.get(prefix + '_run_id')
        record = self.harness.runtime.records.get(identifier)
        if record is None:
            return None
        session = self.harness.get_session(record.session_id)
        if not session.header.cwd:
            raise BoardError('board/execution-mismatch')
        if record.session_id != task.get(prefix + '_session_id') or Path(session.header.cwd).resolve() != root:
            raise BoardError('board/execution-mismatch')
        return record

    def _preflight(self, root: Path, task):
        evidence = GitEvidence(root)
        snapshot = evidence.snapshot()
        if not snapshot['available'] or not snapshot['head'] or not snapshot['branch']:
            raise BoardError('board/git-branch-required')
        repo = Path(snapshot['root']).resolve()
        result = evidence._run('rev-parse', '--absolute-git-dir')
        if result.returncode:
            raise BoardError('board/git-read-failed')
        lease = RepositoryLease(Path(result.stdout.decode().strip()))
        try:
            # 切换分支影响整个仓库，检查范围不能仅限项目子目录。
            status = evidence._run('status', '--porcelain=v1', '-z', '--untracked-files=all')
            if status.returncode or len(status.stdout) > 65536:
                raise BoardError('board/working-tree-changed')
            entries = status.stdout.decode('utf-8', errors='surrogateescape').split('\0')
            paths, index = [], 0
            while index < len(entries) and entries[index]:
                entry = entries[index]
                paths.append(entry[3:])
                index += 1
                if 'R' in entry[:2] or 'C' in entry[:2]:
                    paths.append(entries[index])
                    index += 1
            board_path = TaskBoard(root).path.relative_to(repo).as_posix()
            for path in paths:
                if path == board_path:
                    continue
                try:
                    relative = (repo / path).relative_to(root).as_posix()
                except ValueError:
                    raise BoardError('board/working-tree-changed')
                may_resume = task['status'] == 'IN_PROGRESS' and task['branch'] == snapshot['branch']
                if not may_resume or not any(fnmatch.fnmatchcase(relative, pattern) for pattern in task['allowed_paths']):
                    raise BoardError('board/working-tree-changed')
            return evidence, snapshot, repo, lease
        except BaseException as primary:
            try:
                lease.close()
            except BaseException as cleanup:
                raise_cleanup_errors([primary, cleanup])
            raise

    def _switch(self, root: Path, branch: str, *, base: str | None = None):
        """只切换本目录分支，不迁移、暂存、删除用户文件或创建 worktree。"""
        evidence = GitEvidence(root)
        if not isinstance(branch, str) or not branch or evidence._run('check-ref-format', 'refs/heads/' + branch).returncode:
            raise BoardError('board/git-invalid-target')
        command = ['git', '-c', 'core.fsmonitor=false', 'switch']
        command += ['-c', branch, base] if base is not None else [branch]
        try:
            result = subprocess.run(command, cwd=root, env=execution_env(), stdin=subprocess.DEVNULL, capture_output=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BoardError('board/git-switch-failed') from exc
        if result.returncode:
            raise BoardError('board/git-switch-failed')

    async def launch(self, root: Path, task_id: str, revision: str, key: str, source_session_id: str | None = None):
        root = root.resolve()
        scope = str(root)
        board = TaskBoard(root, verify_artifacts=self.harness.artifact_review.verify)
        current = board.read()
        task = next((row for row in current['tasks'] if row['id'] == task_id), None)
        if task is None:
            raise BoardError('board/unknown-task')
        artifact = task['verification_policy'] == 'artifact'
        reviewing = artifact and (task['status'] == 'REVIEW' or task.get('review_key') == key)
        prefix = 'review' if reviewing else 'execution'
        existing = self._existing(root, task, review=reviewing)
        if task.get(prefix + '_key') == key and existing is not None:
            return {'board': current, 'run': existing.to_dict(), 'session_id': existing.session_id, 'reused': True}
        if revision != current['revision']:
            raise BoardError('board/stale-revision')
        if task['status'] not in {'READY', 'IN_PROGRESS', 'REVIEW'}:
            raise BoardError('board/task-not-executable')
        if existing is not None and existing.status in ACTIVE and existing.task is not None and not existing.task.done():
            return {'board': current, 'run': existing.to_dict(), 'session_id': existing.session_id, 'reused': True}
        if scope in self.preparing:
            raise BoardError('board/project-busy')
        if not self.harness.config.enable_fs or not artifact and not self.harness.config.enable_shell:
            raise BoardError('board/execution-disabled')
        if source_session_id is None and existing is not None:
            source_session_id = existing.session_id
        source_policy = self.harness.permissions.snapshot(source_session_id) if source_session_id else None
        selection = None if reviewing else project_agent_selection(self.harness, root, source_session_id)
        if not self.harness.config.api_key and not self.harness.config.api_keys and not self.harness.config.model_routes:
            raise BoardError('board/model-required')
        self.preparing.add(scope)
        lease = None
        session = None
        record = None
        claimed_revision = None
        preparation = None
        release = None
        try:
            if artifact:
                evidence, snapshot, repo = None, {}, root
            else:
                evidence, snapshot, repo, lease = await run_owned(lambda: self._preflight(root, task), cancel_cleanup=lambda result: result[3].close())
            for run in self.harness.runtime.live_records():
                if run.status in ACTIVE and run.task is not None and not run.task.done():
                    active_session = self.harness.get_session(run.session_id)
                    if Path(active_session.header.cwd or self.harness.config.cwd).resolve().is_relative_to(repo):
                        raise BoardError('board/project-busy')
            preparation = self.harness.workspace_preparations.claim(repo)
            if board.read()['revision'] != revision:
                raise BoardError('board/stale-revision')
            if not artifact:
                if task['status'] == 'READY':
                    branch = 'codex/task-' + task_id.lower() + '-' + uuid.uuid4().hex[:8]
                    base = snapshot['head']
                    target = task.get('target_branch') or snapshot['branch']
                    if evidence._run('rev-parse', '--verify', '--end-of-options', 'refs/heads/' + target + '^{commit}').returncode:
                        raise BoardError('board/git-target-missing')
                    create_branch = True
                else:
                    branch, base, target = task['branch'], task['base_commit'], task.get('target_branch') or snapshot['branch']
                    resolved_base = evidence._commit(base)
                    reference = evidence._run('rev-parse', '--verify', '--end-of-options', 'refs/heads/' + branch + '^{commit}')
                    create_branch = bool(reference.returncode) and task.get('execution_prepared') is False
                    if reference.returncode and not create_branch:
                        raise BoardError('board/git-switch-failed')
                    if not reference.returncode:
                        evidence._ancestor(resolved_base, reference.stdout.decode().strip(), 'git-base-unrelated')
            preserved = existing is not None and existing.status == 'queued' and existing.task is None
            session = self.harness.get_session(existing.session_id) if preserved else self.harness.create_session(cwd=root, title=task_id + ' · ' + task['title'][:120])
            if source_session_id and not preserved:
                source = self.harness.get_session(source_session_id)
                preferences = {key: source.header.metadata[key] for key in ('model', 'reasoning_effort') if key in source.header.metadata}
                if preferences:
                    self.harness.sessions.update_header(session.id, metadata=preferences)
            await self.harness.runtime.set_queue_paused(session.id, True)
            if reviewing or source_policy and not source_policy['inherited']:
                state = self.harness.permissions.snapshot(session.id)
                await self.harness.permissions.set_policy(session.id, 'read_only' if reviewing else source_policy['permission_policy'], expected_version=state['version'])
            if not preserved:
                await bind_project_agent(self.harness, session, selection)
            contract = {**task, 'owner': 'agent:' + session.id}
            if not artifact:
                contract.update(branch=branch, base_commit=base, target_branch=target)
            if reviewing:
                marker = await run_owned(lambda: self.harness.artifact_review.prepare(root, task))
                self.harness.sessions.update_header(session.id, metadata={'project_task_review': marker})
                text = self.harness.artifact_review.prompt(task, marker)
            else:
                text = tr('project.execution.prompt', task_id=task_id,
                          delivery=tr('project.execution.artifact' if artifact else 'project.execution.git'),
                          contract=json.dumps(contract, ensure_ascii=False, indent=2))
                if task['status'] == 'REVIEW':
                    text = tr('project.execution.review_only') + '\n' + text
            record = existing if preserved else self.harness.runtime.submit(session.id, text, key=key)
            patch = {'id': task_id, 'status': 'REVIEW' if reviewing or task['status'] == 'REVIEW' else 'IN_PROGRESS',
                     prefix + '_session_id': session.id, prefix + '_run_id': record.id, prefix + '_key': key,
                     'execution_prepared': False}
            if not reviewing:
                patch['owner'] = contract['owner']
            else:
                patch['review_error'] = ''
            if not artifact:
                patch.update(branch=branch, base_commit=base, target_branch=target)
            updated = board.update(revision, patch)
            claimed_revision = updated['revision']
            if not artifact and task['status'] != 'REVIEW' and (create_branch or snapshot['branch'] != branch):
                await run_owned(lambda: self._switch(root, branch, base=base if create_branch else None))
            if record.task is not None and record.task.done():
                raise BoardError('board/execution-cancelled')
            updated = board.update(claimed_revision, {'id': task_id, 'execution_prepared': True})
            claimed_revision = updated['revision']
            if lease is not None:
                owned_lease = lease
                self.leases[str(repo)] = (record.id, owned_lease)
                def release(_):
                    self._release_lease(str(repo), record.id, owned_lease)
                if record.task is not None:
                    record.task.add_done_callback(release)
                lease = None
            if reviewing and record.task is not None:
                record.task.add_done_callback(lambda _: self._review_finished(root, task_id, record))
            await self.harness.runtime.set_queue_paused(session.id, False)
            if preserved and release is not None:
                record.task.add_done_callback(release)
            return {'board': updated, 'run': record.to_dict(), 'session_id': session.id, 'reused': False}
        except BaseException:
            if record is not None and record.status == 'queued':
                self.harness.runtime.remove(record.id)
            if claimed_revision is not None:
                try:
                    board.update(claimed_revision, {'id': task_id, 'status': 'BLOCKED',
                        'blocker': tr('project.execution.blocker'),
                        'resume_condition': tr('project.execution.resume')})
                except (BoardError, OSError):
                    pass
            raise
        finally:
            primary = sys.exception()
            errors = []
            cleanup = []
            if preparation is not None:
                cleanup.append(lambda: self.harness.workspace_preparations.release(preparation))
            if release is not None and record is not None and record.task is None:
                cleanup.append(lambda: release(None))
            if lease is not None:
                cleanup.append(lease.close)
            for operation in cleanup:
                try:
                    operation()
                except BaseException as error:
                    errors.append(error)
            self.preparing.discard(scope)
            if errors:
                raise_cleanup_errors(([primary] if primary is not None else []) + errors)

    def _review_finished(self, root, task_id, record):
        """审阅完成只触发证据核验，不直接把模型结束映射为业务完成。"""
        pending = asyncio.create_task(self._settle_review(root, task_id, record))
        self.reviews[record.id] = pending
        def finished(task):
            self.reviews.pop(record.id, None)
            if not task.cancelled() and task.exception() is not None:
                logging.getLogger(__name__).error(tr('log.3fd5a0d977be'), task.exception())
        pending.add_done_callback(finished)

    async def _settle_review(self, root, task_id, record):
        board = TaskBoard(root, verify_artifacts=self.harness.artifact_review.verify)
        try:
            current = await run_owned(board.read)
            task = next((row for row in current['tasks'] if row['id'] == task_id), None)
            if task is None or task['status'] != 'REVIEW' or task.get('review_run_id') != record.id:
                return
            if record.status != 'completed':
                raise BoardError('board/review-unverified')
            await run_owned(lambda: board.update(current['revision'], {'id': task_id, 'status': 'DONE'}))
        except Exception as exc:
            message = board_error_message(str(exc)) if isinstance(exc, BoardError) else str(exc)
            try:
                current = await run_owned(board.read)
                task = next((row for row in current['tasks'] if row['id'] == task_id), None)
                if task is not None and task['status'] == 'REVIEW' and task.get('review_run_id') == record.id:
                    await run_owned(lambda: board.update(current['revision'], {'id': task_id, 'review_error': message}))
            except (BoardError, OSError) as storage_error:
                message = tr('project.review.persistence_failed', error=message, storage=board_error_message(str(storage_error)) if isinstance(storage_error, BoardError) else str(storage_error))
            await self.harness.bus.emit('error', {'session_id': record.session_id, 'code': 'board/review-unverified', 'message': message})

    def _release_lease(self, repo, run_id, lease):
        """回调关闭失败保留原异常与租约，宿主关闭时统一处理。"""
        try:
            lease.close()
        except BaseException as error:
            self._lease_failures.append(error)
            raise
        if self.leases.get(repo) == (run_id, lease):
            self.leases.pop(repo, None)

    async def close(self):
        """观察者取消不截断验收与租约回收，失败后允许显式重试。"""
        if self._close_task is None or self._close_task.done():
            self._close_task = asyncio.create_task(self._close_owned())
        await join_cleanup(self._close_task)

    async def _close_owned(self):
        errors, self._lease_failures = self._lease_failures, []
        while self.reviews:
            pending = dict(self.reviews)
            results = await asyncio.gather(*pending.values(), return_exceptions=True)
            errors.extend(value for value in results if isinstance(value, BaseException))
            for identity, task in pending.items():
                if self.reviews.get(identity) is task:
                    self.reviews.pop(identity, None)
        for repo, (run_id, lease) in list(self.leases.items()):
            try:
                lease.close()
            except BaseException as error:
                if not any(error is previous for previous in errors):
                    errors.append(error)
            else:
                if self.leases.get(repo) == (run_id, lease):
                    self.leases.pop(repo, None)
        raise_cleanup_errors(errors)
