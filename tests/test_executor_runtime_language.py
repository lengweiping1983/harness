"""同一独立执行器的说明、限制和实际拒绝路径按调用语言解析。"""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from harness.core.errors import HarnessError
from harness.core.language import current_language
from harness.execution.config import ExecutionConfig, ProjectConfig
from harness.execution.runtime import ExecutionRuntime


class ExecutorRuntimeLanguageTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_runtime_context_and_errors_follow_language(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            project=root/'project'
            project.mkdir()
            runtime=ExecutionRuntime(ExecutionConfig(root/'state',{'fixture':ProjectConfig(project)}))
            try:
                contracts=[]
                for language in ('en','zh-CN','en'):
                    with current_language.set(language):
                        result=await runtime.call('local','harness_context',{})
                        self.assertFalse(result['is_error'])
                        payload=result['data']
                        for tool in ('harness_job_poll', 'harness_verify', 'harness_finalize', 'harness_session_open', 'harness_read'):
                            self.assertIn(tool, payload['tools'])
                            self.assertIn(tool, payload['instructions'])
                        for value in [payload['instructions'],*payload['limitations']]:
                            self.assertEqual(any('\u4e00' <= c <= '\u9fff' for c in value),language=='zh-CN')
                        contracts.append((payload['mode'],payload['tools'],payload['projects']))
                        denied=await runtime.call('', 'harness_context', {})
                        self.assertEqual(denied['data']['error']['code'],'execution/identity-required')
                        self.assertEqual(any('\u4e00' <= c <= '\u9fff' for c in denied['data']['error']['message']),language=='zh-CN')
                        with self.assertRaises(HarnessError) as error:
                            runtime._project('fixture','other')
                        self.assertEqual(error.exception.code,'execution/project-denied')
                        self.assertEqual(any('\u4e00' <= c <= '\u9fff' for c in error.exception.message),language=='zh-CN')
                        ctx=SimpleNamespace(session_id='fixture',services={})
                        runtime._policy=lambda args, ctx:(None,runtime.config.projects['fixture'],None)
                        with self.assertRaises(HarnessError) as error:
                            await runtime._verify({'verification_id':'unknown'},ctx)
                        self.assertEqual(error.exception.code,'execution/verification-denied')
                        self.assertIn('config.json',error.exception.message)
                        self.assertEqual(any('\u4e00' <= c <= '\u9fff' for c in error.exception.message),language=='zh-CN')
                self.assertEqual(contracts[0],contracts[1])
                self.assertEqual(contracts[0],contracts[2])
            finally:
                await runtime.close()
