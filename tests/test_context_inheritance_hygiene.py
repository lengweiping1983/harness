"""继承上下文跨代理边界前必须脱敏，并如实报告继承与改写数量。"""
import json
import unittest

from harness.collaboration.context_inheritance import MAX_CONTEXT_BYTES, inherited_context
from harness.core.language import current_language
from harness.i18n import tr
from harness.session.events import AppendOp
from harness.session.session import Session

SECRET_TEXT = 'export OPENAI_API_KEY="sk-fixture1234567890" 以及 token: fixture-plain-value'


class ContextInheritanceHygieneTests(unittest.IsolatedAsyncioTestCase):
    async def test_secret_shaped_text_is_redacted_in_the_copy_but_not_in_the_source(self):
        session = Session()
        await session.append('user/message', {'text': SECRET_TEXT, 'source': 'user'}, surface_op=AppendOp())
        await session.append('assistant/message', {'text': '已记录 fixture-echo'}, surface_op=AppendOp())
        payload = inherited_context(session, 'all')
        encoded = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn('sk-fixture1234567890', encoded)
        self.assertNotIn('fixture-plain-value', encoded)
        self.assertIn('[redacted', encoded)
        # 非敏感正文照常继承，脱敏不得抹掉任务本身。
        self.assertIn('fixture-echo', encoded)
        # 父会话事件保持原件，脱敏只作用于跨边界副本。
        self.assertEqual(session.events_of_type('user/message')[0].data['text'], SECRET_TEXT)

    async def test_accounting_reports_inherited_and_rewritten_counts(self):
        session = Session()
        await session.append('user/message', {'text': 'plain instruction 原文', 'source': 'user'}, surface_op=AppendOp())
        await session.append('user/message', {'text': SECRET_TEXT, 'source': 'user'}, surface_op=AppendOp())
        payload = inherited_context(session, 'all')
        self.assertEqual(payload['message_count'], len(payload['messages']))
        self.assertEqual(payload['message_count'], 2)
        self.assertEqual(payload['redacted_messages'], 1)
        self.assertGreaterEqual(payload['redacted_hits'], 2)
        self.assertEqual(payload['source_session_id'], session.id)
        self.assertEqual(payload['fork_turns'], 'all')

    async def test_notice_and_payload_share_one_language_and_stay_parseable(self):
        session = Session()
        await session.append('user/message', {'text': SECRET_TEXT, 'source': 'user'}, surface_op=AppendOp())
        payload = inherited_context(session, 'all')
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                notice = tr('agent.inheritance.context_notice')
            self.assertTrue(notice.strip())
            # 提示词与载荷必须仍能按前缀切分后解析，供子代理与审计核对。
            self.assertEqual(json.loads(json.dumps(payload, ensure_ascii=False))['redacted_messages'], 1)
        self.assertNotIn('sk-fixture1234567890', notice)

    async def test_boundaries_stay_explicit_instead_of_silently_truncating(self):
        self.assertIsNone(inherited_context(Session(), 'none'))
        for invalid in (0, -1, True, 1.5, '2', 'all-but-one'):
            with self.assertRaises(ValueError) as caught:
                inherited_context(Session(), invalid)
            self.assertEqual(str(caught.exception), 'agent/invalid-fork-turns')
        with self.assertRaises(ValueError) as caught:
            inherited_context(None, 'all')
        self.assertEqual(str(caught.exception), 'agent/context-unavailable')
        oversized = Session()
        await oversized.append('user/message', {'text': 'x' * (MAX_CONTEXT_BYTES + 1), 'source': 'user'},
                               surface_op=AppendOp())
        with self.assertRaises(ValueError) as caught:
            inherited_context(oversized, 'all')
        self.assertEqual(str(caught.exception), 'agent/inherited-context-too-large')


if __name__ == '__main__':
    unittest.main()
