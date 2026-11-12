"""随包系统技能：目录、frontmatter 与注册行为保持稳定。"""
import tempfile
import unittest
from pathlib import Path

from harness.skills.parser import parse_skill_file
from harness.skills.paths import SYSTEM_SKILL_ROOT
from harness.skills.registry import build_default_registry

#: 随包分发的系统技能清单；新增技能时同步扩展。
EXPECTED_SYSTEM_SKILLS = ('code-review', 'project-onboarding', 'tech-docs', 'website-design')


class SystemSkillTests(unittest.TestCase):
    def test_system_skill_root_contains_exactly_the_shipped_skills(self):
        self.assertTrue(SYSTEM_SKILL_ROOT.is_dir())
        directories = sorted(entry.name for entry in SYSTEM_SKILL_ROOT.iterdir()
                             if entry.is_dir() and (entry / 'SKILL.md').is_file())
        self.assertEqual(directories, sorted(EXPECTED_SYSTEM_SKILLS))

    def test_each_shipped_skill_parses_with_complete_frontmatter(self):
        for name in EXPECTED_SYSTEM_SKILLS:
            with self.subTest(skill=name):
                skill = parse_skill_file(SYSTEM_SKILL_ROOT / name / 'SKILL.md')
                self.assertEqual(skill.name, name)
                self.assertTrue(skill.description.strip())
                self.assertTrue(skill.when_to_use and skill.when_to_use.strip())
                self.assertGreater(len(skill.content.strip()), 200)
                self.assertTrue(skill.model_invocable)
                self.assertTrue(skill.user_invocable)

    def test_default_registry_discovers_shipped_skills_without_user_or_project_roots(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = build_default_registry(cwd=str(Path(directory) / 'project'),
                                              home=str(Path(directory) / 'home'))
            discovered = {skill.name for skill in registry.list_all()}
            for name in EXPECTED_SYSTEM_SKILLS:
                self.assertIn(name, discovered)
                self.assertEqual(registry.get(name).source, 'system-harness')


if __name__ == '__main__':
    unittest.main()
