"""团队派工共用的工作树准备和交付服务。"""
from __future__ import annotations

import asyncio
from pathlib import Path

from ..core.cleanup import join_cleanup
from ..core.errors import HarnessError
from ..i18n import tr


async def _mutation(operation):
    """取消时等宿主写入结束，避免遗落后台 Git 创建或合并。"""
    task = asyncio.create_task(asyncio.to_thread(operation))
    return await join_cleanup(task)


class TeamWorkspaces:
    def __init__(self, harness, session, parent, team, *, working_copy=False):
        self.harness, self.session, self.parent, self.team = harness, session, parent, team
        self.working_copy = working_copy
        self.records = {}
        self.snapshot = None
        self.base = None

    def check(self):
        """每次写入前重新检查当前权限，计划模式不创建工作树。"""
        if (self.harness.worktrees is None or self.parent.recovery_read_only
                or self.parent.task_read_only or self.harness.plan.is_active(self.session)):
            raise HarnessError(tr('agent.team.worktrees_unavailable'), code='agent/worktrees-unavailable')

    async def freeze(self, run_id):
        self.check()
        def freeze():
            manager = self.harness.worktrees
            with manager._mutex:
                self.check()
                if self.base is None:
                    if self.working_copy:
                        self.snapshot = manager.freeze_team_source(self.session.header.cwd,
                            team=self.team.name, owner_session_id=self.session.id, run_id=run_id)
                        self.base = self.snapshot['base_commit']
                    else:
                        self.base = manager.git(Path(self.session.header.cwd), 'rev-parse', '--verify', 'HEAD^{commit}').strip()
        await _mutation(freeze)

    async def create(self, run_id, members):
        await self.freeze(run_id)
        def create():
            manager = self.harness.worktrees
            with manager._mutex:
                self.check()
                missing = [name for name in members if name not in self.records]
                rows = manager.create_team(self.session.header.cwd, missing, team=self.team.name,
                    owner_session_id=self.session.id, run_id=run_id, ref=self.base) if missing else []
                for row in rows:
                    if self.snapshot:
                        row['source_snapshot'] = {**self.snapshot, 'status': 'pending'}
                    self.records[row['team_member']] = row
                return [self.records[name] for name in members]
        return await _mutation(create)

    async def initialize(self, rows):
        if not self.snapshot:
            return
        def initialize():
            manager = self.harness.worktrees
            with manager._mutex:
                for row in rows:
                    if row.get('source_snapshot', {}).get('status') != 'pending':
                        continue
                    self.check()
                    try:
                        manager.initialize_team_source(row['id'], self.snapshot)
                    finally:
                        row.update(manager.get(row['id']))
        await _mutation(initialize)

    async def prepare(self, member, dependencies):
        self.check()
        def prepare():
            manager = self.harness.worktrees
            with manager._mutex:
                self.check()
                wid = self.records[member]['id']
                sources = [self.records[name]['id'] for name in dependencies]
                plan = manager.plan_delivery_merge(wid, sources)
                if not plan['clean']:
                    raise HarnessError(tr('ui.git.error.conflicts'), code='git/delivery-conflicts',
                        conflicts=plan['conflicts'], source=plan['conflicted_source'], member=member)
                return manager.apply_delivery_merge(wid, sources,
                    expected_target_tree=plan['target_tree'], expected_tree=plan['tree'])
        return await _mutation(prepare)

    async def finalize(self, member):
        self.check()
        return await _mutation(lambda: self.harness.worktrees.capture_delivery(self.records[member]['id']))
