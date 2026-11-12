"""对话详情读取控制状态失败不能伪装成正常空态。"""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from harness.core.errors import HarnessError
from harness.session.session import Session
from harness.web.views import session_detail


class SessionViewControlFailureTests(unittest.TestCase):
    def fixture(self):
        session = Session()
        host = SimpleNamespace(get_session=Mock(return_value=session), _active_team_runs={},
            plan_mode_state=Mock(return_value={'active': False}),
            questions=SimpleNamespace(pending=Mock(return_value=[])))
        return session, host

    def test_read_failures_propagate_original_error(self):
        for field in ('plan', 'questions'):
            for view in ('full', 'conversation', 'conversation-window'):
                with self.subTest(field=field, view=view):
                    session, host = self.fixture()
                    original = HarnessError('Original diagnostic 原文 {x}', code='fixture/read-failed')
                    target = host.plan_mode_state if field == 'plan' else host.questions.pending
                    target.side_effect = original
                    with self.assertRaises(HarnessError) as error:
                        session_detail(host, session.id, view=view)
                    self.assertIs(error.exception, original)
                    self.assertEqual(session.event_count, 0)

    def test_valid_empty_and_active_snapshots_remain_distinct(self):
        session, host = self.fixture()
        for view in ('full', 'conversation', 'conversation-window'):
            for plan, questions in [({'active': False}, []), ({'active': True}, [{'id': 'Original {x}'}])]:
                with self.subTest(view=view, plan=plan):
                    host.plan_mode_state.return_value = plan
                    host.questions.pending.return_value = questions
                    result = session_detail(host, session.id, view=view)
                    self.assertEqual(result['plan_mode'], plan)
                    self.assertEqual(result['questions'], questions)

    def test_member_context_does_not_read_parent_controls(self):
        session, host = self.fixture()
        result = session_detail(host, session.id, session=session)
        self.assertNotIn('plan_mode', result)
        self.assertNotIn('questions', result)
        host.plan_mode_state.assert_not_called()
        host.questions.pending.assert_not_called()
