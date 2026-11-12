"""步骤状态结算失败不得继续请求模型，原错误沿轮次收尾返回。"""
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path
from unittest.mock import Mock, patch

from harness import Harness, HarnessConfig
from tests.agent_configuration_fixture import RecordingTransport


class PreStepHookFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_plan_settlement_and_sync_hook_fail_before_model_request(self):
        for language in ('en','zh-CN'):
            for plan in (False,True):
                with self.subTest(language=language,plan=plan), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    flags = {item.name:False for item in fields(HarnessConfig) if item.name.startswith('enable_')}
                    flags['enable_plan_mode'] = plan
                    transport = RecordingTransport()
                    app = Harness(HarnessConfig(api_key='fixture',home=root/'state',cwd=root,language=language,**flags),transport=transport)
                    try:
                        session = app.create_session()
                        agent = app._agent_for(session)
                        failure = RuntimeError('Original step settlement {diagnostic}')
                        following = Mock()
                        if plan:
                            with patch.object(agent.plan_controller,'settle',side_effect=failure) as settlement:
                                agent.add_pre_step_hook(following)
                                result = await agent.prompt(session,'Original task')
                            settlement.assert_awaited_once()
                        else:
                            agent.add_pre_step_hook(Mock(side_effect=failure))
                            agent.add_pre_step_hook(following)
                            result = await agent.prompt(session,'Original task')
                        self.assertEqual(result.reason,'error')
                        self.assertEqual(result.error,str(failure))
                        self.assertEqual(transport.requests,[])
                        following.assert_not_called()
                        self.assertIsNone(session.open_turn)
                        self.assertFalse(agent.accepting_steering)
                        self.assertEqual(len([event for event in session.events if event.type=='turn/end']),1)
                    finally:
                        await app.close()
