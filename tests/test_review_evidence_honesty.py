"""自动审阅证据必须标明请求方身份与截断事实，不得把样本当完整授权。"""
import json
import unittest
from types import SimpleNamespace

from harness.app.permissions import MAX_REVIEW_USER_MESSAGES
from harness.core.language import current_language
from harness.core.runs import AgentRun
from harness.session.events import AppendOp

from tests.test_permission_sensitive import permission_fixture


class ReviewEvidenceHonestyTests(unittest.IsolatedAsyncioTestCase):
    async def review_payload(self, host, policy, session, context, *, messages=1):
        for index in range(messages):
            await session.append('user/message', {'text': f'用户意图 {index}', 'source': 'user'},
                                 surface_op=AppendOp())
        host.adapter.complete.return_value = SimpleNamespace(
            text=json.dumps({'decision': 'ask', 'reason': '需要人工确认'}), has_tool_use=False, usage=None)
        tool = SimpleNamespace(schema=SimpleNamespace(name='bash'), effect='execute', dangerous=False,
                               approval_required=False)
        with current_language.set('zh-CN'):
            await policy._review(tool, {'command': 'ls'}, context, session.id, None)
        return json.loads(host.adapter.complete.await_args.args[0][1].text)

    async def test_truncated_user_evidence_is_declared_with_its_true_total(self):
        host, policy, session, _, _, context, _ = permission_fixture('auto_review')
        total = MAX_REVIEW_USER_MESSAGES + 3
        payload = await self.review_payload(host, policy, session, context, messages=total)
        self.assertEqual(payload['user_messages_total'], total)
        self.assertTrue(payload['user_messages_incomplete'])
        self.assertEqual(len(payload['user_messages']), MAX_REVIEW_USER_MESSAGES)
        # 保留的必须是有界的最近样本，且截断事实可核对。
        self.assertIn(f'用户意图 {total - 1}', json.dumps(payload['user_messages'], ensure_ascii=False))
        self.assertNotIn('用户意图 0"', json.dumps(payload['user_messages'], ensure_ascii=False))

    async def test_complete_evidence_is_declared_complete(self):
        host, policy, session, _, _, context, _ = permission_fixture('auto_review')
        payload = await self.review_payload(host, policy, session, context, messages=2)
        self.assertEqual(payload['user_messages_total'], 2)
        self.assertFalse(payload['user_messages_incomplete'])
        self.assertEqual(payload['requester'], {'kind': 'root', 'depth': 0})

    async def test_delegated_requester_is_labelled_with_its_depth_and_boundaries(self):
        host, policy, session, _, _, context, _ = permission_fixture('auto_review')
        member = AgentRun(id='sub_fixture', status='running', parent_session_id='root-session',
                          read_only=True, runner=None, depth=2)
        member.session_id = session.id
        host.subagents = SimpleNamespace(state_for_session=lambda identifier: member if identifier == session.id else None,
                                         list_states=lambda: [member])
        payload = await self.review_payload(host, policy, session, context, messages=1)
        self.assertEqual(payload['requester'], {'kind': 'subagent', 'depth': 2, 'agent_id': 'sub_fixture',
                                                'read_only': True, 'runner': None,
                                                'parent_session_id': 'root-session'})

    async def test_review_policy_explains_provenance_and_incompleteness(self):
        from harness.i18n import tr
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                policy_text = tr('owned.permissions.1d72dffc9a0f')
            self.assertIn('user_messages_incomplete', policy_text)
            self.assertIn('requester.kind', policy_text)
            self.assertIn('subagent', policy_text)
            # 返回格式约定必须仍是最后一条指令。
            self.assertIn('decision', policy_text.splitlines()[-1] + policy_text[-200:])


if __name__ == '__main__':
    unittest.main()
