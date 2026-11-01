"""执行器存储与宿主技能运行根分离，所有写入均使用隔离夹具。"""
import tempfile
import unittest
from pathlib import Path

from harness.core.errors import ConfigError
from harness.core.language import current_language
from harness.execution.config import ExecutionConfig, ProjectConfig
from harness.execution.runtime import ExecutionRuntime
from harness.plugins.state import EscStateStore


class ExecutionSkillHomeTests(unittest.IsolatedAsyncioTestCase):
    async def test_shared_catalog_loading_precedence_disabled_and_isolation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            host, project = root / '.harness', root / 'project'
            project.mkdir()
            for base, name, body in ((root / '.agents/skills', 'sample', 'User original'),
                                     (project / '.agents/skills', 'sample', 'Project original'),
                                     (root / '.agents/skills', 'blocked', 'Disabled original')):
                path = base / name / 'SKILL.md'
                path.parent.mkdir(parents=True)
                path.write_text(f'---\nname: {name}\ndescription: Fixture\n---\n{body}\n', encoding='utf-8')
            EscStateStore(host).set_enabled('skill', 'blocked', False)
            for shared in (False, True):
                runtime = ExecutionRuntime(ExecutionConfig(root / ('shared' if shared else 'isolated'),
                    {'project': ProjectConfig(project)}, skill_home=host if shared else None))
                try:
                    opened = await runtime.call('local', 'harness_session_open', {'project_id': 'project', 'request_id': 'open'})
                    sid = opened['data']['session_id']
                    for language in ('en', 'zh-CN', 'en'):
                        with current_language.set(language):
                            listing = await runtime.call('local', 'harness_skill_list', {'session_id': sid})
                            names = [s['name'] for s in listing['data']['skills']]
                            self.assertIn('sample', names)
                            self.assertNotIn('blocked', names)
                            context = await runtime.call('local', 'harness_context', {})
                            self.assertEqual(context['data']['projects'][0]['skills'], listing['data']['skills'])
                            loaded = await runtime.call('local', 'harness_skill_load', {'session_id': sid, 'name': 'sample'})
                            self.assertIn('Project original', loaded['data']['content'])
                            denied = await runtime.call('local', 'harness_skill_load', {'session_id': sid, 'name': 'blocked'})
                            self.assertTrue(denied['is_error'])
                    (project / '.agents/skills/sample/SKILL.md').unlink()
                    result = await runtime.call('local', 'harness_skill_load', {'session_id': sid, 'name': 'sample'})
                    self.assertEqual(result['is_error'], not shared)
                    if shared:
                        self.assertIn('User original', result['data']['content'])
                finally:
                    await runtime.close()
                path = project / '.agents/skills/sample/SKILL.md'
                path.write_text('---\nname: sample\ndescription: Fixture\n---\nProject original\n', encoding='utf-8')

    async def test_config_path_validation_and_language(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'project').mkdir()
            base = {'projects': {'p': {'root': 'project'}}}
            config = ExecutionConfig.from_dict({**base, 'skill_home': 'host'}, base=root)
            self.assertEqual(config.skill_home, root.resolve() / 'host')
            for language in ('en', 'zh-CN', 'en'):
                with current_language.set(language):
                    for value in ('', ' ', 0, False, [], {}, '\0'):
                        with self.subTest(language=language, value=value), self.assertRaises(ConfigError) as error:
                            ExecutionConfig.from_dict({**base, 'skill_home': value}, base=root)
                        self.assertEqual(any('\u4e00' <= c <= '\u9fff' for c in str(error.exception)), language == 'zh-CN')
            self.assertFalse((root / 'host').exists())
