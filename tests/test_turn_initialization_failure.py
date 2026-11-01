"""轮次已开始后初始化失败也须提交结束，不能留下未结束轮次。"""
import asyncio
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path
from unittest.mock import patch

from harness import Harness, HarnessConfig
from tests.agent_configuration_fixture import RecordingTransport


class TurnInitializationFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_initialization_error_and_cancellation_close_turn_before_next_prompt(self):
        for language in ('en','zh-CN'):
            for cancelled in (False,True):
                with self.subTest(language=language,cancelled=cancelled), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    flags = {item.name:False for item in fields(HarnessConfig) if item.name.startswith('enable_')}
                    transport = RecordingTransport()
                    app = Harness(HarnessConfig(api_key='fixture',home=root/'state',cwd=root,language=language,**flags),transport=transport)
                    try:
                        session = app.create_session()
                        agent = app._agent_for(session)
                        failure = asyncio.CancelledError() if cancelled else RuntimeError('Original initialization {diagnostic}')
                        with patch('harness.core.agent.TodoProgressReminder.sync',side_effect=failure):
                            result = await agent.prompt(session,'Original task')
                        self.assertEqual(result.reason,'aborted' if cancelled else 'error')
                        self.assertIsNone(session.open_turn)
                        self.assertEqual(len([event for event in session.events if event.type=='turn/start']),1)
                        self.assertEqual(len([event for event in session.events if event.type=='turn/end']),1)
                        self.assertEqual(transport.requests,[])
                        if not cancelled:
                            self.assertEqual(result.error,str(failure))
                        next_result = await agent.prompt(session,'Next task')
                        self.assertEqual(next_result.reason,'completed')
                        self.assertEqual(next_result.turn,2)
                        self.assertIsNone(session.open_turn)
                    finally:
                        await app.close()
