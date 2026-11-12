"""首次动态注册保持默认启停过滤，既有Agent按宿主实时解析目录。"""
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path

from harness import Harness, HarnessConfig
from harness.skills.parser import SkillMetadata
from harness.tools.registry import ToolRunContext
from tests.agent_configuration_fixture import RecordingTransport


class DynamicSkillRegistrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_registration_respects_disabled_state_for_existing_agent_and_tool(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            flags = {field.name:False for field in fields(HarnessConfig) if field.name.startswith('enable_')}
            app = Harness(HarnessConfig(api_key='fixture',home=root/'state',cwd=root,**flags),transport=RecordingTransport())
            try:
                session = app.create_session()
                agent = app._agent_for(session)
                self.assertIsNone(app.skills)
                app.set_skill_enabled('dynamic-skill',False)
                app.add_skill(SkillMetadata(name='dynamic-skill',description='Original description',content='Original 用户正文 {literal}'))
                app.add_skill(SkillMetadata(name='sample:review',plugin_id='sample',description='Plugin original',content='Plugin original'))
                self.assertIn('sample:review',[skill.name for skill in app.skills.list_all()])
                self.assertEqual(app.skills.list(),[])
                self.assertEqual(agent.skills_for_session(session).list(),[])
                tool = app.tools.get('skill')
                ctx = ToolRunContext('load',session.id,str(root),1,1,services={'session':session})
                self.assertTrue((await tool.execute({'name':'dynamic-skill'},ctx)).is_error)
                app.set_skill_enabled('dynamic-skill',True)
                self.assertEqual([skill.name for skill in agent.skills_for_session(session).list()],['dynamic-skill'])
                loaded = await tool.execute({'name':'dynamic-skill'},ctx)
                self.assertFalse(loaded.is_error)
                self.assertIn('Original 用户正文 {literal}',loaded.content[0].text)
                app.refresh_skills()
                self.assertEqual([skill.name for skill in agent.skills_for_session(session).list()],['dynamic-skill'])
                app.set_skill_enabled('dynamic-skill',False)
                self.assertEqual(agent.skills_for_session(session).list(),[])
            finally:
                await app.close()
