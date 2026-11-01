"""当前清单严格校验字段类型，不解析旧字符串依赖或改写原件。"""
import json
import tempfile
import unittest
from pathlib import Path

from harness.agents.parser import parse_agent_text
from harness.skills.parser import parse_skill_file
from harness.core.errors import AgentParseError, SkillParseError


def manifest(data):
    return '---\n' + json.dumps(data) + '\n---\noriginal instructions'


class ManifestStrictTypesTests(unittest.TestCase):
    def test_agent_invalid_fields_are_rejected(self):
        for field, value in [('name', 123), ('description', []), ('agent_type', 1), ('model', 12), ('metadata', [])]:
            with self.subTest(field=field), self.assertRaises(AgentParseError):
                parse_agent_text(manifest({'name': 'fixture', field: value}))

    def test_members_require_native_dependency_lists(self):
        base = {'name': 'team', 'agent_type': 'team', 'members': [{'name': 'lead', 'role': 'lead'}, {'name': 'first'}, {'name': 'second', 'depends_on': ['first']}]}
        self.assertEqual(parse_agent_text(manifest(base)).members[2].depends_on, ('first',))
        for field, value in [('depends_on', '["first"]'), ('name', 12), ('prompt', []), ('model', 12), ('agent', 12)]:
            data = json.loads(json.dumps(base))
            data['members'][2][field] = value
            with self.subTest(field=field), self.assertRaises(AgentParseError):
                parse_agent_text(manifest(data))

    def test_skill_bad_metadata_is_not_silently_discarded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'SKILL.md'
            for field, value in [('metadata', []), ('metadata', None), ('when_to_use', {'bad': True})]:
                text = manifest({'name': 'fixture', 'description': 'original description', field: value})
                path.write_text(text, encoding='utf-8')
                with self.assertRaises(SkillParseError):
                    parse_skill_file(path)
                self.assertEqual(path.read_text(encoding='utf-8'), text)
