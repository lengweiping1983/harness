"""主理人的显式只读声明进入可见工具与执行闸，结束后不污染普通会话。"""
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path
from unittest.mock import AsyncMock

from harness import Harness, HarnessConfig
from harness.agents.parser import AgentDefinition, AgentMember
from harness.app.prompt_team_run import PromptTeamRun
from harness.core.errors import HarnessError
from harness.llm.types import ToolSchema, ToolUseBlock
from harness.tools.registry import ToolDefinition, ToolRunContext
from tests.web_fixture import FixtureTransport


class TeamLeadAccessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        flags = {f.name: False for f in fields(HarnessConfig) if f.name.startswith('enable_')}
        flags.update(enable_agents=True, enable_subagent=True)
        self.app = Harness(HarnessConfig(api_key='fixture', home=root/'state', cwd=root,
            model='fixture', **flags), transport=FixtureTransport())
        self.addAsyncCleanup(self.app.close)
        self.session = self.app.create_session()
        self.parent = self.app._agent_for(self.session)
        self.executions = {}
        for name, effect in (('probe_read', 'read'), ('probe_execute', 'execute'),
                             ('probe_write', 'workspace_write'), ('probe_external', 'external_write'), ('probe_unknown', 'unknown'),
                             ('spawn_agent', 'unknown'), ('wait_agent', 'unknown'), ('wait_agents', 'unknown')):
            execute = AsyncMock(return_value='完成')
            self.executions[name] = execute
            self.parent.tools.register(ToolDefinition({'name': name}, execute, effect=effect), replace=True)

    async def start(self, access='read', **options):
        team = AgentDefinition('readonly-team', '只读协调', agent_type='team', members=(
            AgentMember('host', role='lead', prompt='协调', access=access),
            AgentMember('research', prompt='研究', access='read')))
        self.app.agents.register(team)
        await self.app.agent_mount.set(self.session, team.name)
        return PromptTeamRun(self.app, self.session, self.parent, team,
                             self.app.agent_mount.projection(self.session), **options)

    async def test_read_lead_hides_writes_and_execution_but_keeps_coordination(self):
        run = await self.start()
        self.addAsyncCleanup(run.close, 'completed')
        names = {schema.name for schema in self.parent._eligible_tool_schemas()}
        self.assertTrue({'probe_read', 'spawn_agent', 'wait_agent', 'wait_agents'} <= names)
        self.assertFalse({'probe_execute', 'probe_write', 'probe_external', 'probe_unknown'} & names)
        ctx = ToolRunContext(call_id='probe', session_id=self.session.id, cwd=self.directory.name,
            turn=1, step=1, services={'isolated_team_coordinator': lambda: self.parent.isolated_team_coordinator})
        for name in self.executions:
            result = await self.parent.tools.execute_call(ToolUseBlock(id='probe', name=name, input={}), ctx)
            denied = name in {'probe_execute', 'probe_write', 'probe_external', 'probe_unknown'}
            self.assertEqual(result.is_error, denied, name)
            self.assertEqual(self.executions[name].await_count, 0 if denied else 1, name)
        await run.close('completed')
        self.assertFalse(self.parent.isolated_team_coordinator)
        self.assertIn('probe_execute', {schema.name for schema in self.parent._eligible_tool_schemas()})

    async def test_inherit_does_not_restrict_normal_lead(self):
        run = await self.start('inherit')
        self.addAsyncCleanup(run.close, 'completed')
        self.assertFalse(self.parent.isolated_team_coordinator)

    async def test_existing_coordinator_restriction_survives_close(self):
        self.parent.isolated_team_coordinator = True
        run = await self.start()
        await run.close('completed')
        self.assertTrue(self.parent.isolated_team_coordinator)

    async def test_team_spawn_schema_exposes_only_callable_roster_and_restores(self):
        original = self.parent.tools.get('spawn_agent').schema.to_dict()
        other = self.app._agent_for(self.app.create_session())
        run = await self.start()
        self.addAsyncCleanup(run.close, 'completed')
        schema = next(s for s in self.parent._eligible_tool_schemas() if s.name == 'spawn_agent')
        self.assertEqual(schema.parameters['properties']['member']['enum'], ['research'])
        self.assertIn('member', schema.parameters['required'])
        self.assertEqual(self.parent.tools.get('spawn_agent').schema.to_dict(), original)
        peer = next(s for s in other._eligible_tool_schemas() if s.name == 'spawn_agent')
        self.assertNotIn('enum', peer.parameters.get('properties', {}).get('member', {}))
        await run.close('completed')
        restored = next(s for s in self.parent._eligible_tool_schemas() if s.name == 'spawn_agent')
        self.assertNotIn('enum', restored.parameters.get('properties', {}).get('member', {}))

    async def test_direct_team_can_address_its_lead(self):
        run = await self.start(direct=True, recipients=['host', 'research'])
        self.addAsyncCleanup(run.close, 'completed')
        schema = next(s for s in self.parent._eligible_tool_schemas() if s.name == 'spawn_agent')
        self.assertEqual(schema.parameters['properties']['member']['enum'], ['host', 'research'])

    async def test_existing_schema_projection_is_composed_and_restored(self):
        seen = []
        def previous(schemas):
            seen.append(tuple(schema.name for schema in schemas))
            return schemas
        self.parent.tool_schema_transform = previous
        run = await self.start()
        schema = next(s for s in self.parent._eligible_tool_schemas() if s.name == 'spawn_agent')
        self.assertEqual(schema.parameters['properties']['member']['enum'], ['research'])
        self.assertTrue(seen)
        await run.close('completed')
        self.assertIs(self.parent.tool_schema_transform, previous)

    async def test_roster_projection_preserves_prompt_and_unrelated_parameters(self):
        run = await self.start()
        self.addAsyncCleanup(run.close, 'completed')
        parameters = {'type': 'object', 'properties': {'prompt': {'type': 'string'},
            'member': {'type': 'string', 'description': '成员标识'},
            'dependencies': {'type': 'array', 'items': {'type': 'string'}}},
            'required': ['prompt'], 'additionalProperties': False}
        original = ToolSchema('spawn_agent', '派工', parameters)
        projected = run.tool_schemas([original])[0]
        self.assertEqual(projected.parameters['required'], ['prompt', 'member'])
        self.assertEqual(projected.parameters['properties']['prompt'], {'type': 'string'})
        self.assertEqual(projected.parameters['properties']['dependencies'], parameters['properties']['dependencies'])
        self.assertEqual(projected.parameters['properties']['member']['description'], '成员标识')
        self.assertFalse(projected.parameters['additionalProperties'])
        self.assertNotIn('enum', parameters['properties']['member'])
        self.assertEqual(parameters['required'], ['prompt'])

    async def test_invalid_context_initialization_preserves_parent_state_and_data(self):
        self.parent.reply_max_chars = 1800
        previous_projection = lambda schemas: schemas
        self.parent.tool_schema_transform = previous_projection
        hooks = tuple(self.parent._pre_step_hooks)
        invalid = await self.session.append('note', {
            'kind': 'collaboration/member-context', 'key': 'damaged-context'})
        with self.assertRaises(HarnessError) as failure:
            await self.start()
        self.assertEqual(failure.exception.code, 'team/context-invalid')
        self.assertEqual(self.parent.reply_max_chars, 1800)
        self.assertFalse(self.parent.isolated_team_coordinator)
        self.assertIs(self.parent.tool_schema_transform, previous_projection)
        self.assertEqual(tuple(self.parent._pre_step_hooks), hooks)
        self.assertFalse(self.app._active_team_runs)
        self.assertEqual(self.session.event_at(invalid.seq).data, invalid.data)
