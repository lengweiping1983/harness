"""验证会话配置隔离及成员等待期间的请求配置归属。"""
import json
import tempfile
import unittest
from dataclasses import fields, replace
from pathlib import Path
from types import SimpleNamespace

from harness import Harness, HarnessConfig
from harness.app.runtime import ExecutionBudget
from harness.llm.types import LlmCallConfig
from tests.agent_configuration_fixture import RecordingTransport


class AgentConfigurationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        flags = {item.name: False for item in fields(HarnessConfig) if item.name.startswith('enable_')}
        flags['enable_plan_mode'] = True
        self.transport = RecordingTransport()
        self.app = Harness(HarnessConfig(api_key='fixture-only', home=root/'state', cwd=root,
            model='initial-model', max_steps=2, max_retries=0, **flags), transport=self.transport)
        self.addAsyncCleanup(self.app.close)

    async def test_saved_default_reasoning_reaches_requests_and_preserves_overrides(self):
        from harness.core.language import current_language
        from harness.web.api.settings import build_router
        from harness.web.router import Request

        inherited = self.app.create_session()
        overridden = self.app.create_session(metadata={'reasoning_effort': 'low'})
        omitted = self.app.create_session(metadata={'reasoning_effort': ''})
        cached = self.app._agent_for(inherited)
        router = build_router(SimpleNamespace(harness=self.app))
        handler, _ = router.resolve('PATCH', '/api/v1/ui-settings')
        for language, effort in (('en', 'high'), ('zh-CN', None), ('en', 'medium')):
            with self.subTest(language=language, effort=effort), current_language.set(language):
                previous = cached.config
                response = handler(Request('PATCH', '/api/v1/ui-settings', {}, {},
                    json.dumps({'reasoning_effort': effort}).encode()))
                self.assertEqual(json.loads(response.body)['settings']['reasoning_effort'], effort)
                self.assertIs(cached.config, previous)
                for session, expected in ((self.app.create_session(), effort), (inherited, effort),
                                          (overridden, 'low'), (omitted, None)):
                    await self.app.run_async(session, 'Original 用户 {value}')
                    request = self.transport.requests[-1]
                    self.assertEqual(request.get('reasoning'), {'effort': expected} if expected else None)
                self.assertEqual(self.app.agent.config.reasoning_effort, effort)

    async def test_restored_default_reasoning_updates_request_template_before_first_run(self):
        from harness.web.api.settings import SettingsStore, build_router

        SettingsStore(self.app.config.home).update({'reasoning_effort': 'high'})
        self.assertIsNone(self.app.agent.config.reasoning_effort)
        build_router(SimpleNamespace(harness=self.app))
        await self.app.run_async(self.app.create_session(), 'Original first request')
        self.assertEqual(self.transport.requests[-1].get('reasoning'), {'effort': 'high'})

    async def test_cleared_model_override_follows_later_defaults_in_actual_requests(self):
        from harness.core.language import current_language
        from harness.web.api.sessions import build_router
        from harness.web.router import Request

        router = build_router(SimpleNamespace(harness=self.app))
        for index, language in enumerate(('en', 'zh-CN', 'en')):
            with self.subTest(language=language), current_language.set(language):
                original = self.app.agent.config.model
                restored = self.app.create_session(metadata={'model': original})
                pinned = self.app.create_session(metadata={'model': original})
                previous = self.app._agent_for(restored).config
                path = f'/api/v1/sessions/{restored.id}'
                handler, params = router.resolve('PATCH', path)
                response = handler(Request('PATCH', path, {}, {}, b'{"model":null}'), **params)
                self.assertEqual(json.loads(response.body)['session']['metadata']['model'], '')
                later = f'later-default-{index}'
                self.app.config.model = later
                self.app.agent.config = replace(self.app.agent.config, model=later)
                for session, expected in ((restored, later), (pinned, original)):
                    await self.app.run_async(session, 'Original 用户 {value}')
                    self.assertEqual(self.transport.requests[-1]['model'], expected)
                # 恢复继承不修改已经取得的不可变请求配置。
                self.assertEqual(previous.model, original)

    async def test_failed_default_reasoning_persistence_keeps_request_template(self):
        from unittest.mock import patch
        from harness.web.api.settings import SettingsStore, build_router
        from harness.web.errors import ServiceUnavailable
        from harness.web.router import Request

        router = build_router(SimpleNamespace(harness=self.app))
        handler, _ = router.resolve('PATCH', '/api/v1/ui-settings')
        previous = self.app.agent.config
        with patch.object(SettingsStore, '_persist', side_effect=OSError('Original failure')):
            with self.assertRaises(ServiceUnavailable) as caught:
                handler(Request('PATCH', '/api/v1/ui-settings', {}, {}, b'{"reasoning_effort":"high"}'))
        self.assertEqual(caught.exception.code, 'settings/persist-failed')
        self.assertIs(self.app.agent.config, previous)
        self.assertIsNone(self.app.config.reasoning_effort)

    async def test_repeat_reminder_is_owned_by_each_harness(self):
        from harness.core.agent import AgentLoop

        disabled = self.app._agent_for(self.app.create_session())
        standalone = AgentLoop(self.app.adapter, self.app.tools, self.app.prompts)
        flags = {item.name: False for item in fields(HarnessConfig) if item.name.startswith('enable_')}
        flags['enable_repeat_tool_reminder'] = True
        root = Path(self.directory.name)
        enabled = Harness(HarnessConfig(api_key='fixture-only', home=root/'enabled', cwd=root,
            repeat_tool_reminder_thresholds=[2, 3], **flags), transport=RecordingTransport())
        self.addAsyncCleanup(enabled.close)
        enabled_session = enabled.create_session()
        enabled_agent = enabled._agent_for(enabled_session)
        self.assertIsNone(disabled._reminder_for())
        self.assertIsNone(standalone._reminder_for())
        self.assertIsNone(self.app.agent._reminder_for())
        self.assertIs(enabled.agent._reminder_for(), enabled.repeat_reminder)
        self.assertIs(enabled_agent._reminder_for(), enabled.repeat_reminder)
        self.assertIsNone(enabled_agent._reminder_for().observe('same-id', 'tool', {}))
        self.assertIsNotNone(enabled_agent._reminder_for().observe('same-id', 'tool', {}))
        member = enabled.subagents.spawn(prompt='回复已核对', description='提醒器归属',
            parent_session_id=enabled_session.id)
        await enabled.subagents.wait(member.id, timeout=5)
        self.assertEqual(member.status, 'completed', member.error)
        self.assertIs(member.agent._reminder_for(), enabled.repeat_reminder)
        # 后续绑定关闭配置必须清除旧服务，不能继续使用原提醒器。
        enabled_agent.repeat_reminder = enabled.repeat_reminder
        self.app._bind_agent_services(enabled_agent, None)
        self.assertIsNone(enabled_agent._reminder_for())
        self.assertIs(enabled.agent._reminder_for(), enabled.repeat_reminder)

    async def test_queued_member_keeps_parent_request_settings_including_empty_effort(self):
        session = self.app.create_session()
        parent = self.app._agent_for(session)
        parent.config = LlmCallConfig(model='initial-model', reasoning_effort=None,
            max_tokens=321, temperature=0.2)
        member = self.app.subagents.spawn(prompt='回复配置已核对', description='冻结配置', parent_session_id=session.id)
        parent.config = replace(parent.config, model='later-model', reasoning_effort='high',
            max_tokens=999, temperature=0.8)
        await self.app.subagents.wait(member.id, timeout=5)
        self.assertEqual(member.status, 'completed', member.error)
        self.assertEqual(member.agent.config.model, 'initial-model')
        self.assertIsNone(member.agent.config.reasoning_effort)
        self.assertEqual(member.agent.config.max_tokens, 321)
        self.assertEqual(member.agent.config.temperature, 0.2)
        self.assertEqual(self.transport.requests[-1]['max_tokens'], 321)
        self.assertEqual(self.transport.requests[-1]['temperature'], 0.2)

    async def test_member_explicit_overrides_do_not_change_parent_config(self):
        session = self.app.create_session()
        parent = self.app._agent_for(session)
        original = parent.config
        member = self.app.subagents.spawn(prompt='回复配置已核对', description='成员覆盖',
            parent_session_id=session.id, model='member-model', reasoning_effort='low')
        await self.app.subagents.wait(member.id, timeout=5)
        self.assertEqual(member.status, 'completed', member.error)
        self.assertEqual(member.agent.config.model, 'member-model')
        self.assertEqual(member.agent.config.reasoning_effort, 'low')
        self.assertEqual(parent.config, original)

    async def test_member_without_active_parent_keeps_resolved_defaults(self):
        session = self.app.create_session()
        original = LlmCallConfig(model='default-at-dispatch', max_tokens=256, temperature=0.3)
        self.app.agent.config = original
        member = self.app.subagents.spawn(prompt='回复配置已核对', description='未启动父循环', parent_session_id=session.id)
        self.app.agent.config = replace(original, model='default-later', max_tokens=768, temperature=0.9)
        await self.app.subagents.wait(member.id, timeout=5)
        self.assertEqual(member.status, 'completed', member.error)
        self.assertEqual(member.agent.config, original)
        self.assertEqual(self.transport.requests[-1]['max_tokens'], 256)

    async def test_frozen_request_config_keeps_live_parent_safety_constraints(self):
        session = self.app.create_session()
        parent = self.app._agent_for(session)
        parent.task_read_only = True
        parent.recovery_read_only = True
        parent.budget = ExecutionBudget(100_000)
        parent.max_steps = 1
        await self.app.set_plan_mode(session, True)
        member = self.app.subagents.spawn(prompt='只核对，不改文件', description='继承约束', parent_session_id=session.id)
        await self.app.subagents.wait(member.id, timeout=5)
        self.assertEqual(member.status, 'completed', member.error)
        self.assertTrue(member.agent.task_read_only)
        self.assertTrue(member.agent.recovery_read_only)
        self.assertTrue(member.read_only)
        self.assertIs(member.agent.budget, parent.budget)
        self.assertEqual(member.agent.max_steps, 1)
        self.assertTrue(self.app.plan.is_active(member.session))

    async def test_main_and_member_share_services_without_sharing_execution_identity(self):
        session = self.app.create_session()
        parent = self.app._agent_for(session)
        member = self.app.subagents.spawn(prompt='只回复核对结果', description='服务绑定', parent_session_id=session.id)
        await self.app.subagents.wait(member.id, timeout=5)
        self.assertEqual(member.status, 'completed', member.error)
        child = member.agent
        for agent in (parent, child):
            self.assertEqual(agent.skills_for_session, self.app.skills_for)
            self.assertEqual(agent.context_control, self.app.runtime.process_compaction)
            self.assertEqual(agent.context_policy, self.app._context_policy)
            self.assertIs(agent.identity, self.app.identity)
            self.assertEqual(agent.summary_max_tokens, self.app.config.summary_max_tokens)
        self.assertEqual(child.run_id, member.id)
        self.assertIsNone(parent.run_id)
        self.assertIsNot(child.tool_discovery, parent.tool_discovery)
        self.app.config.tool_schema_mode = 'all'
        self.assertEqual(parent.tool_schema_policy(), 'all')
        self.assertEqual(child.tool_schema_policy(), 'all')
        self.assertEqual(len(parent._pre_step_hooks), 1)
        self.assertEqual(len(child._pre_step_hooks), 1)

    async def test_session_loop_is_reused_without_duplicating_plan_hooks(self):
        first, second = self.app.create_session(), self.app.create_session()
        original = self.app._agent_for(first)
        hooks = tuple(original._pre_step_hooks)
        self.assertIs(self.app._agent_for(first), original)
        self.assertEqual(tuple(original._pre_step_hooks), hooks)
        self.assertIsNot(self.app._agent_for(second), original)
        original.cancel('第一会话已停止')
        self.assertFalse(self.app._agent_for(second).cancelled)

    async def test_refresh_session_config_propagates_read_failures_without_changing_agent(self):
        from unittest.mock import patch
        from harness.core.errors import PersistenceError
        from harness.core.language import current_language
        session = self.app.create_session()
        agent = self.app._agent_for(session)
        original = agent.config
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                for error in (OSError('Original read failure'), PersistenceError('Original storage failure')):
                    with patch.object(self.app.sessions, 'get', side_effect=error):
                        with self.assertRaises(type(error)) as caught:
                            self.app.refresh_session_config(session.id)
                    self.assertIs(caught.exception, error)
                    self.assertIs(agent.config, original)
        self.app.sessions.update_header(session.id, metadata={'model':'updated-model'})
        self.app.refresh_session_config(session.id)
        self.assertEqual(agent.config.model, 'updated-model')

    async def test_refresh_session_config_keeps_missing_and_uninitialized_sessions_harmless(self):
        from unittest.mock import patch
        from harness.core.errors import SessionNotFoundError
        session = self.app.create_session()
        with patch.object(self.app.sessions, 'get') as get:
            self.app.refresh_session_config(session.id)
            get.assert_not_called()
        agent = self.app._agent_for(session)
        original = agent.config
        with patch.object(self.app.sessions, 'get', side_effect=SessionNotFoundError(session.id)):
            self.app.refresh_session_config(session.id)
        self.assertIs(agent.config, original)

    async def test_queued_member_keeps_language_and_request_snapshot_after_parent_changes(self):
        import asyncio
        from harness.core.language import current_language
        for language, later in (('en','zh-CN'),('zh-CN','en'),('en','zh-CN')):
            with self.subTest(language=language):
                session = self.app.create_session()
                parent = self.app._agent_for(session)
                parent.language = language
                parent.config = replace(parent.config,model='snapshot-model',max_tokens=321)
                from harness.subagent.admission import AdmissionGate
                self.app.subagents._gate = AdmissionGate(capacity=1)
                blocker = await self.app.subagents._gate.acquire('fixture-blocker', 1)
                # 占满全树名额，明确验证排队期间设置已发生改变。
                try:
                    with current_language.set(language):
                        member = self.app.subagents.spawn(prompt='Original task {literal}',description='Snapshot',parent_session_id=session.id)
                    await asyncio.sleep(0)
                    self.assertIsNone(member.agent)
                    parent.language = later
                    parent.config = replace(parent.config,model='later-model',max_tokens=999)
                    self.app.config.language = later
                finally:
                    self.app.subagents._gate.release(blocker)
                await self.app.subagents.wait(member.id,timeout=5)
                self.assertEqual(member.status,'completed',member.error)
                self.assertEqual(member.language,language)
                self.assertEqual(member.agent.language,language)
                self.assertEqual(member.agent.language_preference(),language)
                self.assertEqual(member.agent.config.model,'snapshot-model')
                self.assertEqual(member.agent.config.max_tokens,321)
                request = self.transport.requests[-1]
                self.assertEqual(request['model'],'snapshot-model')
                self.assertEqual(request['max_tokens'],321)
                starts = [event for event in member.session.events if event.type=='turn/start']
                self.assertEqual(starts[0].data['language'],language)
                self.assertIn('Original task {literal}',str(request['messages']))
