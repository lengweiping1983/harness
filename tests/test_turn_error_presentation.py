"""错误轮次的实时帧、持久证据和运行回执必须保持同一诊断身份。"""
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path
from unittest.mock import patch

from harness import Harness, HarnessConfig
from harness.core.errors import HarnessError, LlmRequestError, LlmTimeoutError
from tests.agent_configuration_fixture import RecordingTransport


class TurnErrorPresentationTests(unittest.IsolatedAsyncioTestCase):
    async def test_failure_frames_match_committed_evidence_and_runtime_errors(self):
        for language in ('en', 'zh-CN', 'en'):
            for branch in ('request', 'timeout', 'resource', 'external', 'step-limit', 'budget'):
                with self.subTest(language=language, branch=branch), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    flags = {item.name: False for item in fields(HarnessConfig) if item.name.startswith('enable_')}
                    transport = RecordingTransport()
                    app = Harness(HarnessConfig(api_key='fixture', home=root/'state', cwd=root,
                        language=language, max_retries=0, **flags), transport=transport)
                    try:
                        session = app.create_session()
                        agent = app._agent_for(session)
                        ends, errors = [], []
                        app.bus.on('turn/end', lambda payload: ends.append(dict(payload)))
                        app.bus.on('error', lambda payload: errors.append(dict(payload)))
                        failures = {
                            'request': LlmRequestError('Original request {diagnostic}', status=503, cause='Original cause'),
                            'timeout': LlmTimeoutError('Original timeout', timeout=20),
                            'resource': HarnessError('Original product diagnostic', code='run/incomplete',
                                copy={'key': 'ui.run.incomplete', 'params': {'reason': 'Original reason'}}),
                            'external': RuntimeError('Original external {diagnostic}'),
                        }
                        if branch == 'step-limit':
                            app.config.max_steps = 0
                        if branch == 'budget':
                            app.config.token_budget = 0
                        with patch.object(agent, '_stream_once', side_effect=failures.get(branch, AssertionError('Unexpected model call'))):
                            record = app.runtime.submit(session.id, 'Original task', key='once')
                            await record.task
                        self.assertEqual(record.status, 'failed')
                        expected_code = {'request': 'llm/request', 'timeout': 'llm/timeout',
                            'resource': 'run/incomplete', 'external': 'run/error',
                            'step-limit': 'agent/step-limit', 'budget': 'agent/token-budget'}[branch]
                        self.assertEqual(record.error['code'], expected_code)
                        self.assertEqual(len(ends), 1)
                        self.assertEqual(len(errors), 1)
                        committed = session.latest('turn/end')
                        self.assertIsNotNone(committed)
                        self.assertTrue(ends[0]['committed'])
                        self.assertEqual(ends[0]['source_seq'], committed.seq)
                        self.assertEqual(ends[0]['time'], committed.time)
                        for key, value in record.error.items():
                            self.assertEqual(ends[0][key], value)
                            self.assertEqual(errors[0][key], value)
                            self.assertEqual(committed.data['reason']['error'][key], value)
                        self.assertEqual(transport.requests, [])
                        self.assertIs(app.runtime.submission(session.id, 'once'), record)
                        self.assertEqual(len(ends), 1)
                    finally:
                        await app.close()
