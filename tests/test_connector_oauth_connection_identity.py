"""授权后的连接等待不能把已替换的声明报告为授权成功。"""
import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

from harness.connectors.credentials import CredentialStore
from harness.core.language import current_language
from harness.i18n import tr
from harness.connectors.manager import ConnectorManager
from harness.connectors.store import ConnectorRecord, ConnectorStore


class ConnectorOAuthConnectionIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_changed_declaration_during_connection_cannot_publish_success(self):
        for change in ('replace', 'remove', 'unchanged'):
            manager = ConnectorManager(ConnectorStore(), Mock(), Mock(), Mock(), Mock(), CredentialStore(backend='memory'))
            original = ConnectorRecord('fixture', transport='streamable-http', url='https://original.invalid',
                enabled=True, metadata={'oauth': {'scopes': []}})
            manager.store.upsert(original)
            manager._oauth_epochs['fixture'] = 1
            manager._emit_changed = AsyncMock()
            manager._register_tools = Mock(return_value=1)
            manager._unregister_tools = Mock()
            entered, release = asyncio.Event(), asyncio.Event()
            async def connect():
                entered.set()
                await release.wait()
            supervisor = Mock(connect=AsyncMock(side_effect=connect), aclose=AsyncMock())
            manager.mcp.replace = AsyncMock(return_value=supervisor)
            tokens = Mock()
            tokens.to_dict.return_value = {'access_token': 'original-token'}
            manager._apply_oauth_header = AsyncMock()
            with patch('harness.connectors.manager.run_authorization', new_callable=AsyncMock,
                    return_value=(tokens, 'client', 'issuer')) as authorize:
                task = asyncio.create_task(manager._oauth_login_task('fixture', False, 1))
                try:
                    await asyncio.wait_for(entered.wait(), 2)
                    if change == 'replace':
                        manager.store.upsert(ConnectorRecord('fixture', transport='streamable-http', url='https://replacement.invalid'))
                    elif change == 'remove':
                        manager.store.remove('fixture')
                    release.set()
                    await asyncio.wait_for(task, 2)
                finally:
                    release.set()
                    await asyncio.gather(task, return_exceptions=True)
            actions = [call.args[0] for call in manager._emit_changed.await_args_list]
            self.assertEqual(actions, ['connector/oauth-authorized'] if change == 'unchanged' else ['connector/oauth-error'])
            self.assertEqual(manager._register_tools.call_count, 1 if change == 'unchanged' else 0)
            self.assertEqual(supervisor.aclose.await_count, 0 if change == 'unchanged' else 1)
            authorize.assert_awaited_once()
            if change != 'unchanged':
                failure = manager._oauth_error['fixture']
                snapshot = failure.message
                credentials = manager.credentials.get('fixture')
                for language in ('en', 'zh-CN', 'en'):
                    with current_language.set(language):
                        status = manager._oauth_status_for(original)
                        self.assertEqual(status['state'], 'error')
                        self.assertEqual(status['message'], '[connector/oauth] ' + tr('config.validation.message_22'))
                self.assertEqual(failure.message, snapshot)
                self.assertEqual(manager.credentials.get('fixture'), credentials)
                authorize.assert_awaited_once()

    async def test_logout_during_connection_rejects_a_result_that_ignores_cancellation(self):
        manager = ConnectorManager(ConnectorStore(), Mock(), Mock(), Mock(), Mock(), CredentialStore(backend='memory'))
        manager.store.upsert(ConnectorRecord('fixture', transport='streamable-http', url='https://original.invalid',
            enabled=True, metadata={'oauth': {'scopes': []}}))
        manager._oauth_epochs['fixture'] = 1
        manager._emit_changed = AsyncMock()
        manager._register_tools = Mock(return_value=1)
        manager._unregister_tools = Mock()
        manager._refresh_prompt = Mock()
        manager._apply_oauth_header = AsyncMock()
        entered, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def connect():
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.set()
                await release.wait()

        supervisor = Mock(connect=AsyncMock(side_effect=connect), aclose=AsyncMock())
        manager.mcp.replace = AsyncMock(return_value=supervisor)
        manager.mcp.get.return_value = supervisor
        tokens = Mock()
        tokens.to_dict.return_value = {'access_token': 'original-token'}
        with patch('harness.connectors.manager.run_authorization', new_callable=AsyncMock,
                return_value=(tokens, 'client', 'issuer')) as authorize:
            task = asyncio.create_task(manager._oauth_login_task('fixture', False, 1))
            manager._oauth_tasks['fixture'] = task
            try:
                await asyncio.wait_for(entered.wait(), 2)
                self.assertEqual(manager.credentials.get('fixture')['access_token'], 'original-token')
                result = await manager.oauth_logout('fixture')
                await asyncio.wait_for(cancelled.wait(), 2)
                self.assertEqual(result, {'name': 'fixture', 'state': 'none'})
                self.assertIsNone(manager.credentials.get('fixture'))
                self.assertFalse(manager.store.get('fixture').bound)
                self.assertFalse(manager.store.get('fixture').enabled)
                release.set()
                await asyncio.wait_for(task, 2)
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)
        self.assertIsNone(manager.credentials.get('fixture'))
        self.assertEqual(manager._oauth_pending, {})
        self.assertEqual(manager._oauth_error, {})
        manager._register_tools.assert_not_called()
        self.assertNotIn('connector/oauth-authorized', [call.args[0] for call in manager._emit_changed.await_args_list])
        authorize.assert_awaited_once()
