"""GitHub 原始 Webhook、身份和目标范围在统一信道中的回归。"""
import copy
import unittest

from harness.core.errors import HarnessError
from harness.core.language import current_language
from tests.superagent_delivery_fixture import DeliveryFixture


class GitHubSourceTests(DeliveryFixture):
    async def test_bad_signature_wrong_repository_and_sender_are_refused_without_work(self):
        await self.remote('github')
        before = len(self.adapter.shared['submit_calls'])
        ingress = self.service.source_ingress('github-source')
        headers, raw = self.packet('github', self.remote_data)
        with self.assertRaises(HarnessError):
            ingress.receive({**headers, 'x-hub-signature-256': 'sha256=' + '0' * 64}, raw)
        with self.assertRaises(HarnessError):
            ingress.receive(headers, raw + b' ')
        for changed in ({'id': 11, 'full_name': 'acme/project'}, {'id': 10, 'full_name': 'another/repo'}):
            data = copy.deepcopy(self.remote_data)
            data['repository'] = changed
            headers, raw = self.packet('github', data)
            with self.assertRaises(HarnessError):
                ingress.receive(headers, raw)
        self.assertEqual(len(self.rows('SELECT * FROM triggers')), 1)
        self.assertEqual(len(self.adapter.shared['submit_calls']), before)

    async def test_signature_error_consumes_english_chinese_english_in_same_instance(self):
        await self.remote('github')
        headers, raw = self.packet('github', self.remote_data)
        headers['x-hub-signature-256'] = 'sha256=' + '0' * 64
        messages = []
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language), self.assertRaises(HarnessError) as caught:
                self.service.source_ingress('github-source').receive(headers, raw)
            messages.append(caught.exception.message)
        self.assertEqual(messages[0], messages[2])
        self.assertNotEqual(messages[0], messages[1])

    async def test_quotes_bot_names_and_forged_payload_urls_do_not_expand_authority(self):
        await self.remote('github')
        source = self.f.host._source_service.kind('github')
        config = self.f.host.sources.get_instance('github-source').config
        for body, sender in (('> @bot Fix this', {'id': 18, 'login': 'outsider', 'type': 'User'}),
                             ('@bot Fix this', {'id': 42, 'login': 'bot', 'type': 'Bot'})):
            data = copy.deepcopy(self.remote_data)
            data['issue']['body'] = body
            data['issue']['html_url'] = 'https://attacker.invalid/not-an-issue'
            data['sender'] = sender
            headers, raw = self.packet('github', data)
            result = source.normalize(headers=headers, raw=raw, config=config,
                secret_resolver=self.remote_secrets.__getitem__, received_at='2026-11-08T00:00:00Z')
            if hasattr(result, 'channel_address'):
                self.assertEqual(result.channel_address['repository'], 'acme/project')
                self.assertEqual(result.channel_address['issue_number'], '7')
                if sender['type'] == 'User':
                    self.assertNotEqual(result.addressed, 'mention')

    async def test_actual_webhook_acknowledges_durable_intake_without_another_model_run(self):
        import asyncio
        import json
        from harness.app.superagent_http import WebhookServer
        await self.remote('github')
        server = WebhookServer(self.service.source_ingress('github-source'), 0, 'en', path='/github/test', prefix='github', success_status=202)
        self.addAsyncCleanup(server.aclose)
        await server.start()
        headers, raw = self.packet('github', self.remote_data)
        self.assertEqual(self.service.source_ingress('github-source').receive(headers, raw)['state'], 'duplicate')
        reader, writer = await asyncio.open_connection('127.0.0.1', server.port)
        try:
            head = 'POST /github/test HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: '+str(len(raw))+'\r\n'
            head += ''.join(key+': '+value+'\r\n' for key,value in headers.items())+'\r\n'
            writer.write(head.encode()+raw)
            await writer.drain()
            response = await asyncio.wait_for(reader.read(), 10)
        finally:
            writer.close()
            await writer.wait_closed()
        self.assertIn(b' 202 ', response.split(b'\r\n',1)[0], response.decode())
        body = json.loads(response.split(b'\r\n\r\n',1)[1])
        self.assertEqual(body['state'], 'duplicate')
        self.assertEqual(len(self.rows('SELECT * FROM triggers')), 1)
        self.assertEqual(len(self.adapter.shared['submit_calls']), 1)
        self.assertFalse(server.errors)
        import json as strict_json
        self.assertEqual(strict_json.loads(self.rows('SELECT metadata_json FROM triggers')[0]['metadata_json'])['delivery_id'], 'delivery-1')
        self.assertEqual(len(self.rows("SELECT * FROM audit_events WHERE event_type='trigger.redelivered'")), 1)

    async def test_repository_case_in_original_receipt_does_not_force_reposting(self):
        provider, assignment, _ = await self.remote('github')
        provider.mode, provider.lose_ack = 'absent', True
        await self.complete(assignment)
        provider.objects[0]['issue_url'] = provider.root + '/repos/ACME/Project/issues/7'
        provider.mode = 'exact'
        await self.service.tick()
        self.assertEqual(provider.posts, 1)
        self.assertEqual(self.rows('SELECT state,attempts FROM outbox_messages')[0], {'state':'sent', 'attempts':1})
