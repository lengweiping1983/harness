"""模型创建工具的说明及错误按当前语言解析，用户指引保留原文。"""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from harness.core.language import current_language
from harness.i18n import tr
from harness.plugins.authoring_tools import build_authoring_tools


class AuthoringToolLanguageTests(unittest.TestCase):
    def test_descriptions_errors_and_real_creation_in_both_languages(self):
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language), tempfile.TemporaryDirectory() as directory:
                app = SimpleNamespace(config=SimpleNamespace(enable_skills=True), refresh_skills=lambda: None)
                tools = build_authoring_tools(app)
                for tool in tools:
                    kind = tool.schema.name.removeprefix('author_')
                    self.assertEqual(tool.schema.description, tr('plugin.authoring.' + kind + '_tool'))
                    args = {'name': 'fixture-' + kind, 'description': 'Original description 原文', 'instructions': 'Original instructions 用户原文'}
                    with self.assertRaises(ValueError) as failure:
                        tool.execute(args, SimpleNamespace(cwd=None))
                    self.assertEqual(str(failure.exception), tr('plugin.authoring.cwd'))
                    with self.assertRaises(ValueError) as failure:
                        tool.execute({**args, 'instructions': ' '}, SimpleNamespace(cwd=directory))
                    self.assertEqual(str(failure.exception), tr('plugin.authoring.complete'))
                    result = tool.execute(args, SimpleNamespace(cwd=directory))
                    source = Path(result['path'])
                    skill = source / 'SKILL.md' if kind == 'skill' else source / 'skills' / args['name'] / 'SKILL.md'
                    self.assertIn(args['instructions'], skill.read_text(encoding='utf-8'))
                    if kind == 'plugin':
                        self.assertFalse(result['installed'])
                    with self.assertRaises(FileExistsError):
                        tool.execute(args, SimpleNamespace(cwd=directory))
                    self.assertIn(args['instructions'], skill.read_text(encoding='utf-8'))
