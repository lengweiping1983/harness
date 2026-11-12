"""扩展安装范围拒绝非法值，避免SDK参数拼写错误操作其他目录。"""
import tempfile
import unittest
from pathlib import Path

from harness.core.errors import ValidationError
from harness.core.language import current_language
from harness.plugins.installer import install_from_directory, uninstall, validate
from harness.skills.paths import installation_root


class InstallationScopeValidationTests(unittest.TestCase):
    def test_invalid_kind_and_scope_preserve_all_candidate_directories(self):
        for language in ('en', 'zh-CN', 'en'):
            with tempfile.TemporaryDirectory() as folder, current_language.set(language):
                root = Path(folder)
                project, home = root/'project', root/'runtime'
                project.mkdir(); home.mkdir()
                source = root/'sample-skill'; source.mkdir()
                (source/'SKILL.md').write_text('---\nname: sample-skill\ndescription: Original description\n---\nOriginal instructions.\n', encoding='utf-8')
                for kind in ('skill', 'agent'):
                    for scope in ('user', 'project'):
                        target = installation_root(kind, scope, cwd=project, home=home)/source.name
                        target.mkdir(parents=True)
                        (target/'sentinel').write_bytes(b'original user data')
                original = {str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}
                cases = [('skill', value) for value in (None, '', 'system', 'User', True, [], {})]
                cases += [(value, 'user') for value in (None, '', 'skills', 'Agent', False, [], {})]
                for kind, scope in cases:
                    for action in (
                        lambda: installation_root(kind, scope, cwd=project, home=home),
                        lambda: install_from_directory(source, kind=kind, scope=scope, cwd=project, home=home),
                        lambda: uninstall(source.name, kind=kind, scope=scope, cwd=project, home=home),
                    ):
                        with self.subTest(language=language, kind=kind, scope=scope), self.assertRaises(ValidationError) as failure:
                            action()
                        self.assertEqual(any('\u4e00' <= c <= '\u9fff' for c in str(failure.exception)), language == 'zh-CN')
                        self.assertEqual({str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}, original)

    def test_direct_manifest_validation_rejects_invalid_kind(self):
        with tempfile.TemporaryDirectory() as folder:
            for kind in (None, '', 'skills', [], {}):
                with self.subTest(kind=kind), self.assertRaises(ValidationError):
                    validate(Path(folder), kind)
