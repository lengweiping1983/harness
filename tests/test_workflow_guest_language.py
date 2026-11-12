"""真实 Node guest 使用进程语言快照，原始脚本失败及数据保持。"""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from harness.core.language import current_language
from harness.i18n import resources
from harness.tools.builtin.workflow_host import WorkflowGuest, WorkflowLimits


class WorkflowGuestLanguageTests(unittest.IsolatedAsyncioTestCase):
    async def run_script(self, script, language):
        manager = SimpleNamespace(spawn=Mock(side_effect=AssertionError('Unexpected member creation')), interrupt=Mock())
        with current_language.set(language):
            guest = WorkflowGuest(manager, script=script, meta={'name': 'fixture'}, args=None, limits=WorkflowLimits())
            result = await asyncio.wait_for(guest.run(), 5)
        self.assertEqual(guest._proc.returncode, 0)
        self.assertEqual(guest._children, {})
        self.assertEqual(guest._rpc_tasks, set())
        manager.spawn.assert_not_called()
        manager.interrupt.assert_not_called()
        return result

    async def test_same_guest_sources_localize_argument_and_materialization_failures(self):
        cases = [('return await agent("")', 'workflow.guest.87b7fb1f70dc'),
            ('return await parallel([7])', 'workflow.guest.749e654c9089'),
            ('return await pipeline([1])', 'workflow.guest.486a3e52c166'),
            ('return 1n', 'workflow.guest.7db56953de31'),
            ('const x={};x.self=x;return x', 'workflow.guest.17af60c14c77'),
            ('return await agent("fixture", {schema:{type:"object",properties:{n:{type:["string"]}}}})', 'workflow.validation.dee44a47ae35')]
        for language in ('en', 'zh-CN', 'en'):
            for script, key in cases:
                result = await self.run_script(script, language)
                self.assertEqual(result['stopReason'], 'error')
                self.assertIsNone(result['value'])
                self.assertEqual(result['agentsStarted'], 0)
                if key == 'workflow.validation.dee44a47ae35':
                    self.assertIn('schema.properties.n.type', result['error'])
                    self.assertEqual(any('\u4e00' <= c <= '\u9fff' for c in result['error']), language == 'zh-CN')
                else:
                    expected = resources(language)[key].format(p0=0)
                    self.assertIn(expected, result['error'])

    async def test_concurrent_languages_are_isolated_and_external_content_is_preserved(self):
        en, zh = await asyncio.gather(self.run_script('return await agent("")', 'en'), self.run_script('return await agent("")', 'zh-CN'))
        self.assertIn(resources('en')['workflow.guest.87b7fb1f70dc'], en['error'])
        self.assertIn(resources('zh-CN')['workflow.guest.87b7fb1f70dc'], zh['error'])
        for language in ('en', 'zh-CN'):
            result = await self.run_script('throw new Error("Original 原文")', language)
            self.assertIn('Original 原文', result['error'])
            self.assertEqual(result['stopReason'], 'error')
            result = await self.run_script('return {message:"Original 原文", n:7}', language)
            self.assertEqual(result['stopReason'], 'completed')
            self.assertEqual(result['value'], {'message': 'Original 原文', 'n': 7})

    async def test_array_materialization_rejects_data_that_would_be_silently_dropped(self):
        scripts = [
            'const x=[1,2];x["01"]="Original 原文";return x',
            'const x=[1,2];x["1e0"]="Original 原文";return x',
            'const x=[1];Object.defineProperty(x,"hidden",{value:"Original 原文"});return x',
            'Array.prototype[0]="Original 原文";return Array(1)',
        ]
        for language in ('en', 'zh-CN', 'en'):
            for script in scripts:
                result = await self.run_script(script, language)
                self.assertEqual(result['stopReason'], 'error', script)
                self.assertIsNone(result['value'])
                self.assertEqual(result['agentsStarted'], 0)
                self.assertIn(resources(language)['workflow.guest.b66917fde0fe'].split('{p0}')[0], result['error'])
            result = await self.run_script('return ["Original 原文😀", {"01":"Original", "__proto__":null}, null]', language)
            self.assertEqual(result['stopReason'], 'completed')
            self.assertEqual(result['value'], ['Original 原文😀', {'01': 'Original'}, None])
