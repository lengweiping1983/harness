"""统一 Work 阶段报告协议的边界验证。"""
import unittest
from harness.app.superagent_checkpoints import validate_checkpoint
from harness.core.errors import HarnessError


class CheckpointProtocolTests(unittest.TestCase):
    def test_checkpoint_rejects_ambiguous_states_and_unbounded_handoffs(self):
        valid = {'status': 'continue', 'summary': 'Completed one stage.', 'next_steps': ['Finish the next stage.']}
        for values in ({'status': True}, {'status': ['ready']}, {'summary': ''}, {'summary': '界' * 2731},
                {'next_steps': []}, {'next_steps': ['x'] * 17}, {'next_steps': ['界' * 683]}, {'next_steps': [False]}):
            with self.subTest(values=values), self.assertRaises((ValueError, HarnessError)):
                validate_checkpoint({**valid, **values})
        self.assertEqual(validate_checkpoint(valid), valid)
