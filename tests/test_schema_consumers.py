"""最终工具消费者仅解析显式资源，不能按模块或原文猜翻译身份。"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from harness.core.agent import AgentLoop
from harness.core.language import current_language
from harness.i18n import resources
from harness.llm.types import ToolSchema
from harness.tools.registry import ToolDefinition, ToolRegistry
from harness.tools.builtin.mcp_client import McpRemoteTool
from harness.tools.builtin.mcp_tools import build_remote_tool
from harness.web.views import tool_catalog


class SchemaConsumerTests(unittest.TestCase):
    def test_model_and_web_consumers_share_explicit_schema_and_preserve_originals(self):
        registry = ToolRegistry()
        async def execute(args, ctx):
            return None
        execute.__module__ = 'external.fixture'
        original = resources('zh-CN')['policy.edit']
        registry.register(ToolDefinition(ToolSchema(name='Owned', description='initial',
            description_key='tool.result.reader_description', parameters={'type': 'object'}), execute))
        registry.register(ToolDefinition(ToolSchema(name='Original', description=original,
            parameters={'type': 'object', 'description': original}), execute))
        agent = SimpleNamespace(tools=registry, tool_schema_transform=None, isolated_team_coordinator=False,
                                recovery_read_only=False, task_read_only=False)
        for language in ('en', 'zh-CN', 'en'):
            agent.language = language
            with current_language.set(language), patch('harness.i18n.localize_description', side_effect=AssertionError('reverse lookup'), create=True):
                model = {schema.name: schema.to_dict() for schema in AgentLoop._eligible_tool_schemas(agent)}
                web = {item['name']: item for item in tool_catalog(SimpleNamespace(tools=registry))}
            self.assertEqual(model['Owned']['description'], resources(language)['tool.result.reader_description'])
            self.assertEqual(web['Owned']['description'], model['Owned']['description'])
            self.assertEqual(model['Original']['description'], original)
            self.assertEqual(web['Original']['parameters']['description'], original)

    def test_remote_schema_preserves_verbatim_description_and_uses_explicit_empty_fallback(self):
        supervisor = SimpleNamespace(server_name='OriginalServer')
        original = '  ' + resources('zh-CN')['policy.edit'] + '\n'
        for description in (original, '', '  \n\t'):
            tool = build_remote_tool(supervisor, McpRemoteTool('OriginalTool', description,
                {'type': 'object', 'description': original}))
            registry = ToolRegistry()
            registry.register(tool)
            agent = SimpleNamespace(tools=registry, tool_schema_transform=None, isolated_team_coordinator=False,
                                    recovery_read_only=False, task_read_only=False)
            for language in ('en', 'zh-CN', 'en'):
                schema = tool.schema.resolve_description(language)
                self.assertEqual(schema.description, description if description.strip() else resources(language)['mcp.tool.fallback_description'].format(tool='OriginalTool'))
                self.assertEqual(schema.parameters['description'], original)
                agent.language = language
                with current_language.set(language):
                    model = AgentLoop._eligible_tool_schemas(agent)[0]
                    web = tool_catalog(SimpleNamespace(tools=registry))[0]
                self.assertEqual(model.description, schema.description)
                self.assertEqual(web['description'], schema.description)
                self.assertEqual(web['parameters']['description'], original)

    def test_validation_feedback_is_complete_and_keeps_external_requirements(self):
        import copy
        from harness.core.errors import ToolArgsError
        from harness.i18n import tr
        from harness.tools.schema import validate, validate_or_raise

        schema = {'type': 'object', 'properties': {
            'count': {'type': 'integer', 'maximum': 2, 'description': 'Original 原文 {count}'},
            'items': {'type': 'array', 'maxItems': 1, 'description': 'Original 原文 {items}'}},
            'required': ['count', 'items'], 'additionalProperties': False}
        value = {'count': 3, 'items': [1, 2]}
        original = copy.deepcopy((schema, value))
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                errors = validate(value, schema)
                self.assertEqual(len(errors), 2)
                for error, hint in zip(errors, ('Original 原文 {count}', 'Original 原文 {items}')):
                    self.assertIn(hint, error)
                    self.assertIn('Field requirements:' if language == 'en' else '字段要求：', error)
                with self.assertRaises(ToolArgsError) as caught:
                    validate_or_raise(value, schema, tool='Original-{tool}')
                self.assertTrue(caught.exception.details['not_executed'])
                self.assertEqual(caught.exception.details['errors'], errors)
                self.assertEqual(caught.exception.details['tool'], 'Original-{tool}')
                hints = [tr('python.schema.not_executed'), tr('python.schema.parameter_fields',
                    fields=tr('ui.common.list_separator').join(('count', 'items')),
                    required=tr('ui.common.list_separator').join(('count', 'items'))), tr('python.schema.correct_call')]
                self.assertEqual(caught.exception.message, tr('python.schema.b004c6665581', p0='Original-{tool}',
                    errors=tr('common.error_separator').join(errors), hints='\n'.join(hints)))
                valid = {'count': 1, 'items': []}
                self.assertIs(validate_or_raise(valid, schema), valid)
        self.assertEqual((schema, value), original)

    def test_anchored_schema_patterns_reject_trailing_newline_and_keep_search_semantics(self):
        from harness.tools.schema import validate

        anchored = {'type': 'string', 'pattern': '^[a-f0-9]{64}$'}
        self.assertEqual(validate('a' * 64, anchored), [])
        self.assertTrue(validate('a' * 64 + chr(10), anchored))
        self.assertEqual(validate('prefix-needle-suffix', {'type': 'string', 'pattern': 'needle'}), [])

    def test_real_tool_families_resolve_same_registered_schemas_for_both_consumers(self):
        import copy
        from harness.i18n import tr
        from harness.tools.builtin.project_tools import build_project_tools
        from harness.tools.builtin.subagent_model_tools import build_list_subagent_models_tool
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from harness.app.layered_memory import LayeredMemory
        from harness.app.memory import ProjectMemory
        from harness.tools.builtin.memory_tools import build_layered_memory_tools
        from harness.plugins.authoring_tools import build_authoring_tools
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        memory = LayeredMemory(Path(temporary.name), ProjectMemory())
        registry = ToolRegistry()
        with current_language.set('en'):
            tools = build_project_tools(enable_git=True)
            tools.append(build_list_subagent_models_tool(base_url='https://offline.invalid', model='Original/model'))
            tools.extend(build_layered_memory_tools(memory, candidates=SimpleNamespace()))
            tools.extend(build_authoring_tools(SimpleNamespace(config=SimpleNamespace(enable_skills=True))))
            self.assertEqual(len(tools), 22)
            for tool in tools:
                registry.register(tool)
        originals = copy.deepcopy([tool.schema.to_dict() for tool in tools])
        agent = SimpleNamespace(tools=registry, tool_schema_transform=None, isolated_team_coordinator=False,
                                recovery_read_only=False, task_read_only=False)
        for language in ('en', 'zh-CN', 'en'):
            agent.language = language
            with self.subTest(language=language), current_language.set(language):
                model = {schema.name: schema.to_dict() for schema in AgentLoop._eligible_tool_schemas(agent)}
                web = {row['name']: row for row in tool_catalog(SimpleNamespace(tools=registry))}
                for tool in tools:
                    name = tool.schema.name
                    self.assertEqual(model[name]['description'], tr(tool.schema.description_key))
                    self.assertEqual(web[name]['description'], model[name]['description'])
                    self.assertEqual(web[name]['parameters'], model[name]['parameters'])
                    self.assertNotIn('description_key', model[name])
        self.assertEqual([tool.schema.to_dict() for tool in tools], originals)
