"""源码正文、文件引用与命令输入在真实权限入口保持各自边界。"""
import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock

from harness.app.argument_sensitivity import shell_has_sensitive_reference
from harness.core.language import current_language
from harness.llm.types import ToolSchema, ToolUseBlock
from harness.tools.builtin.code_tools import build_code_tools
from harness.tools.builtin.fs_tools import build_fs_tools
from harness.tools.builtin.shell_tools import build_shell_tools
from harness.tools.builtin.widget_tools import build_widget_tools
from harness.tools.registry import ToolDefinition
from tests.test_permission_sensitive import permission_fixture


KEYBOARD = "document.addEventListener('keydown',e=>{if(e.key==='Escape'&&modalOpen)closeModal();});"
SOURCE = ("<script>" + KEYBOARD
          + "const getKey=event=>event.key;const getSecret=state=>state.secret;"
          + "const getCredentials=model=>model.credentials;</script>")


class PermissionArgumentSemanticsTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self):
        host, policy, session, registry, _, context, _ = permission_fixture('full_access')
        return host, policy, session, registry, context

    async def call(self, registry, context, tool, arguments):
        if not registry.has(tool.schema.name):
            registry.register(tool)
        else:
            self.assertIs(registry.get(tool.schema.name), tool)
        call = ToolUseBlock(id=context.call_id, name=tool.schema.name, input=arguments)
        return await registry.execute_call(call, context)

    async def test_real_file_write_and_edit_preserve_keyboard_source(self):
        host, _, _, registry, context = self.fixture()
        with tempfile.TemporaryDirectory() as directory:
            context = replace(context, cwd=directory)
            tools = {tool.schema.name: tool for tool in build_fs_tools(directory)}
            for index, language in enumerate(('en', 'zh-CN', 'en')):
                path = f'board-{index}.html'
                with self.subTest(language=language), current_language.set(language):
                    result = await self.call(registry, context, tools['write'], {'path': path, 'content': SOURCE})
                    self.assertFalse(result.is_error, result)
                    self.assertEqual((Path(directory) / path).read_text(encoding='utf-8'), SOURCE)
                    result = await self.call(registry, context, tools['edit'], {
                        'path': path, 'old_string': "e.key==='Escape'", 'new_string': "e.key==='Enter'",
                    })
                    self.assertFalse(result.is_error, result)
                    self.assertEqual((Path(directory) / path).read_text(encoding='utf-8'),
                                     SOURCE.replace("e.key==='Escape'", "e.key==='Enter'"))
        host.questions.ask.assert_not_awaited()
        host.adapter.complete.assert_not_awaited()

    @unittest.skipIf(os.name == 'nt', '此用例执行 POSIX heredoc，Windows 命令词法由隔离输入验证')
    async def test_real_shell_heredoc_writes_original_source(self):
        host, _, _, registry, context = self.fixture()
        with tempfile.TemporaryDirectory() as directory:
            context = replace(context, cwd=directory)
            tool = build_shell_tools()[0]
            command = "cat > board.js <<'JSEOF'\n" + KEYBOARD + '\nJSEOF'
            result = await self.call(registry, context, tool, {'command': command})
            self.assertFalse(result.is_error, result)
            self.assertEqual(result.data['exit_code'], 0)
            self.assertEqual((Path(directory) / 'board.js').read_text(encoding='utf-8'), KEYBOARD + '\n')
        host.questions.ask.assert_not_awaited()

    async def test_demo1_five_payload_shapes_reach_executor_without_rewriting(self):
        tools = {tool.schema.name: tool for tool in build_fs_tools(None) + build_shell_tools()}
        cases = [
            ('write', {'path': 'index.html', 'content': SOURCE}),
            ('write', {'path': 'trello-board.html', 'content': SOURCE + "const onKey=ev=>ev.key;"}),
            ('bash', {'command': "cat >> trello-board.html <<'JSEOF'\nlet drag=null;\n" + KEYBOARD + "\nJSEOF"}),
            ('bash', {'command': "cat >> trello-board.html <<'JSEOF'\nlet pending=null;\n" + KEYBOARD + "\nJSEOF"}),
            ('bash', {'command': "cat >> trello-board.html <<'JSEOF'\ndocument.addEventListener('pointercancel',()=>{pending=null;});\nJSEOF\n"
                                "cat >> trello-board.html <<'JSEOF'\n" + KEYBOARD + "\nJSEOF\nwc -c trello-board.html"}),
        ]
        for language in ('en', 'zh-CN', 'en'):
            for name, arguments in cases:
                with self.subTest(language=language, name=name), current_language.set(language):
                    host, _, _, registry, context = self.fixture()
                    tool = tools[name]
                    tool.execute = AsyncMock(return_value={'ok': True})
                    tool.to_content = tool.describe = tool.progress = None
                    result = await self.call(registry, context, tool, arguments)
                    self.assertFalse(result.is_error, result)
                    tool.execute.assert_awaited_once_with(arguments, unittest.mock.ANY)
                    self.assertEqual(tool.execute.await_args.args[0], arguments)
                    host.questions.ask.assert_not_awaited()

    async def test_sensitive_paths_and_literal_secrets_still_stop_before_execution(self):
        tools = {tool.schema.name: tool for tool in build_fs_tools(None) + build_shell_tools()}
        cases = [
            ('read', {'path': '.env'}),
            ('write', {'path': 'keys/server.key', 'content': SOURCE}),
            ('edit', {'path': '.ssh/config', 'old_string': 'a', 'new_string': 'b'}),
            ('glob', {'pattern': '**/*.key'}),
            ('grep', {'pattern': 'event.key', 'glob': '**/*.key'}),
            ('write', {'path': 'board.html', 'content': SOURCE + 'api_key=fixture-secret'}),
            ('write', {'path': 'board.html', 'content': 'token=x'}),
            ('write', {'path': 'board.html', 'content': 'AWS_PROFILE=fixture'}),
            ('write', {'path': 'board.html', 'content': '-----BEGIN PRIVATE KEY-----'}),
            ('bash', {'command': "cat <<'EOF' > board.html\nAuthorization: Bearer fixture-secret\nEOF"}),
        ]
        for name, arguments in cases:
            with self.subTest(name=name, arguments=arguments):
                _, _, _, registry, context = self.fixture()
                tool = tools[name]
                tool.execute = AsyncMock()
                result = await self.call(registry, context, tool, arguments)
                self.assertTrue(result.is_error, result)
                tool.execute.assert_not_awaited()

    async def test_data_text_can_describe_sensitive_paths_without_accessing_them(self):
        tools = [*build_fs_tools(None), *build_widget_tools()]
        cases = [('write', {'path': 'guide.html', 'content': '<p>配置位置：.env 和 server.key</p>'}),
                 ('grep', {'pattern': r'event\.key|server\.key', 'path': '.', 'glob': '*.js'}),
                 ('show_widget', {'title': '键盘交互', 'widget_code': SOURCE})]
        for name, arguments in cases:
            _, policy, _, _, context = self.fixture()
            tool = next(tool for tool in tools if tool.schema.name == name)
            self.assertIsNone(await policy.guard(tool, arguments, context))

    async def test_executable_code_checks_path_literals_without_flagging_members(self):
        tool = build_code_tools(None)[0]
        for language, code, denied in [
            ('node', 'const pick=event=>event.key;', False),
            ('python', 'result = event.key', False),
            ('node', 'require("fs").readFileSync(".env")', True),
            ('python', 'print(open("keys/server.key").read())', True),
            ('bash', 'cat .env', True),
            ('bash', "cat <<'EOF' > app.js\n" + KEYBOARD + '\nEOF', False),
        ]:
            with self.subTest(language=language, code=code):
                _, _, _, registry, context = self.fixture()
                tool.execute = AsyncMock(return_value={'ok': True})
                tool.to_content = tool.describe = None
                result = await self.call(registry, context, tool, {'language': language, 'code': code})
                self.assertEqual(result.is_error, denied, result)
                self.assertEqual(tool.execute.await_count, 0 if denied else 1)

    async def test_undeclared_nested_and_model_supplied_semantics_cannot_exempt_values(self):
        _, policy, _, _, context = self.fixture()
        tool = ToolDefinition(ToolSchema(name='custom', description='', parameters={'type': 'object'}),
                              AsyncMock(), argument_semantics={'content': 'text'})
        for arguments in (
            {'nested': {'content': 'event.key'}},
            {'content': {'path': '.env'}},
            {'content': 'api_key=fixture-secret'},
            {'argument_semantics': {'path': 'text'}, 'path': '.env'},
        ):
            with self.subTest(arguments=arguments):
                self.assertIsNotNone(await policy.guard(tool, arguments, context))

    async def test_semantic_contract_is_copied_and_bound_to_authority(self):
        _, policy, _, _, _ = self.fixture()
        declaration = {'content': 'text'}
        tool = ToolDefinition(ToolSchema(name='custom', description='', parameters={'type': 'object'}),
                              AsyncMock(), argument_semantics=declaration)
        authority = policy._tool_authority(tool)
        declaration['content'] = 'path'
        self.assertEqual(tool.argument_semantics['content'], 'text')
        with self.assertRaises(TypeError):
            tool.argument_semantics['content'] = 'path'
        self.assertNotIn('argument_semantics', json.dumps(tool.schema.to_dict()))
        tool.argument_semantics = {'content': 'path'}
        self.assertFalse(policy._same_authority(authority, tool))

    async def test_source_semantics_do_not_remove_write_approval_or_read_only_boundary(self):
        tool = next(tool for tool in build_fs_tools(None) if tool.schema.name == 'write')
        arguments = {'path': 'board.html', 'content': SOURCE}
        for policy_name in ('request_approval', 'read_only'):
            host, policy, _, _, _, context, _ = permission_fixture(policy_name)
            host.questions.ask.return_value = [{'selected_ids': ['deny']}]
            self.assertIsNotNone(await policy.guard(tool, arguments, context))
            question = host.questions.ask.await_args.kwargs['questions'][0]
            reasons = question['operation']['approval']['reasons']
            self.assertNotIn('sensitive_data_access', reasons)
            self.assertIn('read_only_policy' if policy_name == 'read_only' else 'workspace_write', reasons)

    async def test_unknown_argument_semantics_keep_conservative_scan(self):
        _, policy, _, _, context = self.fixture()
        tool = ToolDefinition(ToolSchema(name='custom', description='', parameters={'type': 'object'}),
                              AsyncMock(), argument_semantics={'content': 'unsupported'})
        self.assertIsNotNone(await policy.guard(tool, {'content': 'event.key'}, context))

    async def test_decorator_registration_consumes_the_same_semantic_contract(self):
        _, _, _, registry, context = self.fixture()
        executed = []

        @registry.tool('render_source', '渲染源码', {'type': 'object'}, effect='read',
                       argument_semantics={'content': 'text', 'path': 'path'})
        async def render_source(arguments, ctx):
            executed.append(arguments)
            return {'content': arguments['content']}

        tool = registry.get('render_source')
        allowed = {'path': 'board.html', 'content': SOURCE}
        result = await self.call(registry, context, tool, allowed)
        self.assertFalse(result.is_error, result)
        result = await self.call(registry, context, tool, {'path': '.env', 'content': SOURCE})
        self.assertTrue(result.is_error, result)
        self.assertEqual(executed, [allowed])

    def test_shell_data_source_and_execution_boundaries(self):
        allowed = [
            "cat <<'JS' > app.js\nif (event.key === 'Escape') closeModal();\nJS",
            "cat <<-'JS' > app.js\n\t" + KEYBOARD + '\n\tJS',
            "tee app.js <<'JS'\n" + KEYBOARD + '\nJS',
            "cat <<'JS' > app.js\n$(cat .env)\nJS",
            'node -e "const pick=event=>event.key;"',
            'python -c "result = event.key"',
            "printf '%s' 'event.key' > app.js",
        ]
        denied = [
            'cat .env', 'cat "my server.key"', 'cat --path=.env',
            'cat server#.key',
            "cat <<'JS' > keys/server.key\n" + KEYBOARD + '\nJS',
            "cat <<JS > app.js\n$(cat .env)\nJS",
            "cat <<JS > app.js\n`cat keys/server.key`\nJS",
            "bash <<'SH'\ncat .env\nSH",
            "node <<'JS'\nrequire('fs').readFileSync('.env')\nJS",
            "cat <<'JS' | node\nrequire('fs').readFileSync('.env')\nJS",
            "node -e 'require(\"fs\").readFileSync(\".env\")'",
            "bash -c 'cat .env'",
            'echo "$(cat .env)"',
            "printf '%s' '.env' | xargs cat",
            "printf '%s' 'cat .env' | sh",
            'cat "$(echo .env)"',
            'eval "$(echo \'cat .env\')"',
            "cat <<'JS' > out\n" + KEYBOARD,
        ]
        for command in allowed:
            with self.subTest(command=command):
                self.assertFalse(shell_has_sensitive_reference(command))
        for command in denied:
            with self.subTest(command=command):
                self.assertTrue(shell_has_sensitive_reference(command))
