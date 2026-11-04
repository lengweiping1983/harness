"""显式 schema 说明使用资源身份，不依赖原文匹配且不泄漏到模型协议。"""
import unittest
from unittest.mock import patch

from harness.core.language import current_language
from harness.i18n import resources
from harness.tools.builtin.tool_result_tools import build_tool_result_reader


class SchemaResourceIdentityTests(unittest.TestCase):
    def test_same_registered_reader_resolves_explicit_key_without_reverse_translation(self):
        with current_language.set('zh-CN'):
            tool = build_tool_result_reader()
        original = tool.schema.to_dict()
        for language in ('en', 'zh-CN', 'en'):
            with patch('harness.i18n.localize_description', side_effect=AssertionError('reverse lookup'), create=True):
                schema = (tool.schema).resolve_description(language)
            self.assertEqual(schema.description, resources(language)['tool.result.reader_description'])
            self.assertEqual(schema.parameters, tool.schema.parameters)
            self.assertEqual(set(schema.to_dict()), {'name', 'description', 'parameters'})
            self.assertEqual(schema.name, 'read_tool_result')
        self.assertEqual(tool.schema.to_dict(), original)

    def test_explicit_resource_does_not_rewrite_parameter_names_or_original_examples(self):
        from harness.llm.types import ToolSchema
        original = resources('zh-CN')['policy.edit']
        schema = ToolSchema(name='OriginalTool', description=original,
            description_key='tool.result.reader_description', parameters={'type': 'object', 'properties': {
                'OriginalParameter': {'type': 'string', 'description': original, 'enum': [original], 'default': original}}})
        with patch('harness.i18n.localize_description', side_effect=AssertionError('reverse lookup'), create=True):
            resolved = (schema).resolve_description('en')
        self.assertEqual(resolved.parameters, schema.parameters)
        self.assertEqual(resolved.parameters['properties']['OriginalParameter']['description'], original)
        self.assertEqual(resolved.description, resources('en')['tool.result.reader_description'])

    def test_registered_skill_resolves_description_and_exact_parameter_path(self):
        from types import SimpleNamespace
        from harness.skills.tool import build_skill_tool
        with current_language.set('zh-CN'):
            tool = build_skill_tool(SimpleNamespace())[0]
        original = tool.schema.to_dict()
        for language in ('en', 'zh-CN', 'en'):
            with patch('harness.i18n.localize_description', side_effect=AssertionError('reverse lookup'), create=True):
                schema = (tool.schema).resolve_description(language)
            self.assertEqual(schema.description, resources(language)['python.tool.24742fca6c8d'])
            self.assertEqual(schema.parameters['properties']['name']['description'], resources(language)['python.tool.4ac8d795426b'])
            self.assertEqual(schema.parameters['required'], ['name'])
            self.assertEqual(set(schema.to_dict()), {'name', 'description', 'parameters'})
            schema.parameters['properties']['name']['description'] = 'caller change'
        self.assertEqual(tool.schema.to_dict(), original)

    def test_parameter_paths_preserve_literals_and_support_nested_array_schema(self):
        from harness.llm.types import ToolSchema
        original = resources('zh-CN')['python.tool.4ac8d795426b']
        schema = ToolSchema(name='OriginalTool', description=original, parameters={
            'anyOf': [{'type': 'string', 'description': original, 'enum': [original], 'default': original, 'examples': [original]}]},
            parameter_keys={('anyOf', 0, 'description'): 'python.tool.4ac8d795426b'})
        result = (schema).resolve_description('en')
        self.assertEqual(result.description, original)
        self.assertEqual(result.parameters['anyOf'][0]['description'], resources('en')['python.tool.4ac8d795426b'])
        for field in ('enum', 'default', 'examples'):
            self.assertEqual(result.parameters['anyOf'][0][field], schema.parameters['anyOf'][0][field])
        from dataclasses import replace
        for path in ((), ('anyOf', 0, 'default'), ('anyOf', 0, 'enum')):
            with self.subTest(path=path), self.assertRaises(ValueError):
                (replace(schema, parameter_keys={path: 'python.tool.4ac8d795426b'})).resolve_description('en')
        with self.assertRaises(KeyError):
            (replace(schema, parameter_keys={('missing', 'description'): 'python.tool.4ac8d795426b'})).resolve_description('en')

    def test_images_questions_and_jobs_resolve_same_instances_with_template_limits(self):
        from harness.tools.builtin.image_tools import build_image_tools
        from harness.tools.builtin.interaction_tools import build_ask_user_question_tools
        from harness.tools.builtin.job_tools import build_job_tools
        from types import SimpleNamespace
        def build():
            return build_image_tools(None) + build_ask_user_question_tools(SimpleNamespace()) + build_job_tools(SimpleNamespace())
        with current_language.set('zh-CN'):
            registered = build()
        originals = [tool.schema.to_dict() for tool in registered]
        self.assertEqual(len(registered), 6)
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                expected = {tool.schema.name: tool.schema.to_dict() for tool in build()}
            for tool in registered:
                with patch('harness.i18n.localize_description', side_effect=AssertionError('reverse lookup'), create=True):
                    resolved = (tool.schema).resolve_description(language)
                self.assertEqual(resolved.to_dict(), expected[tool.schema.name])
                self.assertIsNotNone(resolved.description_key)
        self.assertEqual([tool.schema.to_dict() for tool in registered], originals)

    def test_file_web_execution_and_presentation_schemas_match_fresh_language_builds(self):
        from harness.tools.builtin.fs_tools import build_fs_tools
        from harness.tools.builtin.web_tools import build_web_tools
        from harness.tools.builtin.shell_tools import build_shell_tools
        from harness.tools.builtin.pwsh_tools import build_pwsh_tools
        from harness.tools.builtin.code_tools import build_code_tools
        from harness.tools.builtin.lsp_tools import build_lsp_tools
        from harness.tools.builtin.workspace_tools import build_workspace_dependency_tools
        from harness.tools.builtin.present_tools import build_present_tools
        def build(background=False):
            from types import SimpleNamespace
            jobs = SimpleNamespace() if background else None
            return (build_fs_tools(None) + build_web_tools(timeout=17) + build_shell_tools(timeout=19, registry=jobs)
                    + build_pwsh_tools(timeout=23, registry=jobs) + build_code_tools(None, timeout=29)
                    + build_lsp_tools(None, timeout=31) + build_workspace_dependency_tools() + build_present_tools())
        with current_language.set('zh-CN'):
            registered = build()
        originals = [tool.schema.to_dict() for tool in registered]
        self.assertEqual(len(registered), 13)
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                expected = {tool.schema.name: tool.schema.to_dict() for tool in build()}
            for tool in registered:
                with self.subTest(tool=tool.schema.name, language=language), patch(
                        'harness.i18n.localize_description', side_effect=AssertionError('reverse lookup'), create=True):
                    self.assertEqual((tool.schema).resolve_description(language).to_dict(), expected[tool.schema.name])
        self.assertEqual([tool.schema.to_dict() for tool in registered], originals)

    def test_execution_background_parameter_matrix_uses_explicit_resources(self):
        from harness.tools.builtin.shell_tools import build_shell_tools
        from harness.tools.builtin.pwsh_tools import build_pwsh_tools
        from types import SimpleNamespace
        for factory in (build_shell_tools, build_pwsh_tools):
            for registry in (None, SimpleNamespace()):
                with current_language.set('zh-CN'):
                    tool = factory(timeout=37, registry=registry)[0]
                for language in ('en', 'zh-CN', 'en'):
                    with current_language.set(language):
                        expected = factory(timeout=37, registry=registry)[0].schema.to_dict()
                    with patch('harness.i18n.localize_description', side_effect=AssertionError('reverse lookup'), create=True):
                        result = (tool.schema).resolve_description(language).to_dict()
                    self.assertEqual(result, expected)
                    self.assertEqual('run_in_background' in result['parameters']['properties'], registry is not None)

    def test_terminal_and_session_query_schemas_keep_instance_language_identity(self):
        from harness.tools.builtin.terminal_tools import build_terminal_tools, TerminalSessionService
        from harness.tools.builtin.session_query_tools import build_session_query_tools
        from harness.session.store import SessionStore
        from types import SimpleNamespace
        for background in (False, True):
            service, store = TerminalSessionService(), SessionStore()
            def build():
                return build_terminal_tools(service, registry=SimpleNamespace() if background else None) + build_session_query_tools(store)
            with current_language.set('zh-CN'):
                registered = build()
            self.assertEqual(len(registered), 11)
            originals = [tool.schema.to_dict() for tool in registered]
            for language in ('en', 'zh-CN', 'en'):
                with current_language.set(language):
                    expected = {tool.schema.name: tool.schema.to_dict() for tool in build()}
                for tool in registered:
                    with self.subTest(tool=tool.schema.name, language=language, background=background), patch(
                            'harness.i18n.localize_description', side_effect=AssertionError('reverse lookup'), create=True):
                        self.assertEqual((tool.schema).resolve_description(language).to_dict(), expected[tool.schema.name])
            self.assertEqual([tool.schema.to_dict() for tool in registered], originals)

    def test_project_memory_plan_and_agent_guidance_schema_configuration_matrix(self):
        from harness.tools.builtin.project_tools import build_project_tools
        from harness.tools.builtin.memory_tools import build_layered_memory_tools
        from harness.tools.builtin.plan_tools import build_exit_plan_mode_tool
        from harness.tools.builtin.subagent_model_tools import build_list_subagent_models_tool
        from harness.tools.builtin.ralph_tools import build_ralph_tools
        from types import SimpleNamespace
        for git in (False, True):
            def build():
                return (build_project_tools(enable_git=git) + build_layered_memory_tools(SimpleNamespace(), candidates=SimpleNamespace() if git else None)
                        + [build_exit_plan_mode_tool(SimpleNamespace(), None), build_list_subagent_models_tool(base_url='https://fixture.invalid', model='OriginalModel')]
                        + build_ralph_tools(SimpleNamespace(), round_timeout=37, max_rounds_ceiling=9))
            with current_language.set('zh-CN'):
                registered = build()
            originals = [tool.schema.to_dict() for tool in registered]
            names = {tool.schema.name for tool in registered}
            self.assertEqual('project_git_check' in names, git)
            self.assertIn('user_memory_save', names)
            self.assertEqual('memory_candidates' in names, git)
            for language in ('en', 'zh-CN', 'en'):
                with current_language.set(language):
                    expected = {tool.schema.name: tool.schema.to_dict() for tool in build()}
                for tool in registered:
                    with self.subTest(tool=tool.schema.name, language=language, git=git), patch(
                            'harness.i18n.localize_description', side_effect=AssertionError('reverse lookup'), create=True):
                        self.assertIsNotNone(tool.schema.description_key)
                        self.assertEqual((tool.schema).resolve_description(language).to_dict(), expected[tool.schema.name])
            self.assertEqual([tool.schema.to_dict() for tool in registered], originals)

    def test_agent_and_workflow_schemas_preserve_background_and_identity_parameters(self):
        from harness.tools.builtin.subagent_tools import build_subagent_tools
        from harness.tools.builtin.workflow_tools import build_workflow_tools
        from types import SimpleNamespace
        for jobs, enabled in ((None, True), (SimpleNamespace(), False), (SimpleNamespace(), True)):
            manager = SimpleNamespace()
            def build():
                return build_subagent_tools(manager) + build_workflow_tools(manager, jobs=jobs,
                    enable_run_in_background=enabled, tool_name='OriginalWorkflow')
            with current_language.set('zh-CN'):
                registered = build()
            originals = [tool.schema.to_dict() for tool in registered]
            self.assertEqual(len(registered), 9)
            for language in ('en', 'zh-CN', 'en'):
                with current_language.set(language):
                    expected = {tool.schema.name: tool.schema.to_dict() for tool in build()}
                for tool in registered:
                    with self.subTest(tool=tool.schema.name, language=language), patch(
                            'harness.i18n.localize_description', side_effect=AssertionError('reverse lookup'), create=True):
                        self.assertIsNotNone(tool.schema.description_key)
                        result = (tool.schema).resolve_description(language).to_dict()
                        self.assertEqual(result, expected[tool.schema.name])
                        if tool.schema.name == 'OriginalWorkflow':
                            self.assertEqual('run_in_background' in result['parameters']['properties'], jobs is not None and enabled)
            self.assertEqual([tool.schema.to_dict() for tool in registered], originals)

    def test_goal_read_create_and_mcp_resource_schemas_follow_explicit_language(self):
        from harness.tools.builtin.goal_tools import GoalStore, build_goal_tools
        from harness.tools.builtin.mcp_tools import build_mcp_resource_tools
        from types import SimpleNamespace
        def build():
            return [tool for tool in build_goal_tools(GoalStore()) if tool.schema.name != 'update_goal'] + build_mcp_resource_tools(SimpleNamespace())
        with current_language.set('zh-CN'):
            registered = build()
        self.assertEqual(len(registered), 5)
        originals = [tool.schema.to_dict() for tool in registered]
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                expected = {tool.schema.name: tool.schema.to_dict() for tool in build()}
            for tool in registered:
                with self.subTest(tool=tool.schema.name, language=language), patch(
                        'harness.i18n.localize_description', side_effect=AssertionError('reverse lookup'), create=True):
                    self.assertEqual((tool.schema).resolve_description(language).to_dict(), expected[tool.schema.name])
        self.assertEqual([tool.schema.to_dict() for tool in registered], originals)

    def test_composed_goal_and_todo_descriptions_keep_all_resource_parts(self):
        from harness.tools.builtin.goal_tools import GoalStore, build_goal_tools
        from harness.tools.builtin.todo_tools import TodoStore, build_todo_tools
        for parallel in (False, True):
            def build():
                return build_goal_tools(GoalStore()) + build_todo_tools(TodoStore(), allow_parallel=parallel)
            with current_language.set('zh-CN'):
                registered = build()
            self.assertEqual(len(registered), 7)
            originals = [tool.schema.to_dict() for tool in registered]
            for language in ('en', 'zh-CN', 'en'):
                with current_language.set(language):
                    expected = {tool.schema.name: tool.schema.to_dict() for tool in build()}
                for tool in registered:
                    with self.subTest(tool=tool.schema.name, language=language, parallel=parallel), patch(
                            'harness.i18n.localize_description', side_effect=AssertionError('reverse lookup'), create=True):
                        self.assertEqual((tool.schema).resolve_description(language).to_dict(), expected[tool.schema.name])
            self.assertEqual([tool.schema.to_dict() for tool in registered], originals)

    def test_discovery_authoring_and_agent_loading_have_explicit_resource_identity(self):
        from harness.tools.discovery import build_tool_search
        from harness.plugins.authoring_tools import build_authoring_tools
        from harness.agents.tool import build_agent_tool
        from types import SimpleNamespace
        for skills in (False, True):
            def build():
                host = SimpleNamespace(config=SimpleNamespace(enable_skills=skills))
                return [build_tool_search()] + build_authoring_tools(host) + build_agent_tool(SimpleNamespace(), SimpleNamespace())
            with current_language.set('zh-CN'):
                registered = build()
            self.assertEqual(len(registered), 4 if skills else 3)
            originals = [tool.schema.to_dict() for tool in registered]
            for language in ('en', 'zh-CN', 'en'):
                with current_language.set(language):
                    expected = {tool.schema.name: tool.schema.to_dict() for tool in build()}
                for tool in registered:
                    with self.subTest(tool=tool.schema.name, language=language, skills=skills), patch(
                            'harness.i18n.localize_description', side_effect=AssertionError('reverse lookup'), create=True):
                        self.assertIsNotNone(tool.schema.description_key)
                        self.assertEqual((tool.schema).resolve_description(language).to_dict(), expected[tool.schema.name])
            self.assertEqual([tool.schema.to_dict() for tool in registered], originals)


class HarnessBrowserSchemaIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_browser_registered_in_harness_resolves_without_reverse_lookup(self):
        import tempfile
        from pathlib import Path
        from dataclasses import fields
        from harness import Harness, HarnessConfig
        from tests.web_fixture import FixtureTransport
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            flags = {item.name: False for item in fields(HarnessConfig) if item.name.startswith('enable_')}
            with current_language.set('zh-CN'):
                host = Harness(HarnessConfig(api_key='fixture', model='fixture', home=root / 'state', cwd=root,
                                             **flags), transport=FixtureTransport())
            try:
                if 'browser' not in host.tools.names():
                    host._register_browser_tool()
                schema = host.tools.get('browser').schema
                original = schema.to_dict()
                for language in ('en', 'zh-CN', 'en'):
                    with patch('harness.i18n.localize_description', side_effect=AssertionError('reverse lookup'), create=True):
                        resolved = (schema).resolve_description(language)
                    self.assertEqual(resolved.description, resources(language)['python.harness.15e31eb3ab97'])
                    self.assertEqual(resolved.parameters, schema.parameters)
                self.assertEqual(schema.to_dict(), original)
            finally:
                await host.close()

    async def test_team_and_external_agent_tool_schemas_keep_authorization_configuration(self):
        from harness.collaboration.acceptance import build_acceptance_evidence_tool
        from harness.collaboration.history import build_team_history_tool
        from harness.acp.tool import build_external_agent_tools
        from types import SimpleNamespace
        for shell, filesystem in ((False, False), (True, False), (False, True), (True, True)):
            host = SimpleNamespace(config=SimpleNamespace(enable_shell=shell, enable_fs=filesystem))
            def build():
                return [build_acceptance_evidence_tool(), build_team_history_tool(host)] + build_external_agent_tools(host)
            with current_language.set('zh-CN'):
                registered = build()
            names = {tool.schema.name for tool in registered}
            self.assertEqual('delegate_agent' in names, shell and filesystem)
            originals = [tool.schema.to_dict() for tool in registered]
            for language in ('en', 'zh-CN', 'en'):
                with current_language.set(language):
                    expected = {tool.schema.name: tool.schema.to_dict() for tool in build()}
                for tool in registered:
                    with self.subTest(tool=tool.schema.name, language=language), patch(
                            'harness.i18n.localize_description', side_effect=AssertionError('reverse lookup'), create=True):
                        self.assertIsNotNone(tool.schema.description_key)
                        self.assertEqual((tool.schema).resolve_description(language).to_dict(), expected[tool.schema.name])
            self.assertEqual([tool.schema.to_dict() for tool in registered], originals)
