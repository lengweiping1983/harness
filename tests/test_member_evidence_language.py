"""同一成员证据工具随调用语言变化，运行身份与持久原件不变化。"""
import unittest
from types import SimpleNamespace

from harness.core.errors import ToolExecutionError
from harness.core.language import current_language
from harness.i18n import tr
from harness.session.events import AppendOp
from harness.session.store import SessionStore
from harness.subagent.evidence import build_member_evidence_tool


class MemberEvidenceLanguageTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_language_preserves_evidence_and_permission_boundaries(self):
        session = SessionStore().create()
        await session.append('tool/call', {'call_id': 'real-call', 'name': 'read', 'status': 'running',
            'run_id': 'actual-run', 'turn': 1, 'input': {'path': '/project/原文.txt'}, 'effect': 'read'})
        await session.append('tool/result', {'call_id': 'real-call', 'name': 'read', 'turn': 1,
            'results': [{'tool_use_id': 'real-call', 'content': 'Original evidence 原始证据😀', 'is_error': False}]}, surface_op=AppendOp())
        state = SimpleNamespace(id='actual-run', session_id=session.id, parent_session_id='parent', settled=True,
            session=session, status='completed', parent_run_id='parent-run', scope='run-scope')
        manager = SimpleNamespace(target_for=lambda *_: state, contexts=None)
        with current_language.set('zh-CN'):
            tool = build_member_evidence_tool(manager)
        originals = [event.to_dict() for event in session.events]
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                schema = (tool.schema).resolve_description(language)
                self.assertEqual(schema.description, tr('member.evidence.description'))
                self.assertEqual(schema.parameters['properties']['call_id']['description'], tr('member.evidence.call_description'))
                args = {'agent_id': 'actual-run', 'call_id': 'real-call'}
                ctx = SimpleNamespace(session_id='parent')
                result = tool.execute(args, ctx)
                self.assertEqual(result['limitation'], tr('member.evidence.limitation'))
                self.assertEqual(result['result']['text'], 'Original evidence 原始证据😀')
                self.assertEqual(result['agent_id'], 'actual-run')
                self.assertEqual(result['parent_run_id'], 'parent-run')
                for changed, key in [({'call_id': '*'}, 'call'), ({'call_id': None, 'after': True}, 'cursor')]:
                    with self.assertRaises(ToolExecutionError) as failure:
                        tool.execute({**args, **changed}, ctx)
                    self.assertEqual(failure.exception.message, tr('member.evidence.' + key))
                state.settled = False
                with self.assertRaises(ToolExecutionError) as failure:
                    tool.execute(args, ctx)
                self.assertEqual(failure.exception.message, tr('member.evidence.settled'))
                state.settled = True
                self.assertEqual([event.to_dict() for event in session.events], originals)
