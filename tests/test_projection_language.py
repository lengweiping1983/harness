"""模型结果省略说明遵循回合语言并保留字符预算与原件身份。"""
import hashlib
import unittest
from harness.core.language import current_language
from harness.session.tool_projection import project_tool_result
from harness.session.tool_result_pruner import resolve_budget, prune_texts


class ProjectionLanguageTests(unittest.TestCase):
    def test_budget_errors_follow_language_and_preserve_parameter_names(self):
        for language in ('en', 'zh-CN'):
            with current_language.set(language):
                for args, parameter in (((0, 0, 0), 'thresholdChars'),
                                        ((100, -1, 0), 'headChars'),
                                        ((1, 1, 1), 'tailChars')):
                    with self.assertRaises(ValueError) as caught:
                        resolve_budget(*args)
                    text = str(caught.exception)
                    self.assertIn(parameter, text)
                    self.assertEqual(any('\u4e00' <= c <= '\u9fff' for c in text), language == 'zh-CN')

    def test_projection_retains_evidence_within_budget(self):
        text = 'original evidence ' * 200
        digest = hashlib.sha256(text.encode()).hexdigest()
        for language in ('en', 'zh-CN'):
            with current_language.set(language):
                for call in ('fixture', 'long' * 300):
                    result = project_tool_result(text, name='read', call_id=call, max_chars=512)
                    body = result['model_content']
                    self.assertLessEqual(len(body), 512)
                    self.assertIn('read_tool_result', body)
                    self.assertIn(digest, body)
                    self.assertEqual(any('\u4e00' <= c <= '\u9fff' for c in body), language == 'zh-CN')
                    self.assertEqual(result['projection']['original_chars'], len(text))

    def test_pruning_uses_runtime_language_and_shared_safe_budget(self):
        budget = resolve_budget(100, 20, 20)
        for language in ('en', 'zh-CN'):
            with current_language.set(language):
                result = ''.join(prune_texts(['a' * 300], budget))
                self.assertLessEqual(len(result), 100)
                self.assertEqual(any('\u4e00' <= c <= '\u9fff' for c in result), language == 'zh-CN')
                self.assertTrue(result.startswith('a' * 20) and result.endswith('a' * 20))

    def test_reader_errors_and_schema_follow_language(self):
        from harness.tools.builtin.tool_result_tools import read_tool_result, build_tool_result_reader
        from harness.core.errors import ToolExecutionError
        for language in ('en', 'zh-CN'):
            with current_language.set(language):
                with self.assertRaises(ToolExecutionError) as caught:
                    read_tool_result(None, 'fixture', offset=-1)
                self.assertEqual(any('\u4e00' <= c <= '\u9fff' for c in caught.exception.message), language == 'zh-CN')
                description = build_tool_result_reader().schema.description
                self.assertEqual(any('\u4e00' <= c <= '\u9fff' for c in description), language == 'zh-CN')
                self.assertIn('offset', description)


class TinyProjectionOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def test_tiny_prune_keeps_retrieval_marker_then_supported_budget_can_shrink(self):
        from harness.session.events import AppendOp
        from harness.session.session import Session
        from harness.session.tool_projection import model_result_content
        from harness.session.tool_result_pruner import ToolResultPruner
        from harness.tools.builtin.tool_result_tools import read_tool_result
        original = 'Original 原文😀\n' * 700
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                session = Session()
                await session.start_turn()
                await session.append('assistant/message', {'content': [
                    {'type': 'tool_use', 'id': 'fixture', 'name': 'read', 'input': {}}]}, surface_op=AppendOp())
                projection = project_tool_result(original, name='read', call_id='fixture', max_chars=1200)
                await session.append('tool/result', {'name': 'read', 'results': [
                    {'tool_use_id': 'fixture', 'content': original, 'is_error': True, **projection}]}, surface_op=AppendOp())
                before = len(session.events)
                tiny = ToolResultPruner(resolve_budget(100, 20, 20))
                self.assertFalse((await tiny.prune_session(session)).changed)
                self.assertEqual(len(session.events), before)
                item = session.latest('tool/result').data['results'][0]
                self.assertEqual(model_result_content(item), projection['model_content'])
                self.assertIn('read_tool_result', model_result_content(item))
                normal = ToolResultPruner(resolve_budget(512, 64, 64))
                self.assertTrue((await normal.prune_session(session)).changed)
                item = session.latest('tool/result').data['results'][0]
                self.assertLessEqual(len(model_result_content(item)), 512)
                self.assertIn('read_tool_result', model_result_content(item))
                self.assertEqual(item['content'], original)
                self.assertTrue(item['is_error'])
                chunks, offset = [], 0
                while True:
                    page = read_tool_result(session, 'fixture', offset=offset, limit=4096)
                    chunks.append(page['text'])
                    self.assertEqual(page['sha256'], hashlib.sha256(original.encode()).hexdigest())
                    self.assertTrue(page['is_error'])
                    if page['next_offset'] is None:
                        break
                    offset = page['next_offset']
                self.assertEqual(''.join(chunks), original)
