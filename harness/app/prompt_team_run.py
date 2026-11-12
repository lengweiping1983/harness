"""主代理自主派工时的宿主约束，不决定工作流或自动派工。"""
from __future__ import annotations

import asyncio
import copy
import json
import uuid
from dataclasses import replace

from ..core.errors import ToolExecutionError
from ..core.cleanup import join_cleanup, raise_cleanup_errors
from ..agents.execution import resolve_team_execution
from ..agents.registry import AgentRegistry
from ..collaboration.acceptance import (acceptance_example, parse_report, evaluate_report,
    acceptance_verdict, requires_acceptance)
from ..session.partials import member_partial_reply
from ..agents.members import member_model, member_persona, lead_member, member_skills
from ..agents.presentation import agent_presentation
from ..i18n import tr
from ..collaboration.delivery import freeze_member_authors, publish_member_reply


class PromptTeamRun:
    def __init__(self, harness, session, parent, team, projection, *, recipients=None, direct=False, persistent_context=True, conversation_context=""):
        self.harness, self.session, self.parent = harness, session, parent
        self.registry = AgentRegistry()
        for agent in harness.agents_for(session).list_all():
            self.registry.register(copy.deepcopy(agent))
        self.direct = direct
        self.conversation_context = conversation_context
        snapshot = copy.deepcopy(team)
        if direct:
            names = {member.name for member in snapshot.members}
            if (not isinstance(recipients, list) or not 1 <= len(recipients) <= 8
                    or any(not isinstance(name, str) or name not in names for name in recipients)):
                raise ToolExecutionError(tr('collaboration.invalid_recipients'))
        from ..agents.dependencies import validate_dependencies
        validate_dependencies(snapshot.members)
        self.authors = freeze_member_authors(snapshot, self.registry)
        self.team = resolve_team_execution(snapshot, self.registry)
        self.run_id = getattr(parent, 'run_id', None) or uuid.uuid4().hex
        self.scope = 'prompt:' + self.run_id
        from ..agents.limits import member_wait_timeout, reply_limit, member_retry_limit
        try:
            self.timeout = member_wait_timeout(self.team.metadata, default=600)
        except ValueError as exc:
            raise ToolExecutionError(tr('agent.team.timeout_invalid')) from exc
        self.member_retry_limit = member_retry_limit(self.team.metadata)
        self.member_reply_limit = reply_limit(self.team.metadata, 'member_reply_max_chars')
        self.summary_reply_limit = reply_limit(self.team.metadata, 'summary_max_chars')
        self.recipients = recipients or []
        self.states = {}
        self.members = {}
        self.lock = asyncio.Lock()
        self.closed = False
        self._close_task = None
        self.goal = None
        self.steering = None
        self.workspaces = None
        if projection.team_worktrees or projection.team_working_copy:
            from .team_workspaces import TeamWorkspaces
            self.workspaces = TeamWorkspaces(harness, session, parent, self.team,
                working_copy=projection.team_working_copy)
        self.previous_reply_max_chars = parent.reply_max_chars
        self.previous_coordinator = parent.isolated_team_coordinator
        from ..collaboration.member_context import TeamContexts
        self.contexts = (TeamContexts(session, self.team, self.registry)
            if persistent_context and self.team.metadata.get('member_context') != 'ephemeral'
            and getattr(harness.subagents, "contexts", None) is not None else None)
        from ..collaboration.streaming import MemberStreamBridge
        self.streams = MemberStreamBridge(harness.bus, session.id, self.run_id)
        # 可能失败的持久上下文校验完成后，才绑定本轮对主会话的限制。
        parent.reply_max_chars = self.summary_reply_limit
        if self.workspaces is not None or lead_member(self.team).access == 'read':
            parent.isolated_team_coordinator = True
        harness.bus.on('subagent/status', self.status)
        harness._active_team_runs.add((session.id, self.run_id))
        self.parent.add_pre_step_hook(self.step)
        self.previous_schema_transform = parent.tool_schema_transform
        parent.tool_schema_transform = self.tool_schemas

    def tool_schemas(self, schemas):
        """把冻结花名册投影为本次主理人的参数约束，不改共享工具定义。"""
        if self.previous_schema_transform is not None:
            schemas = self.previous_schema_transform(schemas)
        names = [member.name for member in self.team.members
                 if self.direct or member != lead_member(self.team) or len(self.team.members) == 1]
        projected = []
        for schema in schemas:
            if schema.name == 'spawn_agent':
                parameters = copy.deepcopy(dict(schema.parameters))
                properties = parameters.setdefault('properties', {})
                properties['member'] = {**properties.get('member', {'type': 'string'}), 'enum': names}
                required = list(parameters.get('required', []))
                if 'member' not in required:
                    required.append('member')
                parameters['required'] = required
                schema = replace(schema, parameters=parameters)
            projected.append(schema)
        return projected

    def step(self, agent, session, turn, step):
        """仅团队运行绑定回合约束，不改变普通代理的步骤钩子。"""
        self.bind_goal()

    def fail(self, reason):
        raise ToolExecutionError(tr('agent.prompt_team.constraint', p0=reason))

    async def start(self):
        if self.workspaces:
            await self.workspaces.freeze(self.run_id)

    def bind_goal(self):
        if self.steering is None:
            from .team_steering import TeamSteering
            self.steering = TeamSteering(self.harness, self.session, self.parent, self.team,
                acknowledge_parent=self.direct, manage_admission=self.direct)
        if self.goal is not None:
            return
        goal = self.harness.goals.get(self.session.id) if self.harness.config.enable_goal else None
        if goal and goal.phase == 'active' and goal.activation == 'armed':
            from .team_goal import TeamGoal
            self.goal = TeamGoal(self.harness, self.session, goal)
            self.goal.completion_guard = self.completion_feedback
            self.parent.team_goal = self.goal
            self.parent.team_goal_writer = True

    async def emit(self, phase):
        payload = {'team': self.team.name, 'display_name': agent_presentation(self.team)['display_name'],
            'run_id': self.run_id, 'phase': phase,
            'turn': self.session.current_turn, 'members': list(self.members.values()),
            'direct': self.direct, 'member_timeout_seconds': self.timeout,
            **({'workspaces': list(self.workspaces.records.values())} if self.workspaces else {})}
        event = await self.session.append('agent/team', payload)
        await self.harness.bus.emit('agent/team', self.session.id,
            {**payload, 'source_seq': event.seq, 'source_time': event.time})

    async def spawn(self, args, ctx):
        async with self.lock:
            if self.closed or ctx.session_id != self.session.id:
                self.fail('lead_required')
            self.harness.subagents.require_accepting(ctx.session_id)
            name = args.get('member')
            member = next((m for m in self.team.members if m.name == name), None)
            if member is None or (not self.direct and member == lead_member(self.team) and len(self.team.members) > 1):
                available = [m.name for m in self.team.members
                             if self.direct or m != lead_member(self.team) or len(self.team.members) == 1]
                raise ToolExecutionError(tr('agent.prompt_team.unknown_member',
                    member=str(name), available=', '.join(available)))
            if args.get('agent') and args['agent'] != member.agent:
                self.fail('agent_mismatch')
            old = self.states.get(name)
            if old and not old.settled:
                # 已有成员优先给出恢复入口，不让并发上限掩盖可等待的真实运行。
                raise ToolExecutionError(tr('agent.prompt_team.member_busy',
                    member=name, agent_id=old.id, status=old.status))
            active = [state for state in self.states.values() if not state.settled]
            if len(active) >= 4:
                self.fail('concurrency_limit')
            failures = self.harness.subagents.member_failures(self.scope, name)
            if failures > self.member_retry_limit:
                raise ToolExecutionError(tr('agent.team.member_retry_limit', member=name,
                    failures=failures, limit=self.member_retry_limit))
            ids = args.get('dependencies', [])
            if not isinstance(ids, list) or any(not isinstance(value, str) for value in ids) or len(set(ids)) != len(ids):
                self.fail('invalid_dependencies')
            # 声明的依赖由宿主解析当前运行；调用者省略 ID 不能绕过配置。
            ids = list(ids)
            for dependency in member.depends_on:
                if self.direct and dependency not in self.recipients:
                    continue
                state = self.states.get(dependency)
                if state is None:
                    self.fail('invalid_dependencies')
                if state.id not in ids:
                    ids.append(state.id)
            dependencies = []
            for agent_id in ids:
                state = self.harness.subagents.get(agent_id)
                if (state is None or state.scope != self.scope or state.status != 'completed'
                        or not state.settled
                        or not getattr(state, '_prompt_member', None) or state._prompt_member == name
                        or self.states.get(state._prompt_member) is not state):
                    self.fail('invalid_dependencies')
                dependencies.append(state)
            budget = self.parent.budget
            if budget is not None:
                snapshot = budget.snapshot()
                if snapshot['available'] is not None:
                    messages = self.parent._build_messages(self.session)
                    input_tokens = self.parent._request_input_cost(messages, self.parent._active_tool_schemas())
                    output_tokens = self.parent.config.max_tokens or 8192
                    required = input_tokens + output_tokens
                    if snapshot['available'] <= required:
                        raise ToolExecutionError(tr('agent.team.summary_budget_insufficient',
                            available=snapshot['available'], required=required))
            workspace = None
            if self.workspaces:
                rows = await self.workspaces.create(self.run_id, [name])
                await self.workspaces.initialize(rows)
                if dependencies:
                    await self.workspaces.prepare(name, [state._prompt_member for state in dependencies])
                workspace = rows[0]['path']
            prompt = member_persona(member, self.team, self.registry) + '\n\n' + args['prompt']
            if dependencies:
                prompt += '\n\n' + tr('agent.prompt_team.upstream', p0=json.dumps([
                    {'member': state._prompt_member, 'agent_id': state.id,
                     'context_session_id': state.session_id, 'status': state.status,
                     'output': state.output} for state in dependencies], ensure_ascii=False))
            strict = requires_acceptance(self.team.metadata)
            if strict:
                prompt += '\n\n' + tr('agent.team.acceptance_request', p0=acceptance_example())
                prompt += '\n\n' + tr('agent.team.acceptance_evidence')
            model = args.get('model') or member_model(member, self.team, self.registry)
            read_only = member.access == 'read' or self.parent.task_read_only or self.parent.recovery_read_only
            if self.conversation_context and not self.contexts:
                prompt = tr('agent.team.session_context', history=self.conversation_context, prompt=prompt)
            prepared = self.contexts.prepare(member, prompt, workspace, model or self.parent.config.model, read_only) if self.contexts and not member.runner else None
            selected_skills = member_skills(member, self.team, self.registry)
            if args.get('_resume_context') and (prepared is None or prepared['context_session_id'] != args['_resume_context']):
                raise ValueError('agent/context-changed')
            state = self.harness.subagents.spawn(prompt=prepared['prompt'] if prepared else prompt,
                description=args.get('description') or name, parent_session_id=ctx.session_id,
                source_call_id=ctx.call_id, model=args.get('model') or model, runner=member.runner,
                reasoning_effort=args.get('reasoning_effort'), fork_turns=args.get('fork_turns', 'none'),
                prompt_input={**args.get('_prompt_input', {}), 'member': name,
                    'skills': list(selected_skills) if selected_skills is not None else None},
                read_only=read_only, workspace_cwd=workspace, scope=self.scope,
                context_session_id=prepared['context_session_id'] if prepared else None)
            self.states[name] = state
            state._reply_max_chars = self.member_reply_limit
            state._prompt_member = name
            state._timeout_seconds = self.timeout
            state._team_history_scope = (self.session.id, self.run_id)
            state._on_finished = lambda settled: self.finished(member, settled)
            if prepared:
                state._member_context_record = prepared['record']
            self.bind_goal()
            self.steering.register(state)
            self.streams.bind(state, member, 'reply' if self.direct else 'member')
            if self.goal:
                self.goal.register(state)
            self.members[name] = {'name': name, 'display_name': self.authors[member.name]['display_name'],
                'role': member.role, 'status': state.status, 'agent_id': state.id,
                'context_session_id': state.session_id, 'depends_on': [row._prompt_member for row in dependencies],
                **({'runner': member.runner} if member.runner else {})}
            if self.contexts:
                await self.contexts.started(state)
            await self.emit('execute')
            return state

    async def status(self, payload):
        """成员启动和结束由真实子代理状态驱动，不由模型文字推断。"""
        if self.closed or payload.get('parent_session_id') != self.session.id:
            return
        row = next((row for row in self.members.values() if row['agent_id'] == payload.get('agent_id')), None)
        if row is not None:
            row.update(status='done' if payload['status'] == 'completed' else payload['status'], error=payload.get('error'))
            await self.emit('execute')

    def lead_author(self):
        """主理人输出也使用冻结的身份，历史不依赖之后的选择。"""
        return {**self.authors[lead_member(self.team).name], 'stage': 'summary'}

    async def finished(self, member, state):
        """结束后先保存真实交付与上下文，再允许下一次派工。"""
        if self.workspaces and state.status == 'completed':
            try:
                await self.workspaces.finalize(member.name)
            except Exception as exc:
                state.status, state.error = 'failed', {'code': 'run/delivery', 'message': str(exc)}
        if self.contexts:
            await self.contexts.checkpoint(state)
        acceptance = evaluate_report(
            parse_report(state.output), state.status == 'completed', session=state.session)
        self.members[member.name].update(status='done' if state.status == 'completed' else state.status, error=state.error_message, acceptance=acceptance)
        partial = member_partial_reply(state.session, state.id) if state.status != 'completed' else None
        if partial:
            await publish_member_reply(self.session, self.harness.bus, author={**self.authors[member.name], **({'stage': 'reply'} if self.direct else {})},
                text=partial['text'], ok=False, state=partial, turn=self.session.current_turn)
        await publish_member_reply(self.session, self.harness.bus, author={**self.authors[member.name], **({'stage': 'reply'} if self.direct else {})},
            text=state.output, ok=state.status == 'completed',
            state={'context_session_id': state.session_id, 'acceptance': acceptance},
            turn=self.session.current_turn)
        if self.goal:
            await self.goal.sync()
        await self.streams.finish(state.id)
        await self.emit('execute')

    async def run_direct(self, task):
        """确定性提交指定成员，共用派工生命周期，不请求规划或汇总模型。"""
        from ..tools.registry import ToolRunContext
        from ..core.errors import ConfigError, AgentError
        slots = asyncio.Semaphore(4)
        waits = {}
        workers = [member for member in self.team.members if member.name in self.recipients]

        async def execute(member):
            dependencies = [name for name in member.depends_on if name in self.recipients]
            upstream = [await waits[name] for name in dependencies]
            failed = [name for name, state in zip(dependencies, upstream)
                      if state is None or state.status != 'completed']
            if failed:
                self.members[member.name] = {'name': member.name, 'display_name': self.authors[member.name]['display_name'],
                    'role': member.role, 'status': 'blocked', 'depends_on': dependencies,
                    'error': tr('agent.team.dependencies_failed', p0=', '.join(self.authors[name]['display_name'] for name in failed))}
                await self.emit('execute')
                return None
            async with slots:
                ctx = ToolRunContext(call_id='direct:' + member.name, session_id=self.session.id,
                    cwd=self.session.header.cwd, turn=self.session.current_turn, step=0)
                state = await self.spawn({'member': member.name, 'prompt': task}, ctx)
                await self.harness.subagents.wait(state.id, timeout=self.timeout)
                # 成员期限由管理器统一判定；观察超时不能改写其真实终态。
                while not state.settled:
                    await self.harness.subagents.wait(state.id, timeout=self.timeout)
                state._wait_consumed = True
                return state

        reason = 'error'
        try:
            self.bind_goal()
            self.parent.team_steering = self.steering
            self.parent.accepting_steering = True
            if not self.harness.has_model_credentials and any(not member.runner for member in workers):
                raise ConfigError(tr('python.harness.3abc241f3dfd'))
            await self.harness.ensure_started()
            await self.start()
            for member in workers:
                waits[member.name] = asyncio.create_task(execute(member))
            states = await asyncio.gather(*waits.values())
            verdict = acceptance_verdict(self.members, self.members, enforced=requires_acceptance(self.team.metadata))
            if not any(state is not None and state.status == 'completed' for state in states):
                raise AgentError(tr('agent.team.execution_failed'), agent=self.team.name)
            if verdict['enforced'] and not verdict['passed']:
                raise AgentError(tr('agent.team.acceptance_failed', p0=self.team.name,
                    p1=', '.join(verdict['failed'] + verdict['unverified'])), agent=self.team.name)
            reason = 'completed'
            outputs = [(member.name, state.output if state is not None and state.status == 'completed'
                else self.members.get(member.name, {}).get('error') or '')
                for member, state in zip(workers, states)]
            return outputs[0][1] if len(outputs) == 1 else '\n\n'.join(f'## {name}\n\n{text}' for name, text in outputs)
        except asyncio.CancelledError:
            reason = 'aborted'
            raise
        finally:
            for pending in waits.values():
                if not pending.done():
                    pending.cancel()
            async def cleanup():
                await asyncio.gather(*waits.values(), return_exceptions=True)
                await self.close(reason)
            await join_cleanup(asyncio.create_task(cleanup()))

    async def completion_feedback(self, final_text=None):
        self.bind_goal()
        lead = lead_member(self.team).name
        missing = [name for name in self.recipients if name != lead and name not in self.states]
        pending = [name for name, state in self.states.items() if not state.settled or not getattr(state, '_wait_consumed', False)]
        if missing or pending:
            return tr('agent.prompt_team.incomplete', p0=', '.join(missing + pending))
        if requires_acceptance(self.team.metadata):
            verdict = acceptance_verdict(self.states, self.members, enforced=True)
            invalid = verdict['failed'] + verdict['unverified']
            if invalid or not self.states:
                return tr('agent.prompt_team.unverified', p0=', '.join(invalid))
        if self.goal:
            await self.goal.sync()
        if final_text is not None:
            from ..agents.limits import reply_feedback
            return reply_feedback(final_text, self.summary_reply_limit)
        return None

    async def close(self, reason):
        """多个关闭等待者共用同一收尾，重复取消不截断成员回收。"""
        task = self._close_task
        if task is None:
            task = self._close_task = asyncio.create_task(self._close(reason))
        await join_cleanup(task)

    async def _close(self, reason):
        """回收全部本轮成员，汇总失败；不删除会话、工作树或产物。"""
        if self.closed:
            return
        errors = []
        try:
            for state in list(self.states.values()):
                try:
                    await self.harness.subagents.interrupt(state.id, reason=tr('feedback.196802f550a3'))
                    self.members[state._prompt_member].update(
                        status='done' if state.status == 'completed' else state.status, error=state.error_message)
                except BaseException as exc:
                    errors.append(exc)
            phase = 'failed' if errors else 'done' if reason == 'completed' else 'interrupted' if reason == 'aborted' else 'failed'
            try:
                await self.emit(phase)
            except BaseException as exc:
                errors.append(exc)
        finally:
            self.closed = True
            self.parent.tool_schema_transform = self.previous_schema_transform
            self.parent.isolated_team_coordinator = self.previous_coordinator
            self.parent.reply_max_chars = self.previous_reply_max_chars
            self.parent._pre_step_hooks.remove(self.step)
            self.harness.bus.off('subagent/status', self.status)
            self.harness._active_team_runs.discard((self.session.id, self.run_id))
            try:
                await self.streams.close()
            except BaseException as exc:
                errors.append(exc)
            finally:
                if self.steering:
                    self.steering.close()
                if self.goal:
                    self.goal.closed = True
                self.parent.team_goal = None
                self.parent.team_goal_writer = False
        raise_cleanup_errors(errors)
