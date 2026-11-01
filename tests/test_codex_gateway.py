"""隔离验证账号调度和原生协议；不读取用户凭据或访问在线模型。"""
import asyncio
import base64
import copy
import json
import tempfile
import time
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch
from unittest.mock import AsyncMock
from types import SimpleNamespace

import httpx

from harness.codex_gateway.accounts import AccountPool, account_identity
from harness.codex_gateway.__main__ import desktop_config
from harness.codex_gateway.proxy import ResponsesProxy, validate_request
from harness.codex_gateway.quota import QuotaError, parse_quota
from harness.codex_gateway.quota import AppServerQuotaReader
from harness.codex_gateway.lease import runtime_lease
from harness.codex_gateway.protocol import decode_object, openapi
from harness.codex_gateway.server import create_app
from harness.core.language import current_language
from harness.i18n import tr
from harness.llm.chatgpt_auth import ChatGPTAccount


def usage(used=10, *, resets=20000, weekly=10):
    return {'rateLimitsByLimitId': {'codex': {'limitId': 'codex',
        'primary': {'usedPercent': used, 'windowDurationMins': 300, 'resetsAt': resets},
        'secondary': {'usedPercent': weekly, 'windowDurationMins': 10080, 'resetsAt': 30000}}}}


def authorize(account, identity):
    payload = base64.urlsafe_b64encode(json.dumps({'sub': identity}).encode('utf-8')).decode('ascii').rstrip('=')
    account.credentials = {'access': 'fixture.' + payload + '.signature', 'refresh': 'fixture-refresh',
                           'account_id': 'fixture-workspace', 'email': identity + '@example.invalid',
                           'expires': time.time() + 3600}
    account.models = [{'id': 'chatgpt/fixture-model', 'label': 'Fixture', 'provider': 'chatgpt',
                       'description': 'Original 原文', 'supports_reasoning': True, 'reasoning_efforts': ['high']}]
    account.selected = 'chatgpt/fixture-model'
    account.save()


def event(kind, **values):
    return ('event: ' + kind + '\ndata: ' + json.dumps({'type': kind, **values}, ensure_ascii=False) + '\n\n').encode('utf-8')


class QuotaParsingTests(unittest.TestCase):
    def test_registry_and_protocol_reject_duplicate_fields_and_nonfinite_values(self):
        for value in (b'{"version":1,"version":1}', b'{"a":{"x":0,"x":1}}', b'{"value":NaN}', b'[]', b'\xff'):
            with self.subTest(value=value), self.assertRaises((ValueError, UnicodeError)):
                decode_object(value)

    def test_multi_bucket_precedes_single_and_window_order_is_irrelevant(self):
        value = usage(94.9)
        value['rateLimits'] = usage(100)['rateLimitsByLimitId']['codex']
        bucket = value['rateLimitsByLimitId']['codex']
        bucket['primary'], bucket['secondary'] = bucket['secondary'], bucket['primary']
        result = parse_quota(value, epoch=3, monotonic=100)
        self.assertEqual(result.five_hour.used, 94.9)
        self.assertTrue(result.available())
        self.assertTrue(result.fresh(129, 10000))
        self.assertFalse(result.fresh(130, 10000))
        self.assertFalse(result.fresh(101, 20000))
        self.assertEqual(result.epoch, 3)

    def test_threshold_weekly_and_spend_control_block_independently(self):
        for used, weekly, reached in ((95, 0, None), (10, 100, None), (10, 10, 'workspace_member_usage_limit_reached')):
            with self.subTest(used=used, weekly=weekly, reached=reached):
                value = usage(used, weekly=weekly)
                value['rateLimitsByLimitId']['codex']['rateLimitReachedType'] = reached
                self.assertFalse(parse_quota(value, epoch=0).available())
        value = usage()
        value['rateLimitsByLimitId']['codex']['spendControlReached'] = True
        self.assertFalse(parse_quota(value, epoch=0).available())

    def test_unknown_or_malformed_usage_is_not_available(self):
        for field, invalid in (('usedPercent', True), ('usedPercent', -1), ('usedPercent', float('nan')),
                               ('windowDurationMins', True), ('resetsAt', True), ('resetsAt', None)):
            value = usage()
            value['rateLimitsByLimitId']['codex']['primary'][field] = invalid
            with self.subTest(field=field, invalid=invalid), self.assertRaises(QuotaError):
                parse_quota(value, epoch=0)
        for value in ({}, {'rateLimitsByLimitId': {}, 'rateLimits': usage()['rateLimitsByLimitId']['codex']},
                      {'rateLimitsByLimitId': []}, {'rateLimits': {'primary': None, 'secondary': None}}):
            with self.subTest(value=value), self.assertRaises(QuotaError):
                parse_quota(value, epoch=0)

    def test_errors_follow_current_language_without_repeating_the_operation(self):
        error = QuotaError()
        values = []
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                values.append(str(error))
                self.assertEqual(str(error), tr(error.key))
        self.assertEqual(values[0], values[2])
        self.assertNotEqual(values[0], values[1])


class PoolFixture(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.home = Path(self.directory.name)
        self.tick = 100.0
        self.wall = 10000.0
        self.reads = []
        self.allowances = {}

        async def reader(account, epoch):
            identity = account_identity(account)
            self.reads.append(identity)
            return parse_quota(self.allowances.get(identity, usage()), epoch=epoch, monotonic=self.tick)

        self.pool = AccountPool(self.home, ChatGPTAccount(self.home), reader=reader,
                                clock=lambda: self.tick, wall=lambda: self.wall)
        self.addAsyncCleanup(self.pool.close)
        self.pool.configure(True)
        authorize(self.pool.slot('primary').account, 'primary')

    def add_account(self, identity, *, used=10, resets=20000):
        slot = self.pool.slot(self.pool.create())
        authorize(slot.account, identity)
        self.allowances[account_identity(slot.account)] = usage(used, resets=resets)
        return slot


class PoolTests(PoolFixture):
    def test_runtime_lease_excludes_another_writer_without_removing_the_lock_file(self):
        with runtime_lease(self.home):
            with self.assertRaises(QuotaError):
                with runtime_lease(self.home):
                    self.fail('第二写入者不应取得租约')
        self.assertTrue((self.home / 'codex-gateway-runtime.lock').exists())
        with runtime_lease(self.home):
            pass

    async def test_four_account_rotation_and_earliest_reset_selection(self):
        slots = [self.pool.slot('primary'), *[self.add_account(name, resets=20000 + offset)
                 for offset, name in enumerate(('b', 'c', 'd'), 1)]]
        selected = []
        for expected in slots:
            async with self.pool.acquire('fixture-model') as (slot, epoch):
                self.assertIs(slot, expected)
                self.assertTrue(slot.in_use)
                self.assertEqual(epoch, slot.account.epoch)
                selected.append(slot.id)
                self.allowances[account_identity(slot.account)] = usage(95)
        self.assertEqual(len(set(selected)), 4)
        self.allowances[account_identity(slots[1].account)] = usage(0, resets=19000)
        await self.pool.refresh(force=True)
        async with self.pool.acquire('fixture-model') as (slot, _):
            self.assertIs(slot, slots[1])

    async def test_account_count_is_dynamic_beyond_the_four_account_fixture(self):
        slots = [self.pool.slot('primary'), *[self.add_account(f'user-{index}') for index in range(1, 7)]]
        await self.pool.refresh(force=True)
        self.assertEqual(len(self.pool.status()['accounts']), len(slots))
        self.assertEqual(len({slot.id for slot in slots}), 7)
        self.assertTrue(all(row['available'] for row in self.pool.status()['accounts']))

    async def test_duplicate_identity_has_one_scheduling_slot_and_other_users_remain_distinct(self):
        duplicate = self.add_account('primary')
        other = self.add_account('different-user')
        await self.pool.refresh()
        self.assertEqual(len(self.reads), 2)
        snapshot = {row['id']: row for row in self.pool.status()['accounts']}
        self.assertTrue(snapshot[duplicate.id]['duplicate'])
        self.assertFalse(snapshot[duplicate.id]['available'])
        self.assertTrue(snapshot[other.id]['available'])
        self.pool.set_enabled('primary', False)
        await self.pool.refresh(force=True)
        self.assertTrue(next(row for row in self.pool.status()['accounts'] if row['id'] == duplicate.id)['available'])

    async def test_wait_until_reset_then_requery_without_using_reserve(self):
        self.allowances[account_identity(self.pool.slot('primary').account)] = usage(95, resets=10010)
        await self.pool.refresh()
        entered = asyncio.Event()

        async def request():
            async with self.pool.acquire('fixture-model'):
                entered.set()

        task = asyncio.create_task(request())
        await asyncio.wait((task,), timeout=.01)
        self.assertFalse(entered.is_set())
        self.assertFalse(any(row['in_use'] for row in self.pool.status()['accounts']))
        self.wall = 10011
        self.allowances[account_identity(self.pool.slot('primary').account)] = usage(0, resets=20000)
        await asyncio.wait_for(task, 2)
        self.assertTrue(entered.is_set())
        self.assertGreaterEqual(len(self.reads), 2)

    async def test_account_lease_prevents_overlap_and_waiter_cancel_preserves_owner(self):
        async with self.pool.acquire('fixture-model') as (owner, _):
            task = asyncio.create_task(self._acquire_once())
            await asyncio.wait((task,), timeout=.01)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(owner.in_use)
            self.assertEqual(self.pool.waiting, 1)
            with self.assertRaises(QuotaError):
                self.pool.set_enabled('primary', False)
        self.assertEqual(self.pool.waiting, 0)

    async def _acquire_once(self):
        async with self.pool.acquire('fixture-model'):
            pass

    async def test_epoch_change_during_query_cannot_publish_old_quota(self):
        gate = asyncio.Event()
        started = asyncio.Event()

        async def delayed(account, epoch):
            started.set()
            await gate.wait()
            return parse_quota(usage(), epoch=epoch, monotonic=self.tick)

        self.pool.reader = delayed
        task = asyncio.create_task(self.pool.refresh())
        await started.wait()
        self.pool.slot('primary').account.epoch += 1
        gate.set()
        await task
        self.assertIsNone(self.pool.slot('primary').quota)

    async def test_unknown_quota_and_unavailable_model_fail_without_leasing(self):
        with self.assertRaises(QuotaError):
            async with self.pool.acquire('missing-model'):
                self.fail('不应取得不支持模型的租约')

        async def unavailable(*args):
            raise QuotaError()
        self.pool.reader = unavailable
        await self.pool.refresh(force=True)
        with self.assertRaises(QuotaError):
            await self._acquire_once()
        self.assertFalse(self.pool.status()['accounts'][0]['available'])

    async def test_registry_restart_and_public_snapshot_do_not_expose_or_alias_credentials(self):
        self.add_account('b')
        await self.pool.refresh()
        snapshot = self.pool.status()
        text = json.dumps(snapshot)
        self.assertNotIn('fixture-refresh', text)
        self.assertNotIn(self.pool.credential(), text)
        snapshot['accounts'][0]['windows'][0]['used_percent'] = 100
        self.assertEqual(self.pool.status()['accounts'][0]['windows'][0]['used_percent'], 10)
        reopened = AccountPool(self.home, ChatGPTAccount(self.home))
        self.addAsyncCleanup(reopened.close)
        self.assertEqual(self.pool.credential(), reopened.credential())
        self.assertEqual(len(reopened.slots), 2)
        self.assertEqual(reopened.slot('primary').account.selected, 'chatgpt/fixture-model')

    async def test_invalid_and_external_registry_are_preserved(self):
        original = self.pool.path.read_bytes()
        peer = AccountPool(self.home, ChatGPTAccount(self.home))
        self.addAsyncCleanup(peer.close)
        peer.configure(False)
        actual = self.pool.path.read_bytes()
        with self.assertRaises(QuotaError):
            self.pool.create()
        self.assertEqual(self.pool.path.read_bytes(), actual)
        self.pool.path.write_bytes(b'{broken')
        with self.assertRaises(QuotaError):
            AccountPool(self.home, ChatGPTAccount(self.home))
        self.assertEqual(self.pool.path.read_bytes(), b'{broken')
        self.assertNotEqual(original, actual)

    async def test_failed_save_does_not_publish_or_remove_accounts(self):
        before = self.pool.path.read_bytes()
        with patch('harness.codex_gateway.accounts.atomic_json', side_effect=OSError('fixture')):
            with self.assertRaises(OSError):
                self.pool.create()
            with self.assertRaises(OSError):
                self.pool.configure(False)
        self.assertEqual(list(self.pool.slots), ['primary'])
        self.assertTrue(self.pool.enabled)
        self.assertEqual(self.pool.path.read_bytes(), before)

    async def test_expired_snapshot_is_rechecked_without_polling_every_wait_iteration(self):
        self.wall = 20001
        await self.pool.refresh()
        await self.pool.refresh()
        await self.pool.refresh()
        self.assertEqual(len(self.reads), 1)


class FakeTransport:
    def __init__(self, chunks):
        self.chunks = chunks
        self.calls = []
        self.closed = asyncio.Event()

    async def post_stream(self, url, **kwargs):
        self.calls.append((url, kwargs))
        try:
            for chunk in self.chunks:
                yield chunk
        finally:
            self.closed.set()


class ProxyTests(PoolFixture):
    async def test_upstream_close_failure_prevents_terminal_success(self):
        failure = RuntimeError('fixture close failure')

        class FailedClose:
            async def post_stream(inner, *args, **kwargs):
                try:
                    yield event('response.completed', response={'output': []})
                finally:
                    raise failure

        source = ResponsesProxy(self.pool, transport=FailedClose()).stream(
            {'model': 'fixture-model', 'stream': True, 'input': []})
        with self.assertRaises(RuntimeError) as caught:
            await anext(source)
        self.assertIs(caught.exception, failure)
        self.assertFalse(self.pool.slot('primary').in_use)

    async def test_cancelled_upstream_cleanup_failure_is_not_swallowed(self):
        started = asyncio.Event()
        failure = RuntimeError('fixture cleanup failure')

        class BrokenCleanup:
            async def post_stream(inner, *args, **kwargs):
                try:
                    started.set()
                    await asyncio.Event().wait()
                    yield b''
                finally:
                    raise failure

        source = ResponsesProxy(self.pool, transport=BrokenCleanup()).stream(
            {'model': 'fixture-model', 'stream': True, 'input': []})
        task = asyncio.create_task(anext(source))
        await started.wait()
        task.cancel()
        with self.assertRaises(BaseExceptionGroup) as caught:
            await task
        self.assertIn(failure, caught.exception.exceptions)
        self.assertFalse(self.pool.slot('primary').in_use)

    async def test_native_history_encryption_images_tools_and_events_are_preserved(self):
        document = {'model': 'fixture-model', 'stream': True, 'instructions': 'Original 原文', 'input': [
            {'role': 'user', 'content': [{'type': 'input_image', 'image_url': 'data:image/png;base64,fixture'}]},
            {'type': 'reasoning', 'id': 'original-reasoning', 'encrypted_content': 'opaque-encrypted-state'},
            {'type': 'function_call', 'call_id': 'original-call', 'name': 'fixture', 'arguments': '{}'},
            {'type': 'function_call_output', 'call_id': 'original-call', 'output': 'Original 原文'}],
            'tools': [{'type': 'function', 'name': 'fixture', 'parameters': {'type': 'object'}}]}
        original = copy.deepcopy(document)
        raw = event('response.output_text.delta', delta='原文😀') + event('response.completed', response={'output': []})
        transport = FakeTransport([raw[:51], raw[51:67], raw[67:]])
        proxy = ResponsesProxy(self.pool, transport=transport)
        self.assertEqual(b''.join([chunk async for chunk in proxy.stream(document)]), raw)
        sent = json.loads(transport.calls[0][1]['body'].decode('utf-8'))
        self.assertEqual(sent, {**document, 'store': False})
        self.assertEqual(document, original)
        self.assertTrue(transport.closed.is_set())
        self.assertFalse(self.pool.slot('primary').in_use)

    async def test_missing_terminal_does_not_report_completion_or_replay(self):
        transport = FakeTransport([event('response.output_text.delta', delta='partial')])
        proxy = ResponsesProxy(self.pool, transport=transport)
        with self.assertRaises(QuotaError) as caught:
            async for _ in proxy.stream({'model': 'fixture-model', 'stream': True, 'input': []}):
                pass
        self.assertEqual(caught.exception.key, 'ui.codexGateway.streamIncomplete')
        self.assertEqual(len(transport.calls), 1)

    async def test_compaction_preserves_opaque_output(self):
        output = {'output': [{'type': 'compaction', 'encrypted_content': 'opaque-compaction'}]}
        transport = FakeTransport([json.dumps(output).encode('utf-8')])
        proxy = ResponsesProxy(self.pool, transport=transport)
        self.assertEqual(await proxy.compact({'model': 'fixture-model', 'input': []}), output)
        self.assertTrue(transport.calls[0][0].endswith('/responses/compact'))

    async def test_heartbeat_does_not_create_a_model_response_or_spend_reserve(self):
        self.allowances[account_identity(self.pool.slot('primary').account)] = usage(95)
        transport = FakeTransport([])
        proxy = ResponsesProxy(self.pool, transport=transport, heartbeat=.01)
        source = proxy.stream({'model': 'fixture-model', 'stream': True, 'input': []})
        self.assertEqual(await anext(source), b': harness-wait\n\n')
        await source.aclose()
        self.assertEqual(transport.calls, [])
        self.assertEqual(self.pool.waiting, 0)

    async def test_cancellation_waits_for_upstream_and_releases_only_owned_lease(self):
        started, cleaned = asyncio.Event(), asyncio.Event()

        class Blocked:
            async def post_stream(inner, *args, **kwargs):
                try:
                    started.set()
                    await asyncio.Event().wait()
                    yield b''
                finally:
                    cleaned.set()

        source = ResponsesProxy(self.pool, transport=Blocked()).stream(
            {'model': 'fixture-model', 'stream': True, 'input': []})
        task = asyncio.create_task(anext(source))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(cleaned.is_set())
        self.assertFalse(self.pool.slot('primary').in_use)
        self.assertEqual(self.pool.waiting, 0)

    def test_stateful_background_and_malformed_requests_are_rejected(self):
        for override in ({'previous_response_id': 'response-of-another-account'}, {'store': True},
                         {'background': True}, {'model': True}, {'input': None}, {'stream': False},
                         {'input': '\ud800'}):
            with self.subTest(override=override), self.assertRaises(QuotaError):
                validate_request({'model': 'fixture-model', 'input': [], 'stream': True, **override})


class HttpTests(PoolFixture):
    async def test_authentication_error_changes_language_on_the_same_service(self):
        transport = httpx.ASGITransport(app=create_app(self.pool), client=('127.0.0.1', 1))
        async with httpx.AsyncClient(transport=transport, base_url='http://localhost') as client:
            messages = []
            for language in ('en', 'zh-CN', 'en'):
                response = await client.get('/api/v1/codex/models', headers={'Accept-Language': language})
                messages.append(response.json()['error']['message'])
                with current_language.set(language):
                    self.assertEqual(messages[-1], tr('ui.codexGateway.unauthorized'))
            self.assertEqual(messages[0], messages[2])
            self.assertNotEqual(messages[0], messages[1])

    def test_gateway_document_matches_actual_routes_and_is_separate_from_main_api(self):
        app = create_app(self.pool)
        paths = openapi()['paths']
        self.assertEqual(set(paths), {route.path for route in app.routes})
        for route in app.routes:
            for method in route.methods - {'HEAD'}:
                self.assertIn(method.lower(), paths[route.path])

    async def test_native_stream_and_authentication_boundaries(self):
        raw = event('response.completed', response={'output': []})
        upstream = FakeTransport([raw])
        app = create_app(self.pool, proxy=ResponsesProxy(self.pool, transport=upstream))
        transport = httpx.ASGITransport(app=app, client=('127.0.0.1', 1234))
        document = {'model': 'fixture-model', 'input': [], 'stream': True}
        async with httpx.AsyncClient(transport=transport, base_url='http://localhost') as client:
            denied = await client.post('/api/v1/codex/responses', json=document)
            self.assertEqual(denied.status_code, 401)
            self.assertEqual(upstream.calls, [])
            headers = {'Authorization': 'Bearer ' + self.pool.credential()}
            origin = await client.post('/api/v1/codex/responses', json=document,
                                       headers={**headers, 'Origin': 'https://fixture.invalid'})
            self.assertEqual(origin.status_code, 403)
            accepted = await client.post('/api/v1/codex/responses', json=document, headers=headers)
            self.assertEqual(accepted.content, raw)
            self.assertEqual(accepted.headers['cache-control'], 'no-store')
            duplicate = await client.post('/api/v1/codex/responses', headers=headers,
                                          content=b'{"model":"a","model":"b","input":[],"stream":true}')
            self.assertEqual(duplicate.status_code, 400)
        remote = httpx.ASGITransport(app=app, client=('192.0.2.1', 1234))
        async with httpx.AsyncClient(transport=remote, base_url='http://localhost') as client:
            response = await client.post('/api/v1/codex/responses', json=document,
                headers={**headers, 'X-Forwarded-For': '127.0.0.1'})
            self.assertEqual(response.status_code, 403)

    def test_desktop_config_is_valid_toml_and_has_no_subscription_credentials(self):
        config = desktop_config(self.home, 18765, 'fixture-model')
        data = tomllib.loads(config)
        provider = data['model_providers'][data['model_provider']]
        self.assertIs(provider['supports_websockets'], False)
        self.assertEqual(provider['stream_max_retries'], 0)
        self.assertEqual(provider['request_max_retries'], 0)
        self.assertNotIn('fixture-refresh', config)
        self.assertNotIn(self.pool.credential(), config)
        self.assertEqual(provider['auth']['args'][-1], 'token')


class AppServerReaderTests(PoolFixture):
    async def test_query_has_isolated_auth_and_closes_the_owned_process(self):
        output = asyncio.StreamReader()
        for identity, result in ((1, {}), (2, {'type': 'chatgptAuthTokens'}), (3, usage())):
            output.feed_data((json.dumps({'id': identity, 'result': result}) + '\n').encode('utf-8'))
        output.feed_eof()
        packets = []
        stdin = SimpleNamespace(write=packets.append, drain=AsyncMock())
        process = SimpleNamespace(stdin=stdin, stdout=output, returncode=None)
        with patch('harness.codex_gateway.quota.asyncio.create_subprocess_exec', new=AsyncMock(return_value=process)) as spawn, \
             patch('harness.codex_gateway.quota.stop_process', new=AsyncMock()) as stop:
            quota = await AppServerQuotaReader('explicit codex path')(self.pool.slot('primary').account, 0)
        self.assertEqual(quota.five_hour.used, 10)
        stop.assert_awaited_once_with(process)
        args, kwargs = spawn.call_args
        self.assertEqual(args[0], 'explicit codex path')
        self.assertIn('cli_auth_credentials_store="ephemeral"', args)
        self.assertNotEqual(kwargs['env']['CODEX_HOME'], str(self.home))
        self.assertNotIn('OPENAI_API_KEY', kwargs['env'])
        messages = [json.loads(packet.decode('utf-8')) for packet in packets]
        self.assertEqual([message['method'] for message in messages],
                         ['initialize', 'initialized', 'account/login/start', 'account/rateLimits/read'])
        self.assertTrue(messages[0]['params']['capabilities']['experimentalApi'])
        self.assertNotIn('refresh', json.dumps(messages))

    async def test_unsupported_auth_contract_is_explicit_and_redacts_diagnostics(self):
        output = asyncio.StreamReader()
        output.feed_data(b'{"id":1,"result":{}}\n')
        output.feed_data(b'{"id":2,"error":{"message":"private upstream token fixture"}}\n')
        output.feed_eof()
        process = SimpleNamespace(stdin=SimpleNamespace(write=lambda _: None, drain=AsyncMock()),
                                  stdout=output, returncode=None)
        with patch('harness.codex_gateway.quota.asyncio.create_subprocess_exec', new=AsyncMock(return_value=process)), \
             patch('harness.codex_gateway.quota.stop_process', new=AsyncMock()) as stop:
            with self.assertRaises(QuotaError) as caught:
                await AppServerQuotaReader()(self.pool.slot('primary').account, 0)
        self.assertEqual(caught.exception.key, 'ui.codexGateway.quotaUnavailable')
        self.assertNotIn('private', str(caught.exception))
        stop.assert_awaited_once_with(process)


if __name__ == '__main__':
    unittest.main()


class HarnessAccountSelectionTests(PoolFixture):
    async def test_current_selection_is_persistent_and_independent_of_gateway_switch(self):
        second = self.add_account('second')
        self.pool.select(second.id, False)
        self.pool.configure(False)
        async with self.pool.acquire('fixture-model', harness=True) as (selected, _):
            self.assertIs(selected, second)
        snapshot = self.pool.status()
        self.assertEqual(snapshot['current_account'], second.id)
        self.assertFalse(snapshot['auto_rotate'])
        restored = AccountPool(self.home, ChatGPTAccount(self.home), reader=self.pool.reader)
        self.addAsyncCleanup(restored.close)
        self.assertEqual(restored.current_account, second.id)
        self.assertFalse(restored.auto_rotate)
        self.assertFalse(restored.enabled)

    async def test_threshold_switches_only_when_automatic_rotation_is_enabled(self):
        second = self.add_account('second')
        primary = self.pool.slot('primary')
        self.allowances[account_identity(primary.account)] = usage(95, resets=20000)
        self.pool.select('primary', False)
        with self.assertRaises(QuotaError):
            async with self.pool.acquire('fixture-model', harness=True):
                self.fail('关闭自动切换时不能使用另一账号')
        self.pool.select('primary', True)
        async with self.pool.acquire('fixture-model', harness=True) as (selected, _):
            self.assertIs(selected, second)
        self.assertEqual(self.pool.current_account, second.id)
        self.assertFalse(any(row['in_use'] for row in self.pool.status()['accounts']))
