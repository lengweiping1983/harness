"""摘要关闭失败保留原生成错误/取消，不将外层变化误判为证据丢失。"""
import asyncio
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path
from unittest.mock import Mock

from harness import Harness, HarnessConfig
from tests.agent_configuration_fixture import RecordingTransport
from tests.test_adapter_stream_cleanup import Stream


class SummaryFailureChainTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_summary_entry_preserves_generation_and_close_objects(self):
        for language in ('en','zh-CN','en'):
            for cancelled in (False,True):
                with self.subTest(language=language,cancelled=cancelled), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    flags = {item.name:False for item in fields(HarnessConfig) if item.name.startswith('enable_')}
                    transport = RecordingTransport()
                    app = Harness(HarnessConfig(api_key='fixture',home=root/'state',cwd=root,language=language,**flags),transport=transport)
                    try:
                        session = app.create_session()
                        agent = app._agent_for(session)
                        source = asyncio.CancelledError('Original cancellation') if cancelled else RuntimeError('Original provider {failure}')
                        closing = OSError('Original stream close {failure}')
                        attempts = []
                        app.adapter.stream = Mock(return_value=Stream('summary',attempts,closing,source))
                        with self.assertRaises(OSError) as caught:
                            await agent._summarize_transcript('Original transcript',agent.config)
                        self.assertIs(caught.exception,closing)
                        self.assertIs(caught.exception.__context__,source)
                        self.assertEqual(attempts,['summary'])
                        app.adapter.stream.assert_called_once()
                        self.assertEqual(transport.requests,[])
                        self.assertEqual(tuple(session.events),())
                    finally:
                        await app.close()
