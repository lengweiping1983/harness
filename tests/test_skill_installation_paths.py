"""技能安装与发现统一使用共享目录，写入仅限临时数据。"""
import tempfile
import unittest
from pathlib import Path

from harness.plugins.installer import install_from_directory, uninstall
from harness.skills.paths import user_skill_root
from harness.skills.registry import build_default_registry


class SkillInstallationPathTests(unittest.TestCase):
    def test_install_discover_and_uninstall_share_the_same_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            project = root / 'project'
            project.mkdir()
            source = root / 'sample-skill'
            source.mkdir()
            (source / 'SKILL.md').write_text(
                '---\nname: sample-skill\ndescription: Example skill\n---\nInstructions.\n',
                encoding='utf-8',
            )
            for home in (root / 'user' / '.harness', root / 'isolated-runtime'):
                for scope in ('user', 'project'):
                    with self.subTest(home=home, scope=scope):
                        expected = (project if scope == 'project' else home.parent if home.name == '.harness' else home) / '.agents' / 'skills'
                        installed = install_from_directory(source, kind='skill', scope=scope, cwd=project, home=home)
                        self.assertEqual(installed, expected / source.name)
                        registry = build_default_registry(cwd=str(project), home=str(home), include_system=False)
                        self.assertIn(source.name, [skill.name for skill in registry.list()])
                        scoped = registry.for_project(str(project))
                        self.assertIn(source.name, [skill.name for skill in scoped.list()])
                        uninstall(source.name, kind='skill', scope=scope, cwd=project, home=home)
                        self.assertFalse(installed.exists())
                        self.assertFalse((project / '.agent').exists())
                        self.assertFalse((expected.parent.parent / '.agent').exists())

    def test_default_user_root_uses_shared_directory(self):
        self.assertEqual(user_skill_root(), Path.home() / '.agents' / 'skills')

    def test_current_discovery_sources_keep_precedence_without_rewriting_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            project, home = root / 'project', root / 'runtime'
            project.mkdir()
            sources = [(project / '.agents/skills', 'project-agents'),
                       (project / '.harness/skills', 'project-harness'),
                       (user_skill_root(home), 'user-agents'), (home / 'skills', 'system-harness')]
            for index, (base, _) in enumerate(sources):
                path = base / 'fixture/SKILL.md'
                path.parent.mkdir(parents=True)
                path.write_text(f'---\nname: fixture\ndescription: Source {index}\n---\nOriginal {index}', encoding='utf-8')
            for index, (base, source) in enumerate(sources):
                registry = build_default_registry(cwd=str(project), home=str(home), include_system=False)
                self.assertEqual(registry.get('fixture').source, source)
                self.assertEqual(registry.get('fixture').description, f'Source {index}')
                self.assertEqual(registry.for_project(project).get('fixture').description, f'Source {index}')
                path = base / 'fixture/SKILL.md'
                self.assertIn(f'Original {index}', path.read_text(encoding='utf-8'))
                path.unlink()
