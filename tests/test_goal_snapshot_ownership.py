"""目标 SDK 快照与用量身份不能改写其他调用方的状态。"""
import unittest
import tempfile
from pathlib import Path

from harness.session.events import AppendOp
from harness.session.store import SessionStore

from harness.tools.builtin.goal_tools import GoalStore


class GoalSnapshotOwnershipTests(unittest.TestCase):
    def test_created_read_and_updated_snapshots_do_not_share_mutable_state(self):
        store = GoalStore()
        created = store.create('owner', objective='Original objective 原文', token_budget=100)
        created.budget_reservations['foreign'] = 90
        self.assertEqual(store.get('owner').budget_reservations, {})
        store.reserve_usage('owner', created.id, 'request', 10)
        read = store.get('owner')
        read.budget_reservations.clear()
        self.assertEqual(store.get('owner').budget_reservations, {'request': 10})
        blocked = store.block('owner', goal_id=created.id, revision=read.revision, reason='Original reason 原文')
        blocked.blocked_reason['message'] = 'Changed outside store'
        self.assertEqual(store.get('owner').blocked_reason['message'], 'Original reason 原文')
        stored = store.get('owner')
        stored.blocked_reason.clear()
        self.assertEqual(store.get('owner').blocked_reason['message'], 'Original reason 原文')
        self.assertEqual(store.get('owner').revision, blocked.revision)

    def test_equal_request_ids_in_other_goals_do_not_change_reservation_certainty(self):
        store = GoalStore()
        first = store.create('first', objective='First', token_budget=100)
        second = store.create('second', objective='Second', token_budget=100)
        store.reserve_usage('first', first.id, 'same-request', 10)
        store.reserve_usage('second', second.id, 'same-request', 20)
        store.settle_usage('first', first.id, 'same-request', 7)
        self.assertEqual(store.snapshot('second')['goal']['tokensUncertain'], 0)
        self.assertEqual(store.get('second').budget_reservations, {'same-request': 20})
        store.settle_usage('first', 'old-goal', 'same-request', 99)
        self.assertEqual(store.snapshot('second')['goal']['tokensUncertain'], 0)
        store.clear('first')
        self.assertEqual(store.snapshot('second')['goal']['tokensUncertain'], 0)
        store.settle_usage('second', second.id, 'same-request', 12)
        self.assertEqual(store.get('second').tokens_used, 12)


class GoalCompletionSnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def test_completion_return_and_reloaded_nested_evidence_are_isolated(self):
        with tempfile.TemporaryDirectory() as folder:
            sessions = SessionStore(folder)
            session = sessions.create()
            store = GoalStore(sessions=sessions)
            goal = store.create(session.id, objective='Original objective')
            await session.append('tool/call', {'call_id': 'proof', 'name': 'read', 'turn': 1,
                'status': 'running', 'input': {'path': 'Original.txt'}}, ignorable=True)
            result = await session.append('tool/result', {'call_id': 'proof', 'name': 'read', 'turn': 1,
                'results': [{'tool_use_id': 'proof', 'is_error': False, 'content': 'Original evidence 原文'}]}, surface_op=AppendOp())
            completed = store.complete(session.id, goal_id=goal.id, revision=goal.revision,
                checks=[{'criterion': 1, 'summary': 'Verified 原文', 'evidence_seqs': [result.seq]}])
            header = Path(folder) / 'sessions' / session.id / 'header.json'
            original = header.read_bytes()
            completed.completion[0]['evidence'][0]['preview'] = 'Changed return'
            read = store.get(session.id)
            self.assertEqual(read.completion[0]['evidence'][0]['preview'], 'Original evidence 原文')
            read.completion[0]['evidence'].clear()
            restored = GoalStore(sessions=SessionStore(folder))
            loaded = restored.get(session.id)
            loaded.completion[0]['summary'] = 'Changed restored read'
            self.assertEqual(restored.get(session.id).completion[0]['summary'], 'Verified 原文')
            self.assertEqual(store.get(session.id).completion[0]['summary'], 'Verified 原文')
            self.assertEqual(header.read_bytes(), original)
