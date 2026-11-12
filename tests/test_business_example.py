"""业务示例的真实命令、当前事件与双语提示回归。"""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from examples import business_agent as example
from harness.core.language import current_language
from harness.tools.registry import ToolRegistry


class FixtureHarness:
    def __init__(self):
        self.tools = ToolRegistry()
        self.persona = ''
        self.question = ''

    def tool(self, *args, **kwargs):
        return self.tools.tool(*args, **kwargs)

    def set_persona(self, value):
        self.persona = value

    def add_prompt(self, name, value, **kwargs):
        self.rules = value

    def status(self):
        return {'tools': [schema.name for schema in self.tools.schemas()]}

    def create_session(self, **kwargs):
        self.title = kwargs['title']
        return SimpleNamespace(id='fixture')

    async def stream(self, session, question):
        self.question = question
        yield {'type': 'todo/write', 'todos': [], 'payload': {'todos': [{'status': 'pending', 'content': 'obsolete'}]}}
        yield {'type': 'assistant/delta', 'delta_type': 'thinking', 'delta': 'original'}


class BusinessExampleTests(unittest.IsolatedAsyncioTestCase):
    async def configured(self):
        app = FixtureHarness()
        with redirect_stdout(io.StringIO()):
            await example._review(app, Path('/fixture'))
        return app

    async def test_test_command_preserves_exit_and_literal_target(self):
        app = await self.configured()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'pytest.py').write_text('import sys,json\nfrom pathlib import Path\nPath("args.json").write_text(json.dumps(sys.argv[1:]))\nfor i in range(40): print(i)\nraise SystemExit(7)\n')
            ctx = SimpleNamespace(cwd=root, services={})
            target = 'test file; echo injected > marker'
            result = await app.tools.get('run_tests').execute({'target': target}, ctx)
            self.assertEqual(result['exit_code'], 7)
            self.assertEqual(json.loads((root / 'args.json').read_text()), ['-q', '--', target])
            self.assertFalse((root / 'marker').exists())
            self.assertEqual(result['output'].splitlines(), [str(i) for i in range(10, 40)])
            self.assertEqual(app.tools.get('run_tests').effect, 'execute')
            self.assertTrue(app.tools.get('run_tests').approval_required)

    async def test_language_and_current_empty_todos(self):
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language), redirect_stdout(io.StringIO()) as output:
                app = FixtureHarness()
                await example._review(app, Path('/fixture'))
                text = output.getvalue()
                for value in (app.persona, app.rules, app.question, app.title, text):
                    self.assertEqual(any('\u4e00' <= c <= '\u9fff' for c in value), language == 'zh-CN')
                self.assertNotIn('obsolete', text)
                self.assertIn('0/0', text)
                self.assertEqual(app.tools.get('git_log').effect, 'read')

    async def test_main_closes_after_review_failure_and_restores_language(self):
        app = SimpleNamespace(close=unittest.mock.AsyncMock())
        original = current_language.get()
        config = SimpleNamespace(language='en')
        with patch.object(example.HarnessConfig, 'from_env', return_value=config), patch.object(example, 'Harness', return_value=app), patch.object(example, '_review', side_effect=RuntimeError('fixture')):
            with self.assertRaisesRegex(RuntimeError, 'fixture'):
                await example.main('/fixture')
        app.close.assert_awaited_once()
        self.assertEqual(current_language.get(), original)

    async def test_command_timeout_reaps_owned_process(self):
        process = object()
        ctx = SimpleNamespace(cwd=Path('/fixture'), services={})
        with patch.object(example, 'spawn_owned', return_value=process), patch.object(example, 'collect_process', return_value=(b'partial', b'', -1, True)), patch.object(example, 'cleanup_process') as cleanup:
            with self.assertRaises(TimeoutError):
                await example._command([sys.executable, '-V'], ctx)
            cleanup.assert_awaited_once_with(process, ctx)

    async def test_git_uses_literal_paths_and_ignores_inherited_repository(self):
        app = await self.configured()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            example.run_git(root, 'init')
            (root / '[a].txt').write_text('literal')
            example.run_git(root, 'add', '--', '.')
            example.run_git(root, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-m', 'literal')
            (root / 'a.txt').write_text('other')
            example.run_git(root, 'add', '--', '.')
            example.run_git(root, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-m', 'other')
            with patch.dict(os.environ, {'GIT_DIR': str(root / 'missing')}):
                result = await app.tools.get('git_log').execute({'path': '[a].txt'}, SimpleNamespace(cwd=root))
            self.assertEqual([row['subject'] for row in result['commits']], ['literal'])

    async def test_stream_error_returns_failure(self):
        app = FixtureHarness()
        async def failed_stream(*args):
            yield {'type': 'error', 'message': 'Original failure'}
        app.stream = failed_stream
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(await example._review(app, Path('/fixture')), 1)
        self.assertIn('Original failure', output.getvalue())
