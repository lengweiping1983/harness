"""相同任务描述的团队成员仍通过宿主运行身份区分。"""
import unittest

from harness.core.runs import AgentRun, COMPLETED


class TeamResultIdentityTests(unittest.TestCase):
    def test_same_description_keeps_distinct_roster_members(self):
        first = AgentRun(id='run-a', description='独立判断', status=COMPLETED,
            prompt_input={'member': 'proposal'})
        second = AgentRun(id='run-b', description='独立判断', status=COMPLETED,
            prompt_input={'member': 'critique'})
        self.assertIn('"member": "proposal"', first.summary_line())
        self.assertIn('"member": "critique"', second.summary_line())
        self.assertNotIn('"member": "critique"', first.summary_line())

    def test_ordinary_description_does_not_infer_identity(self):
        state = AgentRun(id='run', description='member=proposal', status=COMPLETED)
        self.assertNotIn('"member":', state.summary_line())
