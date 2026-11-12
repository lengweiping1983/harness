"""从项目目标启动可停止、可恢复的正式规划者运行。"""
from __future__ import annotations

from pathlib import Path
from ..i18n import tr
from .board import BoardError, TaskBoard
from .error_messages import board_error_message

from .agent_selection import bind_project_agent, project_agent_selection


class ProjectPlanning:
    """复用持久会话与运行幂等记录，不另建模型客户端。"""

    def __init__(self, harness):
        self.harness = harness
        self.preparing: set[str] = set()

    def guard(self, definition, args, ctx):
        """规划会话及其成员只能读取事实和追加合同，不能变成隐式开发运行。"""
        scope = self.harness.permissions.scope_for(ctx.session_id)
        marker = self.harness.get_session(scope).header.metadata.get('project_board_generation')
        if not isinstance(marker, dict):
            return None
        readable = {'read', 'glob', 'grep', 'project_board_read', 'project_git_check'}
        if definition.schema.name in readable and definition.effect == 'read' and not definition.dangerous:
            return None
        # 显式选择的原生团队可委派成员，所有后代继续受同一规划边界约束。
        run = self.harness._prompt_team_runs.get(ctx.session_id)
        if run:
            name = definition.schema.name
            if name == 'spawn_agent' and any(member.name == args.get('member') and member.role != 'lead' for member in run.team.members):
                return None
            if name == 'followup_task':
                previous = self.harness.subagents.get(args.get('agent_id', ''))
                if previous is not None and previous.scope == run.scope and any(
                        member.name == (previous.prompt_input or {}).get('member') and member.role != 'lead'
                        for member in run.team.members):
                    return None
            if name in {'wait_agent', 'wait_agents', 'list_agents', 'send_message', 'interrupt_agent'}:
                return None
        if definition.schema.name == 'project_tasks_generate' and str(Path(ctx.cwd or self.harness.config.cwd).resolve()) == marker.get('root'):
            return None
        return board_error_message('board/planning-read-only')

    async def generate(self, root: Path, revision: str, goal: str, key: str, source_session_id: str | None = None):
        root = root.resolve()
        if not isinstance(goal, str) or not goal.strip() or len(goal) > 10000:
            raise BoardError('board/invalid-request')
        if not isinstance(key, str) or not key or len(key) > 128:
            raise BoardError('board/invalid-request')
        current = TaskBoard(root).read()
        scope = str(root)
        # 会话头和正式提交键都持久化；断线重试先查事实，不再启动一次。
        for header in self.harness.sessions.list_sessions():
            marker = header.metadata.get('project_board_generation')
            if not isinstance(marker, dict) or marker.get('root') != scope or marker.get('key') != key:
                continue
            if marker.get('goal') != goal:
                raise BoardError('board/invalid-request')
            record = self.harness.runtime.submission(header.id, key)
            if record is not None:
                return {'board': current, 'run': record.to_dict(), 'session_id': header.id, 'reused': True}
        if current['revision'] != revision:
            raise BoardError('board/stale-revision')
        if scope in self.preparing:
            raise BoardError('board/project-busy')
        if not self.harness.config.enable_fs:
            raise BoardError('board/execution-disabled')
        if not self.harness.config.api_key and not self.harness.config.api_keys and not self.harness.config.model_routes:
            raise BoardError('board/model-required')
        selection = project_agent_selection(self.harness, root, source_session_id, planning=True)
        for record in self.harness.runtime.live_records():
            if record.status in {'queued', 'running'} and record.task is not None and not record.task.done():
                session = self.harness.get_session(record.session_id)
                if Path(session.header.cwd or self.harness.config.cwd).resolve() == root:
                    raise BoardError('board/project-busy')
        policy = self.harness.permissions.snapshot(source_session_id) if source_session_id else None
        self.preparing.add(scope)
        preparation = None
        record = None
        try:
            preparation = self.harness.workspace_preparations.claim(root)
            session = self.harness.create_session(cwd=root, title=tr('project.planning.title', goal=goal.strip()[:80]))
            metadata = {'project_board_generation': {'root': scope, 'key': key, 'goal': goal, 'revision': revision}}
            if source_session_id:
                source = self.harness.get_session(source_session_id)
                metadata.update({name: source.header.metadata[name] for name in ('model', 'reasoning_effort') if name in source.header.metadata})
            self.harness.sessions.update_header(session.id, metadata=metadata)
            await self.harness.runtime.set_queue_paused(session.id, True)
            if policy and not policy['inherited']:
                state = self.harness.permissions.snapshot(session.id)
                await self.harness.permissions.set_policy(session.id, policy['permission_policy'], expected_version=state['version'])
            await bind_project_agent(self.harness, session, selection)
            if TaskBoard(root).read()['revision'] != revision:
                raise BoardError('board/stale-revision')
            text = tr('project.planning.prompt', goal=goal)
            record = self.harness.runtime.submit(session.id, text, key=key)
            await self.harness.runtime.set_queue_paused(session.id, False)
            return {'board': current, 'run': record.to_dict(), 'session_id': session.id, 'reused': False}
        except BaseException:
            if record is not None and record.status == 'queued':
                self.harness.runtime.remove(record.id)
            raise
        finally:
            if preparation is not None:
                self.harness.workspace_preparations.release(preparation)
            self.preparing.discard(scope)
