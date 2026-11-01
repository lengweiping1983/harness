"""成员恢复与归档错误保留资源身份，读取语言不能重放任务。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock

from harness.core.errors import PersistenceError
from harness.core.language import current_language
from harness.core.runs import AgentRun
from harness.i18n import tr
from harness.subagent.archive import SubagentArchive
from harness.subagent.manager import SubagentManager


class SubagentErrorLanguageTests(unittest.IsolatedAsyncioTestCase):
    def test_recovered_body_and_summary_resolve_at_read_time(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = SubagentArchive(Path(directory) / 'runs.sqlite3')
            archive.save(AgentRun(id='member', parent_session_id='owner', status='running'))
            with current_language.set('zh-CN'):
                archive = SubagentArchive(archive.path)
            manager = SubagentManager(adapter=None, tools=None, prompts=None)
            manager.archive = archive
            for language in ('en', 'zh-CN', 'en'):
                with current_language.set(language):
                    state = manager.get('member')
                    self.assertEqual(state.error_message, tr('ui.agent.restarted'))
                    self.assertEqual(state.to_dict()['error_copy'], {'key': 'ui.agent.restarted'})
                    self.assertEqual(manager.summary('member')['error'], tr('ui.agent.restarted'))
                    self.assertEqual(manager.history_page('owner')['agents'][0]['error'], tr('ui.agent.restarted'))
            with archive.db() as db:
                record = json.loads(db.execute('SELECT data FROM runs').fetchone()[0])
                self.assertEqual(record['error']['copy'], {'key': 'ui.agent.restarted'})
                self.assertEqual(record['status'], 'interrupted')

    def test_archive_failure_retains_resource_and_success_clears_it(self):
        manager = SubagentManager(adapter=None, tools=None, prompts=None)
        manager.archive = Mock()
        manager.archive.save.side_effect = OSError('Original archive 原文')
        state = AgentRun(id='member', parent_session_id='owner')
        with current_language.set('zh-CN'):
            with self.assertRaises(PersistenceError) as error:
                manager._save(state, required=True)
        self.assertEqual(error.exception.details['copy'], {'key': 'ui.agent.archiveFailed'})
        self.assertEqual(state.to_dict()['archive_error_copy'], {'key': 'ui.agent.archiveFailed'})
        self.assertNotIn('archive_error_copy', state.storage_dict())
        manager.archive.save.side_effect = None
        manager._save(state)
        self.assertNotIn('archive_error', state.to_dict())
        self.assertNotIn('archive_error_copy', state.to_dict())

    def test_handle_projection_and_snapshot_resources_do_not_alias_state(self):
        manager = SubagentManager(adapter=None, tools=None, prompts=None)
        manager.archive = Mock()
        manager.archive.load.return_value = AgentRun(id='member', parent_session_id='owner', status='running')
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                state = manager.get('member')
                self.assertEqual(state.error_message, tr('ui.agent.handleReleased'))
                snapshot = state.to_dict()
                snapshot['error_copy']['key'] = 'external-change'
                self.assertEqual(state.error['copy']['key'], 'ui.agent.handleReleased')
        original = 'Original external 用户原文 {value}'
        state = AgentRun(id='external', parent_session_id='owner', error={'message': original})
        with current_language.set('en'):
            self.assertEqual(state.error_message, original)
            self.assertNotIn('error_copy', state.to_dict())

    async def test_default_interrupt_and_shutdown_keep_copy_but_user_reason_stays_original(self):
        for operation, key in (('interrupt', 'ui.agent.parentRequested'), ('shutdown', 'ui.agent.sessionClosed'), ('user', None)):
            with self.subTest(operation=operation):
                manager = SubagentManager(adapter=None, tools=None, prompts=None)
                state = AgentRun(id='member', session_id='child', parent_session_id='owner', status='pending')
                manager._states[state.id] = state
                manager._by_parent['owner'] = [state.id]
                with current_language.set('zh-CN'):
                    if operation == 'shutdown':
                        await manager.shutdown('owner')
                    else:
                        await manager.interrupt(state.id, reason='Original 用户原因 {name}' if operation == 'user' else None)
                for language in ('en', 'zh-CN', 'en'):
                    with current_language.set(language):
                        self.assertEqual(state.error_message, tr(key) if key else 'Original 用户原因 {name}')
                        self.assertEqual(state.to_dict().get('error_copy'), {'key': key} if key else None)
                self.assertEqual(state.status, 'interrupted')
                self.assertTrue(state.settled)

    def test_combined_error_preserves_resource_and_order_in_full_and_bounded_views(self):
        from harness.subagent.archive import subagent_summary
        manager = SubagentManager(adapter=None, tools=None, prompts=None)
        state = AgentRun(id='member', parent_session_id='owner', status='failed',
            error={'code': 'run/restarted', 'message': 'Original stored translation', 'copy': {'key': 'ui.agent.restarted'}})
        with current_language.set('zh-CN'), self.assertLogs('harness.subagent.manager', level='ERROR'):
            manager._failed(state, PersistenceError('Original stored archive translation', copy={'key': 'ui.agent.archiveFailed'}), 'fixture failed %s')
            manager._failed(state, OSError('Original cleanup 原文' + 'x' * 4000), 'fixture failed %s')
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                rendered = state.error_message
                self.assertLess(rendered.index(tr('ui.agent.restarted')), rendered.index(tr('ui.agent.archiveFailed')))
                self.assertLess(rendered.index(tr('ui.agent.archiveFailed')), rendered.index('Original cleanup 原文'))
                snapshot = subagent_summary(state)
                self.assertLessEqual(sum(len(part['message']) for part in snapshot['error_parts'] if isinstance(part['message'], str)), 2000)
                self.assertEqual(snapshot['error_parts'][0]['message'], {'key': 'ui.agent.restarted'})
                snapshot['error_parts'][0]['message']['key'] = 'external-change'
                self.assertEqual(state.error['parts'][0]['message']['key'], 'ui.agent.restarted')
        self.assertIn('x' * 4000, state.error_message)

    async def test_nested_execution_resource_survives_finalization_and_archive_reads(self):
        from copy import deepcopy
        from types import SimpleNamespace
        from harness.core.runs import turn_outcome
        with tempfile.TemporaryDirectory() as directory:
            manager = SubagentManager(adapter=None, tools=None, prompts=None)
            manager.archive = SubagentArchive(Path(directory) / 'runs.sqlite3')
            with current_language.set('zh-CN'):
                original = PersistenceError(tr('ui.agent.archiveFailed'), copy={'key': 'ui.agent.archiveFailed'})
                error = original.to_dict()
                baseline = deepcopy(error)
                outcome, failure = turn_outcome(SimpleNamespace(reason='error', error_info=error, error=None))
                session = SimpleNamespace(close=AsyncMock(side_effect=OSError('Original cleanup 原文 {value}')),
                                          agent_messages=SimpleNamespace(pending=lambda: []))
                state = AgentRun(id='nested', parent_session_id='owner', status=outcome, error=failure, session=session)
                with self.assertLogs('harness.subagent.manager', level='ERROR'):
                    await manager._finalization(state)
            session.close.assert_awaited_once()
            self.assertTrue(state.settled)
            self.assertEqual(error, baseline)
            for language in ('en', 'zh-CN', 'en'):
                with current_language.set(language):
                    views = [state.to_dict(), manager.get('nested').to_dict(), manager.summary('nested'),
                             manager.history_page('owner')['agents'][0]]
                    for view in views:
                        self.assertEqual(view['error_parts'][0]['message'], {'key': 'ui.agent.archiveFailed'})
                        self.assertIn(tr('ui.agent.archiveFailed'), view['error'])
                        self.assertIn('Original cleanup 原文 {value}', view['error'])
                        self.assertLess(view['error'].index(tr('ui.agent.archiveFailed')),
                                        view['error'].index('Original cleanup 原文 {value}'))
