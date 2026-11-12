"""验证Agent技能选择的持久化、目录、工具加载及团队执行隔离。"""
import json
import tempfile
import unittest
from dataclasses import fields, replace
from pathlib import Path
from unittest.mock import patch

from harness import Harness, HarnessConfig
from harness.agents.authoring import create, markdown
from harness.agents.editing import snapshot, update
from harness.agents.parser import AgentDefinition, AgentMember, parse_agent_text
from harness.agents.registry import AgentRegistry
from harness.app.prompt_team_run import PromptTeamRun
from harness.collaboration.member_context import TeamContexts
from harness.core.errors import AgentParseError
from harness.core.language import current_language
from harness.i18n import tr
from harness.skills.parser import SkillMetadata
from harness.skills.tool import build_skill_tool
from harness.tools.registry import ToolRunContext
from tests.agent_configuration_fixture import RecordingTransport


class AgentSkillDeclarationTests(unittest.TestCase):
    def test_create_edit_export_and_import_preserve_selection_and_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = AgentRegistry()
            payload = {'name': 'writer', 'description': '编写内容', 'skills': ['copywriting', 'sample:review']}
            agent = create(Path(directory), payload, registry)
            self.assertEqual(parse_agent_text(markdown(agent)).skills, ('copywriting', 'sample:review'))
            version = snapshot(agent.path, source='user')['revision']
            result = update(agent.path, {**payload, 'skills': []}, registry, expected_revision=version)
            self.assertEqual(result['agent']['skills'], [])
            self.assertEqual(parse_agent_text(agent.path.read_text('utf-8')).skills, ())
            self.assertIsNone(parse_agent_text(markdown(replace(agent, skills=None))).skills)

    def test_invalid_skill_lists_are_rejected_without_overwriting(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = AgentRegistry()
            payload = {'name': 'writer', 'description': '写作', 'skills': ['writing']}
            agent = create(Path(directory), payload, registry)
            original = agent.path.read_bytes()
            revision = snapshot(agent.path, source='user')['revision']
            for value in ('writer', [1], ['a', 'a'], ['../a'], ['a:b:c'], [''], [{}], [True]):
                with self.subTest(value=value):
                    with self.assertRaises(AgentParseError):
                        update(agent.path, {**payload, 'skills': value}, registry, expected_revision=revision)
                    self.assertEqual(agent.path.read_bytes(), original)

    def test_external_runner_rejects_skill_selection(self):
        for runner in ('codex', 'claude', 'opencode'):
            with self.subTest(runner=runner):
                agent = AgentDefinition('external', '外部执行器', metadata={'runner': runner}, skills=('writing',))
                with self.assertRaises(AgentParseError):
                    parse_agent_text(markdown(agent))
                self.assertIsNone(parse_agent_text(markdown(replace(agent, skills=None))).skills)
                team = AgentDefinition('team', '团队', agent_type='team', members=(
                    AgentMember('lead', role='lead', prompt='协调'),
                    AgentMember('external', runner=runner, skills=('writing',))))
                with self.assertRaises(AgentParseError):
                    parse_agent_text(markdown(team))

    def test_referenced_external_runner_rejects_member_skills(self):
        from harness.agents.execution import resolve_team_execution
        from harness.core.errors import AgentError
        registry = AgentRegistry()
        registry.register(AgentDefinition('external', '外部执行器', metadata={'runner': 'codex'}))
        team = AgentDefinition('team', '团队', agent_type='team', members=(
            AgentMember('lead', role='lead', prompt='协调'),
            AgentMember('worker', agent='external', skills=('writing',))))
        with self.assertRaises(AgentError):
            resolve_team_execution(team, registry)
        model_member = replace(team.members[1], execution='model')
        resolved = resolve_team_execution(replace(team, members=(team.members[0], model_member)), registry)
        self.assertIsNone(resolved.members[1].runner)

    def test_team_and_member_skill_fields_round_trip(self):
        team = AgentDefinition('team', '团队', agent_type='team', skills=('common',), members=(
            AgentMember('lead', role='lead', prompt='主理', skills=()),
            AgentMember('writer', prompt='写作', skills=('writer',))))
        parsed = parse_agent_text(markdown(team))
        self.assertEqual(parsed.skills, ('common',))
        self.assertEqual(parsed.members[0].skills, ())
        self.assertEqual(parsed.members[1].skills, ('writer',))


class AgentSkillRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        flags = {item.name: False for item in fields(HarnessConfig) if item.name.startswith('enable_')}
        flags.update(enable_agents=True, enable_skills=True)
        self.transport = RecordingTransport()
        self.app = Harness(HarnessConfig(api_key='fixture', home=self.root/'state', cwd=self.root,
            max_steps=2, max_retries=0, language='zh-CN', **flags), transport=self.transport)
        self.addAsyncCleanup(self.app.close)
        for name in ('default-skill', 'writer-skill', 'common-skill'):
            self.app.add_skill(SkillMetadata(name=name, description=name, content='正文 ' + name))
        self.app.set_skill_enabled('writer-skill', False)
        self.writer = AgentDefinition('writer', '写作', system_prompt='执行写作任务', skills=('writer-skill',))
        self.app.agents.register(self.writer)

    def catalog_line(self, name, description=None):
        """中文模型请求使用完整目录行，保留技能选择的正反断言。"""
        with current_language.set('zh-CN'):
            return tr('catalog.skill.single', name=name, description=description or name)

    def names(self, session):
        return [skill.name for skill in self.app.skills_for(session).list()]

    def context(self, session):
        return ToolRunContext('test', session.id, session.header.cwd, 0, 0, services={'session': session})

    async def test_independent_selection_loads_globally_disabled_skill_and_blocks_others(self):
        session, default = self.app.create_session(), self.app.create_session()
        await self.app.agent_mount.set(session, 'writer')
        self.assertEqual(self.names(session), ['writer-skill'])
        self.assertIn('default-skill', self.names(default))
        self.assertNotIn('writer-skill', self.names(default))
        load = build_skill_tool(self.app.skills, registry_for_session=self.app.skills_for)[0].execute
        self.assertFalse((await load({'name': 'writer-skill'}, self.context(session))).is_error)
        self.assertTrue((await load({'name': 'default-skill'}, self.context(session))).is_error)
        self.assertFalse(self.app.esc_state.is_enabled('skill', 'writer-skill'))
        await self.app.agent_mount.set(session, None)
        self.assertIn('default-skill', self.names(session))
        self.assertNotIn('writer-skill', self.names(session))

    async def test_explicit_empty_differs_from_inherit_and_management_sees_all(self):
        session = self.app.create_session()
        self.app.agents.register(replace(self.writer, skills=(), rank=99))
        await self.app.agent_mount.set(session, 'writer')
        self.assertEqual(self.names(session), [])
        self.assertIn('writer-skill', [skill.name for skill in self.app.skills_for(session, configured=False).list_all()])
        self.app.agents.register(replace(self.writer, skills=None, rank=98))
        self.assertIn('default-skill', self.names(session))

    async def test_saved_inheritance_follows_new_skills_and_default_changes(self):
        """保存继承标记后，新技能和默认启停在后续回合动态生效。"""
        root = self.root / 'definitions'
        agent = create(root, {'name': 'inherited-writer', 'skills': ['writer-skill']}, self.app.agents)
        version = snapshot(agent.path, source='user')['revision']
        result = update(agent.path, {'name': agent.name, 'skills': None}, self.app.agents, expected_revision=version)
        self.assertIsNone(result['agent']['skills'])
        original = agent.path.read_bytes()
        self.assertNotIn('"skills"', original.decode('utf-8'))
        inherited = parse_agent_text(original.decode('utf-8'))
        self.app.agents.register(inherited)
        session = self.app.create_session()
        await self.app.agent_mount.set(session, inherited.name)
        await self.app.run_async(session, '核对初始默认技能')
        initial = json.dumps(self.transport.requests[-1], ensure_ascii=False)
        self.assertIn(self.catalog_line('default-skill'), initial)
        self.assertNotIn(self.catalog_line('writer-skill'), initial)
        self.app.add_skill(SkillMetadata(name='new-default-skill', description='新增默认技能', content='新增技能正文'))
        self.app.set_skill_enabled('writer-skill', True)
        self.app.set_skill_enabled('default-skill', False)
        await self.app.run_async(session, '核对更新后的默认技能')
        systems = [message for message in self.transport.requests[-1]['messages'] if message['role'] == 'system']
        current = json.dumps(systems, ensure_ascii=False)
        self.assertIn(self.catalog_line('new-default-skill', '新增默认技能'), current)
        self.assertIn(self.catalog_line('writer-skill'), current)
        self.assertNotIn(self.catalog_line('default-skill'), current)
        self.assertEqual(agent.path.read_bytes(), original)
        self.assertIsNone(parse_agent_text(original.decode('utf-8')).skills)

    async def test_once_selection_and_restored_mount_use_correct_skills(self):
        session = self.app.create_session()
        with self.app.agent_mount.selection_scope():
            self.app.agent_mount.select_once(session.id, self.writer)
            self.assertEqual(self.names(session), ['writer-skill'])
        self.assertNotIn('writer-skill', self.names(session))
        await self.app.agent_mount.set(session, 'writer')
        restored = self.app.sessions.reopen(session.id)
        self.assertEqual(self.names(restored), ['writer-skill'])

    async def test_model_request_catalog_matches_selected_skills(self):
        session = self.app.create_session()
        await self.app.agent_mount.set(session, 'writer')
        await self.app._agent_for(session).prompt(session, '核对技能目录')
        text = json.dumps(self.transport.requests[-1], ensure_ascii=False)
        self.assertIn(self.catalog_line('writer-skill'), text)
        self.assertNotIn(self.catalog_line('default-skill'), text)

    async def test_saving_selection_during_tool_call_takes_effect_next_turn(self):
        session = self.app.create_session()
        await self.app.agent_mount.set(session, 'writer')
        original_stream = self.transport.post_stream
        changed = False

        async def stream(url, *, headers, body, timeout):
            nonlocal changed
            if not changed:
                changed = True
                self.transport.requests.append(json.loads(body))
                self.app.agents.register(replace(self.writer, skills=(), rank=99))
                delta = {'tool_calls': [{'index': 0, 'id': 'load-selected-skill', 'type': 'function',
                    'function': {'name': 'skill', 'arguments': '{"name":"writer-skill"}'}}]}
                for chunk in [
                    {'choices': [{'delta': delta, 'finish_reason': None}]},
                    {'choices': [{'delta': {}, 'finish_reason': 'tool_calls'}]},
                ]:
                    yield ('data: ' + json.dumps(chunk) + '\n\n').encode()
                yield b'data: [DONE]\n\n'
            else:
                async for chunk in original_stream(url, headers=headers, body=body, timeout=timeout):
                    yield chunk

        self.transport.post_stream = stream
        result = await self.app.run_async(session, '加载选中的技能')
        self.assertIsNone(result.error)
        messages = self.transport.requests[-1]['messages']
        self.assertIn(self.catalog_line('writer-skill'), json.dumps(messages, ensure_ascii=False))
        loaded = next(message for message in messages if message['role'] == 'tool')
        self.assertIn('正文 writer-skill', str(loaded['content']))
        self.assertEqual(self.names(session), [])
        await self.app.run_async(session, '核对下一回合')
        systems = [message for message in self.transport.requests[-1]['messages'] if message['role'] == 'system']
        self.assertNotIn(self.catalog_line('writer-skill'), json.dumps(systems, ensure_ascii=False))

    async def test_plugin_disable_remains_effective_and_project_resolution_is_preserved(self):
        self.app.add_skill(SkillMetadata(name='sample:review', plugin_id='sample', description='审查', content='检查'))
        session = self.app.create_session()
        self.app.agents.register(replace(self.writer, skills=('sample:review', 'writer-skill')))
        await self.app.agent_mount.set(session, 'writer')
        with patch.object(self.app.packages, 'is_enabled', return_value=False):
            self.assertEqual(self.names(session), ['writer-skill'])
        path = self.root/'.harness'/'skills'/'writer-skill'/'SKILL.md'
        path.parent.mkdir(parents=True)
        path.write_text('---\nname: writer-skill\ndescription: 项目技能\n---\n项目正文', encoding='utf-8')
        self.assertEqual(self.app.skills_for(session).get('writer-skill').content, '项目正文')

    async def test_team_lead_reference_common_and_member_overrides(self):
        session = self.app.create_session()
        team = AgentDefinition('team', '团队', agent_type='team', skills=('common-skill',), members=(
            AgentMember('lead', role='lead', prompt='主理'),
            AgentMember('writer', agent='writer'),
            AgentMember('empty', prompt='无技能', skills=())))
        self.app.agents.register(team)
        await self.app.agent_mount.set(session, 'team')
        parent = self.app._agent_for(session)
        run = PromptTeamRun(self.app, session, parent, team, self.app.agent_mount.projection(session))
        self.app._prompt_team_runs[session.id] = run
        self.assertEqual(self.names(session), ['common-skill'])
        writer = await run.spawn({'member': 'writer', 'prompt': '核对'}, self.context(session))
        empty = await run.spawn({'member': 'empty', 'prompt': '核对'}, self.context(session))
        for state in (writer, empty):
            await self.app.subagents.wait(state.id, timeout=10)
            self.assertEqual(state.status, 'completed', state.error)
        self.assertEqual(self.names(writer.session), ['writer-skill'])
        self.assertEqual(self.names(empty.session), [])
        self.assertEqual(writer.prompt_input['skills'], ['writer-skill'])
        self.assertEqual(self.names(session), ['common-skill'])

    async def test_named_subagent_and_followup_keep_selection(self):
        session = self.app.create_session()
        state = await self.app._spawn_configured_subagent({'agent': 'writer', 'prompt': '核对'}, self.context(session))
        await self.app.subagents.wait(state.id, timeout=10)
        self.assertEqual(self.names(state.session), ['writer-skill'])
        next_state = await self.app._followup_subagent(state, '继续核对', {}, self.context(session))
        await self.app.subagents.wait(next_state.id, timeout=10)
        self.assertEqual(next_state.session_id, state.session_id)
        self.assertEqual(self.names(next_state.session), ['writer-skill'])
        self.assertEqual(next_state.prompt_input['skills'], ['writer-skill'])
        self.assertEqual(self.app.subagents.archive.load(next_state.id).prompt_input['skills'], ['writer-skill'])

    async def test_member_history_key_changes_with_skill_configuration(self):
        session = self.app.create_session()
        member = AgentMember('writer', agent='writer')
        team = AgentDefinition('team', '团队', agent_type='team', members=(member,))
        def key(definition):
            return TeamContexts(session, definition, self.app.agents).prepare(member, '任务', None, None, False)['record']['key']
        original = key(team)
        self.app.agents.register(replace(self.writer, skills=(), rank=99))
        self.assertNotEqual(key(team), original)
        self.assertNotEqual(key(team), key(replace(team, skills=('common-skill',))))


if __name__ == '__main__':
    unittest.main()
