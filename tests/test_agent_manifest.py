"""验证 Agent 与团队统一使用 AGENTS.md 创建、安装和发现。"""
import tempfile
import unittest
from pathlib import Path

from harness.agents.authoring import create
from harness.agents.parser import parse_agent_text
from harness.agents.personality import render_agent
from harness.agents.registry import AgentRegistry, FilesystemAgentProvider
from harness.plugins.installer import install_from_directory


class AgentManifestTests(unittest.TestCase):
    def test_create_install_and_discover_agent_and_team(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = AgentRegistry()
            for kind in ('agent', 'team'):
                with self.subTest(kind=kind):
                    payload = {'name': f'sample-{kind}', 'description': '文件名回归', 'agent_type': kind}
                    if kind == 'team':
                        payload['members'] = [
                            {'name': 'leader', 'role': 'lead', 'prompt': '负责协调'},
                            {'name': 'worker', 'role': 'member', 'prompt': '负责执行'},
                        ]
                    agent = create(root / 'source', payload, registry)
                    self.assertEqual(agent.path.name, 'AGENTS.md')
                    self.assertTrue(agent.path.is_file())
                    installed = install_from_directory(agent.path.parent, kind='agent', home=root / 'home')
                    self.assertTrue((installed / 'AGENTS.md').is_file())
                    found = FilesystemAgentProvider([(installed.parent, 500, 'user')]).discover()
                    loaded = next(item for item in found if item.name == agent.name)
                    self.assertEqual(loaded.path, installed / 'AGENTS.md')
                    self.assertEqual(loaded.is_team, kind == 'team')

    def test_optional_description_round_trips_without_instructions(self):
        """简介和指令留空仍可创建、读取并再次解析。"""
        with tempfile.TemporaryDirectory() as directory:
            agent = create(Path(directory), {'name': 'optional-description'}, AgentRegistry())
            self.assertEqual(agent.description, '')
            self.assertEqual(agent.system_prompt, '')
            loaded = parse_agent_text(agent.path.read_text(encoding='utf-8'))
            self.assertEqual(loaded.description, '')

    def test_personality_uses_only_saved_text_for_agents_and_teams(self):
        """人格正文独立保存，创建和重读不注入任何模板属性。"""
        with tempfile.TemporaryDirectory() as directory:
            for kind in ('agent', 'team'):
                for content in (None, '', '用简洁中文解释，并保留用户自定义的风格。'):
                    with self.subTest(kind=kind, content=content):
                        metadata = {} if content is None else {'personality_prompt': content}
                        payload = {'name': 'text-' + kind, 'agent_type': kind, 'metadata': metadata}
                        if kind == 'team':
                            payload['members'] = [
                                {'name': 'lead', 'role': 'lead', 'prompt': '协调'},
                                {'name': 'worker', 'prompt': '执行'},
                            ]
                        root = Path(directory) / kind / str(len(str(content)))
                        agent = create(root, payload, AgentRegistry())
                        loaded = parse_agent_text(agent.path.read_text(encoding='utf-8'))
                        self.assertEqual(loaded.metadata, metadata)
                        self.assertEqual(render_agent(loaded), content or '')

    def test_import_uses_current_manifest_path(self):
        agent = parse_agent_text('---\nname: imported\ndescription: 导入\n---\n正文')
        self.assertEqual(agent.path.name, 'AGENTS.md')
