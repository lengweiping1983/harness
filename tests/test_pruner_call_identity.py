"""重复调用 ID 的图片身份不能跨表面位置传播。"""
import unittest

from harness.session.events import AppendOp, ReplaceOp
from harness.session.session import Session
from harness.session.tool_result_pruner import ToolResultPruner, resolve_budget


class PrunerCallIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def add_call(self, session, name, text):
        await session.append('assistant/message', {'content': [
            {'type': 'tool_use', 'id': 'same', 'name': name, 'input': {}}]}, surface_op=AppendOp())
        return await session.append('tool/result', {'name': name, 'results': [
            {'tool_use_id': 'same', 'content': text, 'is_error': False}]}, surface_op=AppendOp())

    async def test_reused_ids_use_only_their_preceding_call_identity(self):
        text = 'data:image/png;base64,' + 'A' * 2000
        for names in (('read_image', 'shell'), ('shell', 'read_image')):
            with self.subTest(names=names):
                session = Session()
                await session.start_turn()
                events = [await self.add_call(session, name, text) for name in names]
                pruner = ToolResultPruner(resolve_budget(512, 64, 64))
                result = await pruner.prune_session(session)
                expected = events[names.index('shell')]
                self.assertEqual([row.original_seq for row in result.pruned], [expected.seq])
                image = events[names.index('read_image')]
                self.assertIn(image, session.surface)
                self.assertEqual(image.data['results'][0]['content'], text)
                self.assertEqual(session.event_at(expected.seq).data['results'][0]['content'], text)
                self.assertFalse((await pruner.prune_session(session)).changed)

    async def test_replacement_keeps_original_round_for_image_retention(self):
        session = Session()
        await session.start_turn()
        original = await self.add_call(session, 'read_image', 'data:image/png;base64,' + 'A' * 2000)
        await session.append('turn/end', {'turn': 1, 'reason': {'kind': 'completed'}})
        await session.start_turn()
        replacement = await session.append('tool/result', original.data,
            surface_op=ReplaceOp(original.seq, original.seq), source_event_seqs=[original.seq])
        pruner = ToolResultPruner(resolve_budget(512, 64, 64))
        result = await pruner.prune_session(session)
        self.assertEqual([row.original_seq for row in result.pruned], [replacement.seq])
        current = next(event for event in session.surface if event.type == 'tool/result')
        self.assertNotIn('base64,', current.data['results'][0]['content'])
        self.assertIn('base64,', original.data['results'][0]['content'])
