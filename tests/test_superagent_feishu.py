"""飞书真实加密格式、窄地址验证和范围约束迁接统一来源。"""
import copy

from harness.core.errors import HarnessError
from harness.core.language import current_language
from tests.superagent_delivery_fixture import DeliveryFixture


class FeishuSourceTests(DeliveryFixture):
    async def test_unsigned_encrypted_challenge_is_narrow_and_never_admits_unsigned_event(self):
        await self.remote('feishu')
        before = len(self.rows('SELECT * FROM triggers'))
        ingress = self.service.source_ingress('feishu-source')
        headers, raw = self.packet('feishu', {'type': 'url_verification', 'token': 'fixture-token', 'challenge': 'verify-me'}, signed=False)
        self.assertEqual(ingress.receive(headers, raw), {'challenge': 'verify-me'})
        headers, raw = self.packet('feishu', self.remote_data, signed=False)
        with self.assertRaises(HarnessError):
            ingress.receive(headers, raw)
        headers, raw = self.packet('feishu', {'type': 'url_verification', 'token': 'wrong', 'challenge': 'verify-me'}, signed=False)
        with self.assertRaises(HarnessError):
            ingress.receive(headers, raw)
        self.assertEqual(len(self.rows('SELECT * FROM triggers')), before)

    async def test_tenant_app_chat_token_expiry_and_plaintext_are_refused_without_new_work(self):
        await self.remote('feishu')
        ingress = self.service.source_ingress('feishu-source')
        for scope, key, value in (('header', 'app_id', 'cli_wrong'), ('header', 'tenant_key', 'wrong'),
                                  ('header', 'token', 'wrong'), ('message', 'chat_id', 'oc_wrong')):
            data = copy.deepcopy(self.remote_data)
            target = data['header'] if scope == 'header' else data['event']['message']
            target[key] = value
            headers, raw = self.packet('feishu', data)
            with self.assertRaises(HarnessError):
                ingress.receive(headers, raw)
        headers, raw = self.packet('feishu', self.remote_data)
        with self.assertRaises(HarnessError):
            ingress.receive({**headers, 'x-lark-request-timestamp': '0'}, raw)
        with self.assertRaises(HarnessError):
            ingress.receive(headers, b'{"event":"plaintext"}')
        self.assertEqual(len(self.rows('SELECT * FROM triggers')), 1)
        self.assertEqual(len(self.adapter.shared['submit_calls']), 1)

    async def test_signature_error_tracks_actual_call_language_without_reexecution(self):
        await self.remote('feishu')
        headers, raw = self.packet('feishu', self.remote_data)
        headers['x-lark-signature'] = '0' * 64
        messages = []
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language), self.assertRaises(HarnessError) as caught:
                self.service.source_ingress('feishu-source').receive(headers, raw)
            messages.append(caught.exception.message)
        self.assertEqual(messages[0], messages[2])
        self.assertNotEqual(messages[0], messages[1])
        self.assertEqual(len(self.adapter.shared['submit_calls']), 1)

    async def test_node_standard_aes_cipher_interoperates_with_source_decoder(self):
        import asyncio
        import json
        import shutil
        if shutil.which('node') is None:
            self.fail('此互操作回归要求仓库开发依赖 Node.js。')
        await self.remote('feishu')
        script = "const crypto=require('node:crypto');let raw='';process.stdin.on('data',x=>raw+=x);process.stdin.on('end',()=>{const v=JSON.parse(raw);const iv=Buffer.alloc(16,7);const c=crypto.createCipheriv('aes-256-cbc',crypto.createHash('sha256').update(v.key).digest(),iv);const encrypted=Buffer.concat([iv,c.update(JSON.stringify(v.payload)),c.final()]);process.stdout.write(JSON.stringify({encrypt:encrypted.toString('base64')}));});"
        process = await asyncio.create_subprocess_exec('node', '-e', script, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        output, error = await process.communicate(json.dumps({'key':self.remote_secrets['secret://encrypt'].decode(),
            'payload':{'type':'url_verification', 'token':'fixture-token', 'challenge':'node-interoperability'}}).encode())
        self.assertEqual(process.returncode, 0, error.decode())
        self.assertEqual(self.service.source_ingress('feishu-source').receive({}, output), {'challenge':'node-interoperability'})
        self.assertEqual(len(self.rows('SELECT * FROM triggers')), 1)

    async def test_oversized_reply_preserves_frozen_body_and_never_reaches_provider(self):
        provider, assignment, _ = await self.remote('feishu')
        body = '界' * 101
        self.adapter.finish(assignment['assignment_id'], {'status':'completed', 'summary':'Done', 'reply':body})
        await self.f.host.execution.reconcile(assignment['assignment_id'])
        message = self.rows('SELECT * FROM outbox_messages')[0]
        self.assertEqual(self.f.host.execution.outbox_body(message['message_id']), body)
        with self.assertRaises(HarnessError):
            await self.service._coordinator_delivery.tick()
        self.assertEqual(provider.posts, 0)
        self.assertEqual(self.rows('SELECT state,attempts FROM outbox_messages')[0], {'state':'pending', 'attempts':0})
