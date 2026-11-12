"""刷新凭据并发轮换只能消费一次，失败不能撤销胜出请求的新凭据。"""
import asyncio
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from mcp.server.auth.provider import AuthorizationCode, TokenError
from mcp.shared.auth import OAuthToken

from harness.core.language import current_language
from harness.i18n import tr
from harness.mcp_server.auth import LocalOAuthProvider


class OAuthRefreshConsumptionTests(unittest.IsolatedAsyncioTestCase):
    async def fixture(self, directory):
        provider = LocalOAuthProvider(Path(directory), 'https://fixture.invalid')
        client = SimpleNamespace(client_id='fixture-client')
        with provider.db() as database:
            original = provider._issue(database, client.client_id, ['project.read', 'project.write'], 'fixture-subject')
        refresh = await provider.load_refresh_token(client, original.refresh_token)
        return provider, client, original, refresh

    async def test_concurrent_exchanges_consume_once_and_keep_winning_token_valid(self):
        for language in ('en', 'zh-CN', 'en'):
            with self.subTest(language=language), tempfile.TemporaryDirectory() as directory, current_language.set(language):
                provider, client, original, refresh = await self.fixture(directory)
                barrier = threading.Barrier(2)
                original_database = provider.db

                class SynchronizedDatabase:
                    def __enter__(self):
                        self.connection = original_database().__enter__()
                        return self

                    def execute(self, statement, values=()):
                        cursor = self.connection.execute(statement, values)
                        if statement == "SELECT family FROM tokens WHERE id=? AND kind='refresh'":
                            row = cursor.fetchone()
                            cursor.close()
                            barrier.wait(timeout=3)
                            return SimpleNamespace(fetchone=lambda: row)
                        return cursor

                    def __exit__(self, *args):
                        return self.connection.__exit__(*args)

                def exchange():
                    return asyncio.run(provider.exchange_refresh_token(client, refresh, ['project.read']))

                with patch.object(provider, 'db', side_effect=SynchronizedDatabase):
                    outcomes = await asyncio.gather(asyncio.to_thread(exchange), asyncio.to_thread(exchange), return_exceptions=True)
                winners = [value for value in outcomes if isinstance(value, OAuthToken)]
                losers = [value for value in outcomes if isinstance(value, TokenError)]
                self.assertEqual(len(winners), 1)
                self.assertEqual(len(losers), 1)
                self.assertEqual(losers[0].error, 'invalid_grant')
                self.assertEqual(losers[0].error_description, tr('execution.oauth.f414345f52fc'))
                access = await provider.load_access_token(winners[0].access_token)
                self.assertIsNotNone(access)
                self.assertEqual(access.subject, 'fixture-subject')
                self.assertEqual(access.scopes, ['project.read'])
                self.assertIsNotNone(await provider.load_refresh_token(client, winners[0].refresh_token))
                self.assertIsNone(await provider.load_refresh_token(client, original.refresh_token))
                self.assertIsNone(await provider.load_access_token(original.access_token))
                with provider.db() as database:
                    self.assertEqual(database.execute('SELECT COUNT(*) FROM tokens').fetchone()[0], 2)

    async def test_issue_failure_rolls_back_consumption_and_preserves_original_tokens(self):
        with tempfile.TemporaryDirectory() as directory:
            provider, client, original, refresh = await self.fixture(directory)
            failure = OSError('Original persistence failure')
            with patch.object(provider, '_issue', side_effect=failure):
                with self.assertRaises(OSError) as caught:
                    await provider.exchange_refresh_token(client, refresh, ['project.read'])
            self.assertIs(caught.exception, failure)
            self.assertIsNotNone(await provider.load_refresh_token(client, original.refresh_token))
            self.assertIsNotNone(await provider.load_access_token(original.access_token))
            rotated = await provider.exchange_refresh_token(client, refresh, ['project.read'])
            self.assertIsNotNone(await provider.load_access_token(rotated.access_token))

    async def test_replay_of_loaded_snapshot_cannot_revoke_current_family(self):
        with tempfile.TemporaryDirectory() as directory:
            provider, client, _, refresh = await self.fixture(directory)
            rotated = await provider.exchange_refresh_token(client, refresh, ['project.read'])
            with self.assertRaises(TokenError):
                await provider.exchange_refresh_token(client, refresh, ['project.read'])
            self.assertIsNotNone(await provider.load_access_token(rotated.access_token))
            self.assertIsNotNone(await provider.load_refresh_token(client, rotated.refresh_token))

    async def test_consumed_authorization_code_keeps_original_oauth_error(self):
        for language in ('en', 'zh-CN', 'en'):
            with self.subTest(language=language), tempfile.TemporaryDirectory() as directory, current_language.set(language):
                provider, client, _, _ = await self.fixture(directory)
                code = AuthorizationCode(code='fixture-code', client_id=client.client_id,
                    scopes=['project.read'], expires_at=time.time() + 120, code_challenge='fixture-challenge',
                    redirect_uri='http://127.0.0.1/callback', redirect_uri_provided_explicitly=True,
                    resource=provider.resource, subject='fixture-subject')
                with provider.db() as database:
                    database.execute('INSERT INTO codes VALUES(?,?)', (code.code, code.model_dump_json()))
                result = await provider.exchange_authorization_code(client, code)
                with self.assertRaises(TokenError) as caught:
                    await provider.exchange_authorization_code(client, code)
                self.assertEqual(caught.exception.error, 'invalid_grant')
                self.assertEqual(caught.exception.error_description, tr('execution.oauth.e8340fa211fd'))
                self.assertIsNotNone(await provider.load_access_token(result.access_token))
                with provider.db() as database:
                    self.assertEqual(database.execute('SELECT COUNT(*) FROM tokens').fetchone()[0], 4)
