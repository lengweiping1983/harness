"""加密飞书回调、真实回环 API、原消息回复与跨信道续接回归。"""
import asyncio
import base64
import copy
import hashlib
import hmac
import json
import os
import sqlite3
import subprocess
import tempfile
import threading
import time
import unittest
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from harness.app.git_process import run_git
from harness.app.superagent_service import SuperagentService, read_config
from harness.core.errors import HarnessError
from harness.core.language import current_language
from tests.agent_configuration_fixture import RecordingTransport
from tests.test_superagent_router import batch_content
from tests.test_superagent_service import ServiceRouterTransport


class SuperagentFeishuTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        for args in (('init','--initial-branch=main'), ('config','user.name','Fixture'),
                     ('config','user.email','fixture@example.invalid')):
            self.git(*args)
        (self.repo / 'main.txt').write_text('fixture\n', encoding='utf-8')
        self.git('add','main.txt')
        self.git('commit','-m','fixture')
        self.encrypt_key = 'fixture-feishu-encrypt-key-not-real'
        self.verify_token = 'fixture-feishu-verification-token'
        self.requests, self.replies, self.comments = [], [], []
        self.messages = {}
        self.bot_open_id = 'ou_bot'
        self.api_code = 0
        self.has_more = False
        self.redirect = False
        self.auth_expire = 7200
        self.reply_count = 0
        outer = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def respond(self, value):
                body = json.dumps(value, ensure_ascii=False).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type','application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def do_GET(self):
                outer.requests.append(('GET', self.path, None, self.headers.get('Authorization')))
                url = urlsplit(self.path)
                if url.path == '/open-apis/bot/v3/info/':
                    if outer.redirect:
                        self.send_response(302)
                        self.send_header('Location', outer.api_url + '/leak')
                        self.send_header('Content-Length','0')
                        self.end_headers()
                    else:
                        self.respond({'code':0, 'bot':{'open_id':outer.bot_open_id}})
                elif url.path == '/open-apis/im/v1/messages':
                    chat = parse_qs(url.query)['container_id'][0]
                    self.respond({'code':0, 'data':{'items':[value for value in outer.replies if value['chat_id'] == chat],
                        'has_more':outer.has_more, 'page_token':'same-page' if outer.has_more else ''}})
                elif url.path.startswith('/open-apis/im/v1/messages/'):
                    message = outer.messages.get(url.path.rsplit('/', 1)[1])
                    self.respond({'code':0, 'data':{'items':[message] if message else []}})
                elif url.path == '/user':
                    self.respond({'id':99,'login':'agent'})
                elif url.path == '/repos/fixture/repo':
                    self.respond({'id':123})
                elif url.path.startswith('/repos/fixture/repo/issues/1/comments'):
                    self.respond(outer.comments)
                else:
                    self.respond({'code':1})
            def do_POST(self):
                value = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                outer.requests.append(('POST', self.path, value, self.headers.get('Authorization')))
                if self.path == '/open-apis/auth/v3/tenant_access_token/internal':
                    self.respond({'code':0, 'tenant_access_token':'fixture-tenant-token', 'expire':outer.auth_expire})
                elif self.path.endswith('/reply'):
                    outer.reply_count += 1
                    if outer.api_code:
                        self.respond({'code':outer.api_code})
                        return
                    original = outer.messages[self.path.split('/')[-2]]
                    message = {'message_id':'om_reply_' + str(outer.reply_count),
                        'chat_id':original['chat_id'], 'parent_id':original['message_id'],
                        'root_id':original.get('root_id') or original['message_id'], 'msg_type':'text',
                        'sender':{'id':'cli_fixture', 'id_type':'app_id', 'sender_type':'app', 'tenant_key':'tenant-fixture'},
                        'body':{'content':json.dumps(json.loads(value['content']), ensure_ascii=False, separators=(',',':'))}}
                    outer.replies.append(message)
                    self.respond({'code':0,'data':message})
                elif self.path == '/repos/fixture/repo/issues/1/comments':
                    comment = {'id':101, 'body':value['body'], 'user':{'id':99},
                               'issue_url':outer.api_url + '/repos/fixture/repo/issues/1'}
                    outer.comments.append(comment)
                    raw = json.dumps(comment).encode('utf-8')
                    self.send_response(201)
                    self.send_header('Content-Length', str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                else:
                    self.respond({'code':1})
        self.api = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.api.serve_forever)
        self.thread.start()
        self.addAsyncCleanup(self.close_api)
        self.api_url = 'http://127.0.0.1:' + str(self.api.server_port)
        environment = patch.dict(os.environ, {'HARNESS_API_KEY':'fixture-model-key',
            'HARNESS_TEST_FEISHU_ENCRYPT':self.encrypt_key, 'HARNESS_TEST_FEISHU_VERIFY':self.verify_token,
            'HARNESS_TEST_FEISHU_SECRET':'fixture-app-secret-not-real',
            'HARNESS_TEST_GITHUB_SECRET':'fixture-github-secret-not-real', 'HARNESS_TEST_GITHUB_TOKEN':'fixture-github-token'})
        environment.start()
        self.addCleanup(environment.stop)
        self.spec = {'version':1, 'instance_id':'team', 'state_dir':'state', 'language':'en',
            'router_model':'router-model', 'worker_model':'worker-model', 'base_url':'https://fixture.invalid/v1',
            'permission_policy':'read_only', 'interval':0.01, 'once_timeout':10,
            'sources':{'feishu':{'mode':'requests','actors':['feishu:user:ou_alice'],
                'threads':['chat/oc_group/root/om_root','chat/oc_group/root/om_alt']}},
            'profiles':{'coding':{'repo':'repo', 'description':'Inspect regressions','sources':['feishu'], 'tools':['read']}},
            'feishu':{'source_id':'feishu', 'app_id':'cli_fixture', 'tenant_key':'tenant-fixture', 'bot_open_id':'ou_bot',
                'chats':['oc_group'], 'encrypt_key_env':'HARNESS_TEST_FEISHU_ENCRYPT',
                'verification_token_env':'HARNESS_TEST_FEISHU_VERIFY', 'app_secret_env':'HARNESS_TEST_FEISHU_SECRET',
                'api_url':self.api_url, 'webhook_port':0, 'reply_mode':'requested'}}
        self.config_path = self.root / 'config.json'
        self.save()
        self.worker_transport, self.router_transport = RecordingTransport(), ServiceRouterTransport()
        self.service = await self.open()

    def git(self, *args):
        value = run_git(self.repo, *args)
        if value.returncode:
            raise RuntimeError(value.stderr.decode('utf-8'))

    async def close_api(self):
        await asyncio.to_thread(self.api.shutdown)
        self.api.server_close()
        await asyncio.to_thread(self.thread.join)

    def save(self):
        self.config_path.write_text(json.dumps(self.spec), encoding='utf-8')

    async def open(self):
        service = await SuperagentService.open(self.config_path, worker_transport=self.worker_transport,
                                              router_transport=self.router_transport)
        self.addAsyncCleanup(service.aclose)
        return service

    def payload(self, *, message_id='om_root', root=None, text='@_user_1 inspect the regression',
                actor='ou_alice', sender_type='user', chat_type='group'):
        message = {'message_id':message_id, 'chat_id':'oc_group', 'chat_type':chat_type,
            'message_type':'text', 'content':json.dumps({'text':text}),
            'mentions':[{'key':'@_user_1','id':{'open_id':'ou_bot'}, 'name':'Agent'}]}
        if root is not None:
            message['root_id'] = root
        self.messages[message_id] = {'message_id':message_id, 'chat_id':'oc_group', 'root_id':root or '',
            'msg_type':'text', 'body':{'content':message['content']}}
        return {'schema':'2.0', 'header':{'token':self.verify_token, 'app_id':'cli_fixture', 'tenant_key':'tenant-fixture',
            'event_id':'event-' + message_id, 'event_type':'im.message.receive_v1'},
            'event':{'sender':{'sender_id':{'open_id':actor},'sender_type':sender_type,'tenant_key':'tenant-fixture'}, 'message':message}}

    def encrypted(self, value, *, signed=True, timestamp=None):
        plain = json.dumps(value, ensure_ascii=False).encode('utf-8')
        padding = 16 - len(plain) % 16
        vector = bytes(range(16))
        encoder = Cipher(algorithms.AES(hashlib.sha256(self.encrypt_key.encode('utf-8')).digest()), modes.CBC(vector)).encryptor()
        ciphertext = vector + encoder.update(plain + bytes([padding]) * padding) + encoder.finalize()
        raw = json.dumps({'encrypt':base64.b64encode(ciphertext).decode('ascii')}).encode('utf-8')
        if not signed:
            return {}, raw
        timestamp = str(int(time.time())) if timestamp is None else str(timestamp)
        nonce = 'fixture-nonce'
        return {'x-lark-request-timestamp':timestamp,'x-lark-request-nonce':nonce,
            'x-lark-signature':hashlib.sha256((timestamp + nonce + self.encrypt_key).encode('utf-8') + raw).hexdigest()}, raw

    def receive(self, value=None, **options):
        headers, raw = self.encrypted(value or self.payload(), **options)
        return self.service.feishu.receive(headers, raw, queued=True)

    async def draft(self):
        self.receive()
        with patch.object(self.service.feishu, 'tick', return_value=None):
            self.assertEqual(await self.service.serve(asyncio.Event(), once=True), 0)
        self.assertEqual(self.service.results.status(), {'draft':1})

    async def test_encrypted_queue_deduplicates_signed_entity_and_preserves_original_text(self):
        value = self.payload(text='@_user_1 保留 /goal 原文')
        receipt = self.receive(value)
        self.assertEqual(receipt['state'], 'verified')
        self.assertEqual(self.service.inbox.events()['events'], [])
        value['header']['event_id'] = 'changed-delivery-id'
        self.receive(value)
        self.service.feishu.drain()
        event = self.service.inbox.events()['events'][0]
        self.assertEqual(event['event_id'], 'feishu:message:om_root')
        self.assertEqual(event['actor_id'], 'feishu:user:ou_alice')
        self.assertEqual(event['text'], '@_user_1 保留 /goal 原文')
        self.assertTrue(event['human'] and event['explicit'])
        value['event']['message']['content'] = json.dumps({'text':'different'})
        with self.assertRaises(HarnessError) as caught:
            self.receive(value)
        self.assertEqual(caught.exception.code, 'superagent/event-conflict')

    async def test_unsigned_encrypted_challenge_is_narrowly_authenticated_and_has_no_model_work(self):
        headers, raw = self.encrypted({'type':'url_verification','token':self.verify_token,'challenge':'proof'}, signed=False)
        self.assertEqual(self.service.feishu.receive(headers, raw), {'challenge':'proof'})
        with self.assertRaises(HarnessError):
            self.receive(signed=False)
        headers, raw = self.encrypted({'type':'url_verification','token':'wrong','challenge':'proof'}, signed=False)
        with self.assertRaises(HarnessError):
            self.service.feishu.receive(headers, raw)
        self.assertEqual(self.router_transport.requests, [])

    async def test_invalid_signature_expired_timestamp_plaintext_and_token_are_rejected(self):
        value = self.payload()
        headers, raw = self.encrypted(value)
        bad = dict(headers, **{'x-lark-signature':'0' * 64})
        cases = [(bad,raw), ({}, json.dumps(value).encode('utf-8')), self.encrypted(value, signed=False),
                 self.encrypted(value, timestamp=int(time.time()) - 28801)]
        value['header']['token'] = 'wrong'
        cases.append(self.encrypted(value))
        for headers, raw in cases:
            with self.assertRaises(HarnessError) as caught:
                self.service.feishu.receive(headers, raw)
            self.assertEqual(caught.exception.code, 'superagent/feishu-signature')
        self.assertEqual(self.service.feishu.status()['queued'], 0)

    async def test_app_tenant_and_chat_scope_cannot_be_forged_by_source_fields(self):
        for field in ('app_id','tenant_key'):
            value = self.payload()
            value['header'][field] = 'other'
            with self.assertRaises(HarnessError) as caught:
                self.receive(value)
            self.assertEqual(caught.exception.code, 'superagent/feishu-scope')
        value = self.payload()
        value['event']['message']['chat_id'] = 'oc_other'
        with self.assertRaises(HarnessError):
            self.receive(value)
        self.assertEqual(self.service.feishu.status()['queued'], 0)

    async def test_mentions_quotes_display_names_and_self_events_cannot_manufacture_requests(self):
        for index, text in enumerate(('> @_user_1 inspect', '```\n@_user_1 inspect\n```', '@Agent inspect', 'text @_user_1 inspect')):
            value = self.payload(message_id='om_quote_' + str(index), root='om_root', text=text)
            self.receive(value)
        self.receive(self.payload(message_id='om_self', root='om_root', actor='ou_bot'))
        self.service.feishu.drain()
        self.assertTrue(all(event['disposition'] != 'accepted' for event in self.service.inbox.events()['events']))
        self.assertEqual(await self.service.serve(asyncio.Event(), once=True), 0)
        self.assertEqual(self.router_transport.requests, [])

    async def test_full_channel_posts_threaded_reply_with_stable_uuid_and_reference(self):
        self.receive()
        self.assertEqual(await self.service.serve(asyncio.Event(), once=True), 0)
        self.assertEqual(self.service.feishu.status()['outbound'], {'sent':1})
        self.assertEqual(self.reply_count, 1)
        request = next(value for method, path, value, _ in self.requests if path.endswith('/reply'))
        self.assertTrue(request['reply_in_thread'])
        self.assertEqual(len(request['uuid']), 36)
        self.assertIn('[ref:report_', json.loads(request['content'])['text'])
        self.assertTrue(all(auth == 'Bearer fixture-tenant-token' for _, path, _, auth in self.requests
                            if '/auth/' not in path))
        self.assertEqual(sum('/auth/' in path for _, path, _, _ in self.requests), 1)
        await self.service.tick()
        self.assertEqual(self.reply_count, 1)

    async def test_actual_http_returns_challenge_and_acknowledges_durable_queue_before_model(self):
        async def post(headers, raw):
            reader, writer = await asyncio.open_connection('127.0.0.1', self.service.feishu.server.port)
            head = ('POST /feishu HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: '
                    + str(len(raw)) + '\r\n' + ''.join(key + ': ' + value + '\r\n' for key, value in headers.items()) + '\r\n')
            writer.write(head.encode('ascii') + raw)
            await writer.drain()
            response = await asyncio.wait_for(reader.read(), 3)
            writer.close()
            await writer.wait_closed()
            self.assertTrue(response.startswith(b'HTTP/1.1 200'))
            return json.loads(response.split(b'\r\n\r\n', 1)[1])
        value = await post(*self.encrypted({'type':'url_verification','token':self.verify_token,'challenge':'proof'}, signed=False))
        self.assertEqual(value, {'challenge':'proof'})
        value = await post(*self.encrypted(self.payload()))
        self.assertEqual(value['state'], 'verified')
        self.assertEqual(self.router_transport.requests, [])
        self.assertEqual(self.service.inbox.events()['events'], [])

    async def test_lost_reply_return_reconciles_semantic_json_without_resending(self):
        await self.draft()
        request = self.service.feishu.client.request
        original = OSError('reply acknowledgement lost')
        async def lost(method, path, *args, **kwargs):
            value = await request(method, path, *args, **kwargs)
            if path.endswith('/reply'):
                raise original
            return value
        with patch.object(self.service.feishu.client, 'request', side_effect=lost):
            with self.assertRaises(OSError) as caught:
                await self.service.feishu.tick()
            self.assertIs(caught.exception, original)
            await self.service.feishu.tick()
        self.assertEqual(self.reply_count, 1)
        self.assertEqual(self.service.feishu.status()['outbound'], {'sent':1})

    async def test_unknown_reply_without_evidence_is_never_reposted_after_restart(self):
        await self.draft()
        request = self.service.feishu.client.request
        async def unknown(method, path, *args, **kwargs):
            if path.endswith('/reply'):
                raise OSError('write result unknown')
            return await request(method, path, *args, **kwargs)
        with patch.object(self.service.feishu.client, 'request', side_effect=unknown):
            with self.assertRaises(OSError):
                await self.service.feishu.tick()
        await self.service.aclose()
        self.service = await self.open()
        with self.service.feishu._db(write=True) as db:
            db.execute('UPDATE outbound SET next_check=0')
        self.assertEqual(await self.service.serve(asyncio.Event(), once=True), 2)
        self.assertEqual(self.reply_count, 0)
        self.assertEqual(self.service.feishu.status()['outbound'], {'unknown':1})

    async def test_policy_revocation_after_bot_lookup_suppresses_pending_reply(self):
        await self.draft()
        request = self.service.feishu.client.request
        async def revoke(method, path, *args, **kwargs):
            value = await request(method, path, *args, **kwargs)
            if '/bot/' in path:
                source = self.service.inbox.source('feishu')
                self.service.inbox.configure_source('feishu', mode='ignore', actors=source['actors'],
                    threads=source['threads'], expected_revision=source['revision'])
            return value
        with patch.object(self.service.feishu.client, 'request', side_effect=revoke):
            await self.service.feishu.tick()
        self.assertEqual(self.service.feishu.status()['outbound'], {'suppressed':1})
        self.assertEqual(self.reply_count, 0)

    async def test_wrong_bot_and_original_chat_identity_stop_before_reply(self):
        await self.draft()
        self.bot_open_id = 'ou_other'
        with self.assertRaises(HarnessError):
            await self.service.feishu.tick()
        self.bot_open_id = 'ou_bot'
        self.messages['om_root']['chat_id'] = 'oc_other'
        with self.assertRaises(HarnessError):
            await self.service.feishu.tick()
        self.assertEqual(self.reply_count, 0)

    async def test_receive_only_mode_does_not_read_app_secret_or_call_api(self):
        await self.service.aclose()
        self.spec['state_dir'] = 'receive-only'
        self.spec['feishu']['reply_mode'] = 'disabled'
        self.save()
        with patch.dict(os.environ, {'HARNESS_TEST_FEISHU_SECRET':''}):
            self.service = await self.open()
            self.receive()
            self.assertEqual(await self.service.serve(asyncio.Event(), once=True), 0)
        self.assertEqual(self.requests, [])
        self.assertEqual(self.service.results.status(), {'draft':1})

    async def test_local_event_without_encrypted_origin_cannot_get_feishu_posting_rights(self):
        self.service.inbox.receive(source_id='feishu', event_id='local-only', actor_id='feishu:user:ou_alice', text='inspect',
            thread_ids=['chat/oc_group/root/om_root'], human=True, explicit=True)
        self.assertEqual(await self.service.serve(asyncio.Event(), once=True), 0)
        self.assertEqual(self.requests, [])

    async def test_queued_verified_events_survive_restart_without_duplicate_work_or_replies(self):
        self.receive()
        await self.service.aclose()
        self.service = await self.open()
        self.assertEqual(await self.service.serve(asyncio.Event(), once=True), 0)
        await self.service.aclose()
        self.service = await self.open()
        self.assertEqual(await self.service.serve(asyncio.Event(), once=True), 0)
        self.assertEqual(len(self.worker_transport.requests), 1)
        self.assertEqual(self.reply_count, 1)

    async def test_signature_errors_follow_english_chinese_english_on_same_instance(self):
        messages = []
        for language in ('en','zh-CN','en'):
            with current_language.set(language):
                with self.assertRaises(HarnessError) as caught:
                    self.service.feishu.receive({}, b'{}')
                messages.append(caught.exception.message)
        self.assertEqual(messages[0], messages[2])
        self.assertIn('飞书', messages[1])
        self.assertNotEqual(messages[0], messages[1])

    async def test_shutdown_joins_accepted_reply_and_keeps_remote_receipt(self):
        await self.draft()
        request = self.service.feishu.client.request
        entered, release = asyncio.Event(), asyncio.Event()
        async def held(method, path, *args, **kwargs):
            value = await request(method, path, *args, **kwargs)
            if path.endswith('/reply'):
                entered.set()
                await release.wait()
            return value
        with patch.object(self.service.feishu.client, 'request', side_effect=held):
            tick = asyncio.create_task(self.service.tick())
            await asyncio.wait_for(entered.wait(), 3)
            close = asyncio.create_task(self.service.aclose())
            await asyncio.sleep(0)
            self.assertFalse(close.done())
            release.set()
            await asyncio.wait_for(asyncio.gather(tick, close), 5)
        self.assertEqual(self.reply_count, 1)
        self.assertEqual(self.service.feishu.status()['outbound'], {'sent':1})

    async def test_authentication_redirect_is_rejected_before_forwarding_bearer(self):
        await self.draft()
        self.redirect = True
        with self.assertRaises(HarnessError):
            await self.service.feishu.tick()
        self.assertFalse(any(path == '/leak' for _, path, _, _ in self.requests))
        self.assertEqual(self.reply_count, 0)

    async def test_corrupt_queue_and_outbox_preserve_original_records_without_external_write(self):
        await self.draft()
        self.service.feishu._collect()
        with closing(sqlite3.connect(self.service.feishu.path)) as db, db:
            db.execute("UPDATE outbound SET body='corrupt'")
        with self.assertRaises(HarnessError):
            await self.service.feishu.tick()
        self.assertEqual(self.requests, [])
        with closing(sqlite3.connect(self.service.feishu.path)) as db:
            self.assertEqual(db.execute('SELECT body FROM outbound').fetchone()[0], 'corrupt')

    async def test_reply_api_business_failure_stays_unknown_without_automatic_post_retry(self):
        await self.draft()
        self.api_code = 230011
        with self.assertRaises(HarnessError) as caught:
            await self.service.feishu.tick()
        self.assertEqual(caught.exception.code, 'superagent/feishu-api')
        with self.service.feishu._db(write=True) as db:
            db.execute('UPDATE outbound SET next_check=0')
        await self.service.feishu.tick()
        self.assertEqual(self.reply_count, 1)
        self.assertEqual(self.service.feishu.status()['outbound'], {'unknown':1})

    async def test_source_reply_limit_is_actually_consumed_and_reserves_reference_word(self):
        original = self.router_transport.reply
        def maximum(request):
            value = original(request)
            if 'reply' in value:
                value['reply'] = ' '.join(['word'] * 99)
            return value
        with patch.object(self.router_transport, 'reply', side_effect=maximum):
            self.receive()
            self.assertEqual(await self.service.serve(asyncio.Event(), once=True), 0)
        self.assertEqual(len(json.loads(self.replies[0]['body']['content'])['text'].split()), 100)
        prompts = json.dumps(self.router_transport.requests)
        self.assertIn('99 words', prompts)

    async def test_crypto_interoperates_with_node_standard_aes_implementation(self):
        value = self.payload()
        script = """const fs=require('fs'),c=require('crypto');const x=JSON.parse(fs.readFileSync(0,'utf8'));
const iv=Buffer.alloc(16,7),cipher=c.createCipheriv('aes-256-cbc',c.createHash('sha256').update(x.key).digest(),iv);
process.stdout.write(JSON.stringify({encrypt:Buffer.concat([iv,cipher.update(JSON.stringify(x.body)),cipher.final()]).toString('base64')}));"""
        process = await asyncio.create_subprocess_exec('node', '-e', script, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        raw, error = await process.communicate(json.dumps({'key':self.encrypt_key,'body':value}).encode('utf-8'))
        self.assertEqual(process.returncode, 0, error.decode('utf-8'))
        stamp, nonce = str(int(time.time())), 'node-nonce'
        headers = {'x-lark-request-timestamp':stamp, 'x-lark-request-nonce':nonce,
            'x-lark-signature':hashlib.sha256((stamp + nonce + self.encrypt_key).encode('utf-8') + raw).hexdigest()}
        self.assertEqual(self.service.feishu.receive(headers, raw, queued=True)['state'], 'verified')

    async def test_invalid_config_or_missing_secrets_fail_before_runtime_construction(self):
        self.spec['state_dir'] = 'invalid'
        self.spec['feishu']['api_url'] = 'http://untrusted.invalid'
        self.save()
        with self.assertRaises(HarnessError):
            read_config(self.config_path)
        self.assertFalse((self.root / 'invalid').exists())
        self.spec['feishu']['api_url'] = self.api_url
        self.save()
        with patch.dict(os.environ, {'HARNESS_TEST_FEISHU_ENCRYPT':''}):
            with self.assertRaises(HarnessError):
                await SuperagentService.open(self.config_path)
        self.assertFalse((self.root / 'invalid').exists())

    async def test_github_request_continues_in_feishu_under_same_worker_and_original_reply_targets(self):
        await self.service.aclose()
        self.spec['state_dir'] = 'cross-channel'
        self.spec['sources']['github'] = {'mode':'requests','actors':['github:user:1'],'threads':['issue/1']}
        self.spec['profiles']['coding']['sources'].append('github')
        self.spec['github'] = {'source_id':'github','repository':'fixture/repo','repository_id':123,'bot_login':'agent','bot_user_id':99,
            'secret_env':'HARNESS_TEST_GITHUB_SECRET','token_env':'HARNESS_TEST_GITHUB_TOKEN','api_url':self.api_url,
            'webhook_port':0,'reply_mode':'requested'}
        self.save()
        self.service = await self.open()
        original = self.router_transport.reply
        def reuse(request):
            value = original(request)
            if 'assignments' in value:
                frame = json.loads(batch_content(request).rsplit('\n\n', 1)[1])
                task = next((key for key in frame['workers'] if key.startswith('sa_')), None)
                if task:
                    for item in value['assignments']:
                        item['task_id'] = task
            return value
        user = {'id':1,'login':'alice','type':'User'}
        github = {'action':'created','repository':{'id':123,'full_name':'fixture/repo'},'issue':{'id':200,'number':1},
                  'sender':user,'comment':{'id':9,'body':'@agent fix the parser using A','user':user}}
        raw = json.dumps(github).encode('utf-8')
        headers = {'x-github-event':'issue_comment','x-github-delivery':'delivery-1',
                   'x-hub-signature-256':'sha256=' + hmac.new(b'fixture-github-secret-not-real',raw,hashlib.sha256).hexdigest()}
        with patch.object(self.router_transport, 'reply', side_effect=reuse):
            self.service.github.receive(headers, raw, queued=True)
            self.assertEqual(await self.service.serve(asyncio.Event(), once=True), 0)
            self.receive(self.payload(text='@_user_1 continue that parser task using B'))
            self.assertEqual(await self.service.serve(asyncio.Event(), once=True), 0)
            self.receive(self.payload(message_id='om_followup', root='om_root', text='Include the empty case.'))
            self.assertEqual(await self.service.serve(asyncio.Event(), once=True), 0)
        self.assertEqual(self.service.factory.status()['states'], {'ready':1})
        self.assertEqual(len(self.service.worker_host.worktrees.list()), 1)
        self.assertEqual(len(self.worker_transport.requests), 3)
        self.assertEqual(self.service.github.status()['outbound'], {'sent':1})
        self.assertEqual(self.service.feishu.status()['outbound'], {'sent':2})
        self.assertEqual([item['parent_id'] for item in self.replies], ['om_root','om_followup'])
        self.assertEqual(len({row['session_id'] for row in self.service.dispatcher.deliveries()['deliveries']}), 1)

    async def test_parallel_ticks_share_token_refresh_and_claim_only_one_reply(self):
        await self.draft()
        await asyncio.gather(self.service.feishu.tick(), self.service.feishu.tick())
        self.assertEqual(self.reply_count, 1)
        self.assertEqual(sum('/auth/' in path for _, path, _, _ in self.requests), 1)
        self.assertEqual(self.service.feishu.status()['outbound'], {'sent':1})

    async def test_partial_or_cyclic_history_cannot_confirm_unknown_reply(self):
        await self.draft()
        request = self.service.feishu.client.request
        async def lost(method, path, *args, **kwargs):
            value = await request(method, path, *args, **kwargs)
            if path.endswith('/reply'):
                raise OSError('reply acknowledgement lost')
            return value
        self.has_more = True
        with patch.object(self.service.feishu.client, 'request', side_effect=lost):
            with self.assertRaises(BaseExceptionGroup):
                await self.service.feishu.tick()
        self.assertEqual(self.reply_count, 1)
        self.assertEqual(self.service.feishu.status()['outbound'], {'unknown':1})
        self.has_more = False
        with self.service.feishu._db(write=True) as db:
            db.execute('UPDATE outbound SET next_check=0')
        await self.service.feishu.tick()
        self.assertEqual(self.service.feishu.status()['outbound'], {'sent':1})
        self.assertEqual(self.reply_count, 1)

    async def test_malformed_roots_mentions_content_and_unknown_scope_never_start_work(self):
        template = self.payload()
        for field, value in (('root_id', False), ('mentions', None), ('content', '[]')):
            payload = copy.deepcopy(template)
            payload['event']['message'][field] = value
            with self.assertRaises(HarnessError):
                self.receive(payload)
        self.receive(self.payload(message_id='om_unlisted', text='@_user_1 inspect'))
        self.service.feishu.drain()
        self.assertEqual(self.service.inbox.events()['events'][0]['disposition'], 'thread_denied')
        self.assertEqual(await self.service.serve(asyncio.Event(), once=True), 0)
        self.assertEqual(self.worker_transport.requests, [])

    async def test_over_budget_draft_is_rejected_and_budget_snapshot_cannot_be_mutated(self):
        limits = self.service.results.reply_limits
        limits['feishu'] = 100
        self.assertEqual(self.service.results.reply_limits, {'feishu':99})
        original = self.router_transport.reply
        def over_budget(request):
            value = original(request)
            if 'reply' in value:
                value['reply'] = ' '.join(['word'] * 100)
            return value
        with patch.object(self.router_transport, 'reply', side_effect=over_budget):
            self.receive()
            self.assertEqual(await self.service.serve(asyncio.Event(), once=True), 2)
        self.assertEqual(self.reply_count, 0)
        self.assertEqual(self.service.results.status(), {'rejected':1})

    async def test_second_listener_start_failure_closes_first_listener_and_releases_state_lease(self):
        await self.service.aclose()
        self.spec['state_dir'] = 'failed-listener'
        self.spec['sources']['github'] = {'mode':'requests','actors':['github:user:1'],'threads':['issue/1']}
        self.spec['github'] = {'source_id':'github','repository':'fixture/repo','repository_id':123,'bot_login':'agent','bot_user_id':99,
            'secret_env':'HARNESS_TEST_GITHUB_SECRET','token_env':'HARNESS_TEST_GITHUB_TOKEN','api_url':self.api_url,
            'webhook_port':0,'reply_mode':'disabled'}
        self.save()
        original = asyncio.start_server
        servers = []
        async def fail_second(*args, **kwargs):
            if servers:
                raise OSError('second listener failed')
            server = await original(*args, **kwargs)
            servers.append(server)
            return server
        with patch('harness.app.superagent_http.asyncio.start_server', side_effect=fail_second):
            with self.assertRaises(OSError):
                await self.open()
        self.assertFalse(servers[0].is_serving())
        self.assertEqual(servers[0].sockets, ())
        restarted = await self.open()
        self.assertTrue(restarted.github.server.server.is_serving())
        self.assertTrue(restarted.feishu.server.server.is_serving())

    async def test_source_queue_corruption_is_preserved_and_dependency_failure_precedes_state_creation(self):
        self.receive()
        with self.service.feishu._db(write=True) as db:
            db.execute("UPDATE origins SET target='{}'")
        with self.assertRaises(HarnessError) as caught:
            self.service.feishu.drain()
        self.assertEqual(caught.exception.code, 'superagent/corrupt')
        self.assertEqual(self.worker_transport.requests, [])
        self.spec['state_dir'] = 'missing-dependency'
        self.save()
        original = __import__
        def missing(name, *args, **kwargs):
            if name.startswith('cryptography.'):
                raise ImportError('fixture missing optional dependency')
            return original(name, *args, **kwargs)
        with patch('builtins.__import__', side_effect=missing):
            with self.assertRaises(HarnessError) as caught:
                await self.open()
        self.assertEqual(caught.exception.code, 'superagent/feishu-dependency')
        self.assertFalse((self.root / 'missing-dependency').exists())

    async def test_url_verification_and_direct_messages_do_not_relax_actor_or_thread_scope(self):
        self.receive(self.payload(chat_type='p2p', text='Inspect the regression.'))
        self.service.feishu.drain()
        event = self.service.inbox.events()['events'][0]
        self.assertTrue(event['explicit'])
        self.assertEqual(event['disposition'], 'accepted')
        with self.assertRaises(HarnessError):
            self.service.feishu.receive(*self.encrypted(self.payload(actor='ou_other'), signed=False), queued=True)
        self.receive(self.payload(message_id='om_direct_other', root='om_root', actor='ou_other', chat_type='p2p'))
        self.service.feishu.drain()
        self.assertEqual(self.service.inbox.event(source_id='feishu', event_id='feishu:message:om_direct_other')['disposition'], 'actor_denied')
