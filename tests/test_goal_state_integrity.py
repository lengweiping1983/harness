"""目标恢复拒绝旧格式，保留当前证据、预算和原始持久文件。"""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from harness.core.errors import ToolError
from harness.core.language import current_language
from harness.i18n import tr
from harness.session.events import AppendOp
from harness.session.store import SessionStore
from harness.tools.builtin.goal_tools import GoalStore, GoalView, build_goal_tools


class GoalStateIntegrityTests(unittest.IsolatedAsyncioTestCase):
    async def test_current_goal_restores_disarmed_with_uncertain_usage_and_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            sessions = SessionStore(folder)
            session = sessions.create()
            await session.append('note', {'kind': 'fixture'}, ignorable=True)
            goals = GoalStore(sessions=sessions)
            goal = goals.create(session.id, objective='原始目标😀' * 600, token_budget=10000)
            self.assertTrue(goals.reserve_usage(session.id, goal.id, 'settled', 50))
            goals.settle_usage(session.id, goal.id, 'settled', 40)
            self.assertTrue(goals.reserve_usage(session.id, goal.id, 'uncertain', 80))
            goals.checkpoint_timing()
            header = Path(folder) / 'sessions' / session.id / 'header.json'
            original = header.read_bytes()
            restored_store = GoalStore(sessions=SessionStore(folder))
            restored = restored_store.get(session.id)
            self.assertEqual(restored.activation, 'disarmed')
            self.assertIsNone(restored.running_since)
            self.assertEqual(restored.objective, goal.objective)
            self.assertEqual(restored.criteria, (goal.objective,))
            self.assertEqual(restored.evidence_after, 0)
            self.assertEqual(restored.tokens_used, 40)
            self.assertEqual(restored.budget_reservations, {'uncertain': 80})
            self.assertGreaterEqual(restored.elapsed_seconds, 0)
            self.assertEqual(restored_store.snapshot(session.id)['goal']['tokensUncertain'], 80)
            self.assertEqual(header.read_bytes(), original)
            # 完成回执通过真实工具事件生成，恢复时不重新推算证据或重放操作。
            call = await session.append('tool/call', {'call_id': 'proof', 'name': 'read', 'turn': 1,
                                                     'status': 'running', 'input': {'path': '原文.txt'}}, ignorable=True)
            result = await session.append('tool/result', {'call_id': 'proof', 'name': 'read', 'turn': 1,
                'results': [{'tool_use_id': 'proof', 'is_error': False, 'content': '原始结果😀'}]}, surface_op=AppendOp())
            self.assertGreater(result.seq, call.seq)
            completed = goals.complete(session.id, goal_id=goal.id, revision=goal.revision,
                                      checks=[{'criterion': 1, 'summary': '已核对😀', 'evidence_seqs': [result.seq]}])
            reopened = GoalStore(sessions=SessionStore(folder)).get(session.id)
            self.assertEqual(reopened.completion, completed.completion)
            self.assertEqual(reopened.phase, 'complete')
            self.assertEqual(reopened.budget_reservations, {'uncertain': 80})

    async def test_invalid_current_fields_never_rewrite_or_cache_a_goal(self):
        with tempfile.TemporaryDirectory() as folder:
            sessions = SessionStore(folder)
            session = sessions.create()
            GoalStore(sessions=sessions).create(session.id, objective='Original objective')
            header = Path(folder) / 'sessions' / session.id / 'header.json'
            raw = json.loads(header.read_text())['metadata']['goal']
            variants = [[], 'old goal']
            variants.extend({key: value for key, value in raw.items() if key != missing} for missing in raw)
            for key, value in [('extra', 1), ('activation', 'armed'), ('running_since', 1), ('phase', []),
                               ('revision', True), ('rounds_started', -1), ('evidence_after', -2),
                               ('max_goal_rounds', 0), ('token_budget', True), ('tokens_used', '5'),
                               ('created_at', -1), ('elapsed_seconds', False), ('elapsed_seconds', 10 ** 400), ('created_at', 10 ** 400),
                               ('retry_at', 'now'),
                               ('retry_count', -1), ('retry_error', []), ('stop_reason', {}),
                               ('blocked_reason', {'message': 'incomplete'}), ('criteria', []),
                               ('criteria', ['same', 'same']), ('criteria', [{}]),
                               ('budget_reservations', {'request': True}), ('completion', [{}])]:
                variants.append({**raw, key: value})
            receipt = {'criterion': 1, 'summary': 'Verified', 'evidence': [
                {'sourceSeq': 1, 'callId': 'proof', 'name': 'read', 'input': '{}', 'preview': 'original', 'truncated': False}]}
            for key, value in [('criterion', True), ('summary', ''), ('evidence', []), ('extra', 'old')]:
                variants.append({**raw, 'completion': [{**receipt, key: value}]})
            for key, value in [('sourceSeq', -1), ('callId', ''), ('input', {}), ('truncated', 'false')]:
                changed_receipt = {**receipt, 'evidence': [{**receipt['evidence'][0], key: value}]}
                variants.append({**raw, 'completion': [changed_receipt]})
            for language in ('en', 'zh-CN'):
                with current_language.set(language):
                    for changed in variants:
                        with self.subTest(language=language, changed=changed):
                            sessions.update_header(session.id, metadata={'goal': changed})
                            original = header.read_bytes()
                            store = GoalStore(sessions=SessionStore(folder))
                            for operation in (lambda: store.get(session.id),
                                              lambda: store.create(session.id, objective='Replacement')):
                                with self.assertRaises(ToolError) as failure:
                                    operation()
                                self.assertEqual(failure.exception.code, 'GOAL_INVALID_STATE')
                                self.assertEqual(failure.exception.message, tr('goal.storage.invalid'))
                                self.assertEqual(header.read_bytes(), original)
                                self.assertNotIn(session.id, store._goals)
            for version in (None, True, 0, '1'):
                sessions.update_header(session.id, metadata={'goal': raw, 'goal_state_version': version})
                original = header.read_bytes()
                with self.assertRaises(ToolError) as failure:
                    GoalStore(sessions=SessionStore(folder)).get(session.id)
                self.assertEqual(failure.exception.code, 'GOAL_INVALID_STATE')
                self.assertEqual(header.read_bytes(), original)
            sessions.update_header(session.id, metadata={'goal': None, 'goal_state_version': 1})
            self.assertIsNone(GoalStore(sessions=SessionStore(folder)).get(session.id))

    async def test_same_views_and_tools_follow_active_language(self):
        tools = {tool.schema.name: tool for tool in build_goal_tools(GoalStore())}
        bridge = SimpleNamespace(session=SimpleNamespace(id='main'))
        ctx = SimpleNamespace(session_id='member', services={'team_goal': bridge})
        labels = {'active': 'dc9591e56d50', 'paused': 'eb0c326b60ae',
                  'blocked': 'ece5ef215809', 'complete': 'f28461bb49c8'}
        views = [GoalView(id='goal-fixture', revision=1, objective='原始正文😀', phase=phase, max_goal_rounds=None)
                 for phase in labels]
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                for view in views:
                    self.assertIn(tr('python.goal_tools.' + labels[view.phase]), view.describe())
                    self.assertIn('原始正文😀', view.describe())
                for name, args, key in [('create_goal', {'objective': 'Original'}, 'goal.team.bound'),
                                         ('update_goal', {'action': 'pause'}, 'goal.team.controls')]:
                    with self.assertRaises(ToolError) as failure:
                        await tools[name].execute(args, ctx)
                    self.assertEqual(failure.exception.code, 'GOAL_TEAM_READ_ONLY')
                    self.assertEqual(failure.exception.message, tr(key))

    async def test_complete_state_requires_all_criteria_and_preserves_invalid_header(self):
        with tempfile.TemporaryDirectory() as folder:
            sessions = SessionStore(folder)
            session = sessions.create()
            GoalStore(sessions=sessions).create(
                session.id, objective='必须有完成证据', success_criteria=['第一项', '第二项'])
            header = Path(folder) / 'sessions' / session.id / 'header.json'
            raw = json.loads(header.read_text(encoding='utf-8'))['metadata']['goal']
            raw['phase'] = 'complete'
            raw['completion'] = []
            sessions.update_header(session.id, metadata={'goal': raw})
            original = header.read_bytes()
            with self.assertRaises(ToolError) as failure:
                GoalStore(sessions=SessionStore(folder)).get(session.id)
            self.assertEqual(failure.exception.code, 'GOAL_INVALID_STATE')
            self.assertEqual(header.read_bytes(), original)

    async def test_complete_state_rechecks_event_backed_evidence_before_restore(self):
        with tempfile.TemporaryDirectory() as folder:
            sessions = SessionStore(folder)
            session = sessions.create()
            store = GoalStore(sessions=sessions)
            goal = store.create(session.id, objective='证据必须来自真实事件')
            await session.append('tool/call', {'call_id': 'proof', 'name': 'read', 'turn': 1,
                                               'status': 'running', 'input': {'path': 'source.txt'}}, ignorable=True)
            result = await session.append('tool/result', {'call_id': 'proof', 'name': 'read', 'turn': 1,
                'results': [{'tool_use_id': 'proof', 'is_error': False, 'content': '真实结果'}]}, surface_op=AppendOp())
            store.complete(session.id, goal_id=goal.id, revision=goal.revision,
                           checks=[{'criterion': 1, 'summary': '已核对', 'evidence_seqs': [result.seq]}])
            header = Path(folder) / 'sessions' / session.id / 'header.json'
            raw = json.loads(header.read_text(encoding='utf-8'))['metadata']['goal']
            raw['completion'][0]['evidence'][0]['preview'] = '伪造结果'
            sessions.update_header(session.id, metadata={'goal': raw})
            original = header.read_bytes()
            with self.assertRaises(ToolError) as failure:
                GoalStore(sessions=SessionStore(folder)).get(session.id)
            self.assertEqual(failure.exception.code, 'GOAL_INVALID_STATE')
            self.assertEqual(header.read_bytes(), original)

    async def test_restore_rejects_block_without_reason_and_over_reserved_budget(self):
        with tempfile.TemporaryDirectory() as folder:
            sessions = SessionStore(folder)
            session = sessions.create()
            GoalStore(sessions=sessions).create(session.id, objective='状态不变量')
            header = Path(folder) / 'sessions' / session.id / 'header.json'
            base = json.loads(header.read_text(encoding='utf-8'))['metadata']['goal']
            for changed in (
                {**base, 'phase': 'blocked'},
                {**base, 'token_budget': 1, 'tokens_used': 1, 'budget_reservations': {'request': 1}},
            ):
                with self.subTest(changed=changed):
                    sessions.update_header(session.id, metadata={'goal': changed})
                    original = header.read_bytes()
                    with self.assertRaises(ToolError) as failure:
                        GoalStore(sessions=SessionStore(folder)).get(session.id)
                    self.assertEqual(failure.exception.code, 'GOAL_INVALID_STATE')
                    self.assertEqual(header.read_bytes(), original)

    async def test_goal_snapshot_exposes_derived_limit_status_without_changing_phase(self):
        store = GoalStore()
        active = store.create('status-active', objective='active')
        self.assertEqual(store.snapshot('status-active')['goal']['status'], 'active')

        paused = store.pause('status-active', goal_id=active.id, revision=active.revision)
        self.assertEqual(paused.phase, 'paused')
        self.assertEqual(store.snapshot('status-active')['goal']['status'], 'paused')

        store = GoalStore()
        limited = store.create('status-budget', objective='budget')
        store.disarm('status-budget', 'token-budget', goal_id=limited.id)
        self.assertEqual(store.get('status-budget').phase, 'active')
        self.assertEqual(store.snapshot('status-budget')['goal']['status'], 'budget_limited')

        store = GoalStore()
        usage = store.create('status-usage', objective='usage')
        store.disarm('status-usage', 'usage-limited', goal_id=usage.id)
        self.assertEqual(store.snapshot('status-usage')['goal']['status'], 'usage_limited')

        blocked = GoalView(id='blocked', revision=1, objective='blocked', phase='blocked', max_goal_rounds=None,
                           activation='disarmed', blocked_reason={'code': 'model-reported', 'message': '等待输入'})
        complete = GoalView(id='complete', revision=1, objective='complete', phase='complete', max_goal_rounds=None,
                            activation='disarmed')
        self.assertEqual(blocked.status, 'blocked')
        self.assertEqual(complete.status, 'complete')
