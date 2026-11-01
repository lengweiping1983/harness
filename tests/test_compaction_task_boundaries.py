"""原位编辑后，压缩同时保护末尾任务与最新修订的用户消息。"""
import unittest

from harness.core.agent import _compaction_regions
from harness.session.events import AppendOp, ReplaceOp
from harness.session.store import SessionStore


class CompactionTaskBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_latest_position_and_latest_revision_remain_distinct_protected_messages(self):
        session = SessionStore().create()
        for index in range(36):
            if index in (3, 16):
                await session.append('user/message', {'text': f'Original task {index}'}, surface_op=AppendOp())
            else:
                await session.append('assistant/message', {'content': [{'type': 'text', 'text': f'Original response {index}'}]},
                                     surface_op=AppendOp())
        corrected = await session.append('user/message', {'text': 'Corrected original task'}, surface_op=ReplaceOp(3, 3))
        self.assertEqual(session.surface[3].seq, corrected.seq)
        self.assertGreater(corrected.seq, session.surface[16].seq)
        regions = _compaction_regions(session.surface)
        eligible = {event.seq for region in regions for event in region}
        self.assertNotIn(corrected.seq, eligible)
        self.assertNotIn(session.surface[16].seq, eligible)
        self.assertTrue({2, 9, 20}.issubset(eligible))
        self.assertTrue(all(event.seq not in eligible for event in session.surface[-12:]))
