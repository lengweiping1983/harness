"""跨回合续接原成员保留宿主配置复核和会话隔离。"""
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from harness import Harness
from harness.core.runs import AgentRun, COMPLETED, RUNNING
from harness.subagent.manager import SubagentManager


class TeamFollowupTests(unittest.IsolatedAsyncioTestCase):
    def setup_manager(self, *, parent='parent', status=COMPLETED):
        manager = SubagentManager(adapter=None, tools=None, prompts=None, contexts=SimpleNamespace())
        manager.scope_resolver = lambda _: 'prompt:new-run'
        old = AgentRun(id='old', session_id='private-context', parent_session_id=parent,
            root_session_id=parent, scope='prompt:old-run', status=status,
            context_persisted=True, prompt_input={'member': 'research'},
            session=SimpleNamespace(agent_messages=SimpleNamespace(receipt=lambda _: None)))
        manager._states[old.id] = old
        return manager, old

    async def test_original_parent_can_resume_settled_member_in_new_turn(self):
        manager, old = self.setup_manager()
        new = AgentRun(id='new', session_id=old.session_id, parent_session_id='parent')
        start = AsyncMock(return_value=new)
        result = await manager.followup_task(old.id, '重新读取当前文件', sender_session_id='parent',
            call_id='call', turn=2, start=start)
        self.assertTrue(result['started'])
        self.assertEqual(result['run']['child_session_id'], old.session_id)
        self.assertIs(start.call_args.args[0], old)
        self.assertEqual(start.call_args.args[2]['followup']['source_run_id'], old.id)
        # 续接特例不放宽普通观察与消息投递作用域。
        self.assertFalse(manager.visible_to(old, 'parent'))

    async def test_other_parent_cannot_resume_member(self):
        manager, old = self.setup_manager(parent='other')
        start = AsyncMock()
        with self.assertRaises(KeyError):
            await manager.followup_task(old.id, '任务', sender_session_id='parent', call_id='call', turn=2, start=start)
        start.assert_not_awaited()

    async def test_active_old_scope_cannot_be_resumed(self):
        manager, old = self.setup_manager(status=RUNNING)
        with self.assertRaises(KeyError):
            await manager.followup_task(old.id, '任务', sender_session_id='parent', call_id='call', turn=2, start=AsyncMock())

    async def test_without_configured_start_scope_is_not_bypassed(self):
        manager, old = self.setup_manager()
        with self.assertRaises(KeyError):
            await manager.followup_task(old.id, '任务', sender_session_id='parent', call_id='call', turn=2)

    async def test_team_start_requires_original_context_match(self):
        manager, old = self.setup_manager()
        spawn = AsyncMock(side_effect=ValueError('agent/context-changed'))
        run = SimpleNamespace(scope='prompt:new-run', states={}, spawn=spawn)
        host = SimpleNamespace(_prompt_team_runs={'parent': run})
        ctx = SimpleNamespace(session_id='parent')
        with self.assertRaisesRegex(ValueError, 'context-changed'):
            await Harness._followup_subagent(host, old, '重新读取', {}, ctx)
        args = spawn.call_args.args[0]
        self.assertEqual(args['_resume_context'], old.session_id)
        self.assertNotIn('model', args)

    async def test_missing_team_rejects_historical_resume(self):
        _, old = self.setup_manager()
        with self.assertRaisesRegex(ValueError, 'team-followup-unavailable'):
            await Harness._followup_subagent(SimpleNamespace(_prompt_team_runs={}), old, '任务', {},
                SimpleNamespace(session_id='parent'))

    async def test_same_turn_keeps_explicit_model_override(self):
        _, old = self.setup_manager()
        old.model, old.reasoning_effort = 'member-model', 'low'
        spawn = AsyncMock(return_value=old)
        run = SimpleNamespace(scope=old.scope, states={'research': old}, spawn=spawn)
        await Harness._followup_subagent(SimpleNamespace(_prompt_team_runs={'parent': run}),
            old, '任务', {}, SimpleNamespace(session_id='parent'))
        self.assertEqual(spawn.call_args.args[0]['model'], 'member-model')
        self.assertEqual(spawn.call_args.args[0]['reasoning_effort'], 'low')

    async def test_context_signature_changes_with_identity_permissions_and_model(self):
        from dataclasses import replace
        from harness.agents.parser import AgentDefinition, AgentMember
        from harness.agents.registry import AgentRegistry
        from harness.collaboration.member_context import TeamContexts
        member = AgentMember(name='research', prompt='核验', access='read')
        team = AgentDefinition(name='review', description='测试团队', agent_type='team', members=(member,))
        session = SimpleNamespace(id='parent', header=SimpleNamespace(cwd='/project'),
            event_count=0, surface=[], events_of_type=lambda _: [], latest=lambda _: None)
        contexts = TeamContexts(session, team, AgentRegistry())
        first = contexts.prepare(member, '初次任务', None, 'model', True)
        contexts.records[first['record']['key']] = {**first['record'], 'context_session_id': 'private', 'cursor': -1}
        self.assertEqual(contexts.prepare(member, '新任务', None, 'model', True)['context_session_id'], 'private')
        for changed_member, workspace, model, read_only in (
                (replace(member, prompt='新身份'), None, 'model', True),
                (member, '/other-project', 'model', True),
                (member, None, 'other-model', True),
                (member, None, 'model', False)):
            with self.subTest(model=model, workspace=workspace, read_only=read_only):
                self.assertIsNone(contexts.prepare(changed_member, '新任务', workspace, model, read_only)['context_session_id'])
