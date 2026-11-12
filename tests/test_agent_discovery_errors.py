"""坏Agent清单只影响自身，不中断其他有效Agent的发现。"""
import tempfile
import unittest
from pathlib import Path

from harness.agents.parser import parse_agent_text, parse_agent_file
from harness.agents.registry import FilesystemAgentProvider
from harness.core.errors import AgentParseError
from harness.core.language import current_language


class AgentDiscoveryErrorTests(unittest.TestCase):
    def test_invalid_yaml_uses_agent_error_boundary(self):
        for language in ('en', 'zh-CN'):
            with current_language.set(language):
                with self.assertRaises(AgentParseError) as caught:
                    parse_agent_text('---\nname: first\nname: second\n---\nbody')
                self.assertEqual(caught.exception.code, 'agent/parse')
                self.assertIn('Duplicate' if language == 'en' else '重复', caught.exception.message)

    def test_bad_files_do_not_hide_valid_agent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contents = {'good': b'---\nname: good\n---\noriginal prompt',
                        'bad-yaml': b'---\nname: first\nname: second\n---\nbody',
                        'bad-encoding': b'\xff\xfe\xff'}
            for name, content in contents.items():
                folder = root / name
                folder.mkdir()
                (folder / 'AGENTS.md').write_bytes(content)
            with self.assertRaises(AgentParseError):
                parse_agent_file(root / 'bad-encoding/AGENTS.md')
            provider = FilesystemAgentProvider([(root, 100, 'project-harness')])
            with self.assertLogs('harness.agents.registry', level='WARNING') as logs:
                agents = provider.discover()
            self.assertEqual([agent.name for agent in agents], ['good'])
            self.assertEqual(len(logs.records), 2)
            for name, content in contents.items():
                self.assertEqual((root / name / 'AGENTS.md').read_bytes(), content)
