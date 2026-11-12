"""OAuth 收尾失败清除挂起状态，不重放授权或删除已有凭据。"""
import asyncio
import threading
import unittest
from unittest.mock import AsyncMock, Mock, patch
from harness.connectors.manager import ConnectorManager
from harness.connectors.store import ConnectorRecord, ConnectorStore
from harness.connectors.credentials import CredentialStore


class ConnectorOAuthFinalizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_each_finalization_failure_exits_pending_without_replaying(self):
        for stage in ('read', 'credentials', 'bind', 'connect'):
            with self.subTest(stage=stage):
                manager = object.__new__(ConnectorManager)
                record = ConnectorRecord('fixture', transport='streamable-http', metadata={'oauth': {'scopes': []}})
                manager.store = Mock()
                manager.store.get.return_value = record
                manager.store.set_bound.return_value = record
                manager.credentials = Mock()
                manager._oauth_epochs = {'fixture': 0}
                manager._oauth_lock = threading.RLock()
                manager._oauth_pending = {'fixture': {'state': 'pending'}}
                manager._oauth_error = {}
                manager._connect_and_register = AsyncMock()
                manager._emit_changed = AsyncMock()
                if stage == 'read':
                    manager.store.get.side_effect = RuntimeError('Fixture read failure')
                elif stage == 'credentials':
                    manager.credentials.set.side_effect = RuntimeError('Fixture storage failure')
                elif stage == 'bind':
                    manager.store.set_bound.side_effect = RuntimeError('Fixture binding failure')
                else:
                    manager._connect_and_register.side_effect = RuntimeError('Fixture connection failure')
                tokens = Mock()
                tokens.to_dict.return_value = {'access_token': 'fixture'}
                with patch('harness.connectors.manager.run_authorization', new_callable=AsyncMock,
                           return_value=(tokens, 'client', 'issuer')) as authorize:
                    await manager._oauth_login_task('fixture', False, 0)
                self.assertNotIn('fixture', manager._oauth_pending)
                self.assertIn('fixture', manager._oauth_error)
                manager._emit_changed.assert_awaited_once_with('connector/oauth-error', 'fixture')
                self.assertEqual(authorize.await_count, 0 if stage == 'read' else 1)
                manager.credentials.delete.assert_not_called()
                if stage == 'credentials':
                    manager.store.set_bound.assert_not_called()

    async def test_cancellation_during_finalization_clears_pending_and_propagates(self):
        manager = object.__new__(ConnectorManager)
        manager.store = Mock()
        manager.store.get.return_value = ConnectorRecord('fixture', metadata={'oauth': {}})
        manager.store.set_bound.return_value = manager.store.get.return_value
        manager.credentials = Mock()
        manager._oauth_epochs = {'fixture': 0}
        manager._oauth_lock = threading.RLock()
        manager._oauth_pending = {'fixture': {'state': 'pending'}}
        manager._connect_and_register = AsyncMock(side_effect=asyncio.CancelledError())
        tokens = Mock()
        tokens.to_dict.return_value = {}
        with patch('harness.connectors.manager.run_authorization', new_callable=AsyncMock,
                   return_value=(tokens, 'client', 'issuer')):
            with self.assertRaises(asyncio.CancelledError):
                await manager._oauth_login_task('fixture', False, 0)
        self.assertNotIn('fixture', manager._oauth_pending)
        manager.credentials.delete.assert_not_called()

    async def test_late_authorization_cannot_write_to_replaced_or_deleted_declaration(self):
        for change in ('delete', 'replace', 'disable', 'unchanged'):
            with self.subTest(change=change):
                manager = object.__new__(ConnectorManager)
                manager.store = ConnectorStore()
                original = ConnectorRecord('fixture', transport='streamable-http', url='https://original.invalid', metadata={'oauth': {'scopes': []}})
                manager.store.upsert(original)
                manager.credentials = CredentialStore(backend='memory')
                manager.credentials.set('fixture', {'access_token': 'existing-fixture'})
                manager._oauth_epochs = {'fixture': 0}
                manager._oauth_lock = threading.RLock()
                manager._oauth_pending = {'fixture': {'state': 'pending'}}
                manager._oauth_error = {}
                manager._connect_and_register = AsyncMock()
                manager._emit_changed = AsyncMock()
                entered, release = asyncio.Event(), asyncio.Event()
                tokens = Mock()
                tokens.to_dict.return_value = {'access_token': 'late-fixture'}
                async def authorize(*args, **kwargs):
                    entered.set()
                    await release.wait()
                    return tokens, 'client', 'issuer'
                with patch('harness.connectors.manager.run_authorization', side_effect=authorize) as authorization:
                    task = asyncio.create_task(manager._oauth_login_task('fixture', False, 0))
                    await asyncio.wait_for(entered.wait(), 2)
                    if change == 'delete':
                        manager.store.remove('fixture')
                    elif change == 'replace':
                        manager.store.upsert(ConnectorRecord('fixture', transport='streamable-http', url='https://replacement.invalid'))
                    elif change == 'disable':
                        manager.store.set_enabled('fixture', False)
                    release.set()
                    await asyncio.wait_for(task, 2)
                self.assertNotIn('fixture', manager._oauth_pending)
                self.assertEqual(authorization.await_count, 1)
                if change == 'unchanged':
                    self.assertEqual(manager.credentials.get('fixture')['access_token'], 'late-fixture')
                    self.assertTrue(manager.store.get('fixture').bound)
                    manager._connect_and_register.assert_awaited_once()
                else:
                    self.assertEqual(manager.credentials.get('fixture'), {'access_token': 'existing-fixture'})
                    manager._connect_and_register.assert_not_awaited()
                    manager._emit_changed.assert_awaited_once_with('connector/oauth-error', 'fixture')
                    if change != 'delete':
                        self.assertFalse(manager.store.get('fixture').bound)

    async def test_logout_rejects_late_authorization_that_survives_cancellation(self):
        """注销后授权即使忽略取消返回，也不能重建凭据或挂起状态。"""
        manager = object.__new__(ConnectorManager)
        record = ConnectorRecord('fixture', transport='streamable-http', metadata={'oauth': {}})
        manager.store = Mock()
        manager.store.get.return_value = record
        manager.credentials = Mock()
        manager._oauth_lock = threading.RLock()
        manager._oauth_epochs = {'fixture': 0}
        manager._oauth_tasks = {}
        manager._retired_oauth_tasks = set()
        manager._oauth_pending = {}
        manager._oauth_error = {}
        manager._emit_changed = AsyncMock()
        manager._connect_and_register = AsyncMock()
        entered, release = asyncio.Event(), asyncio.Event()
        tokens = Mock()
        tokens.to_dict.return_value = {'access_token': 'original'}
        async def authorize(*args, **kwargs):
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            kwargs['on_pending'](Mock(mode='device', user_code='original', verification_uri='original', authorize_url='original', started_at=1))
            return tokens, 'client', 'issuer'
        with patch('harness.connectors.manager.run_authorization', side_effect=authorize):
            task = asyncio.create_task(manager._oauth_login_task('fixture', False, 0))
            manager._oauth_tasks['fixture'] = task
            await entered.wait()
            await manager.oauth_logout('fixture')
            release.set()
            await task
        manager.credentials.delete.assert_called_once_with('fixture')
        manager.credentials.set.assert_not_called()
        manager.store.set_bound.assert_not_called()
        manager._connect_and_register.assert_not_awaited()
        self.assertEqual(manager._oauth_pending, {})
        self.assertEqual(manager._oauth_error, {})
