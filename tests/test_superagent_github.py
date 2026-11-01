"""签名 Webhook、真实本机 HTTP 发言和丢失回执后的单次发送回归。"""
import asyncio
import hashlib
import hmac
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from harness.app.git_process import run_git
from harness.app.superagent_service import SuperagentService
from harness.core.errors import HarnessError
from harness.core.language import current_language
from tests.agent_configuration_fixture import RecordingTransport
from tests.test_superagent_router import batch_content
from tests.test_superagent_service import ServiceRouterTransport


class SuperagentGitHubTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        for args in (('init','--initial-branch=main'),('config','user.name','Fixture'),
                     ('config','user.email','fixture@example.invalid')):
            self.git(*args)
        (self.repo / 'main.txt').write_text('fixture\n',encoding='utf-8')
        self.git('add','main.txt')
        self.git('commit','-m','fixture')
        self.requests,self.comments = [],[]
        self.post_count = 0
        self.api_author = 99
        self.api_repository = 123
        self.redirect_user = False
        outer = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):
                pass
            def respond(self,status,data):
                raw = json.dumps(data).encode('utf-8')
                self.send_response(status)
                self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            def do_GET(self):
                outer.requests.append(('GET',self.path))
                if self.path == '/user':
                    if outer.redirect_user:
                        self.send_response(302)
                        self.send_header('Location',outer.api_url+'/leak')
                        self.send_header('Content-Length','0')
                        self.end_headers()
                    else:
                        self.respond(200,{'id':outer.api_author,'login':'agent'})
                elif self.path == '/repos/fixture/repo':
                    self.respond(200,{'id':outer.api_repository})
                elif self.path.startswith('/repos/fixture/repo/issues/') and '/comments?' in self.path:
                    issue_url = outer.api_url + self.path.split('/comments?', 1)[0]
                    self.respond(200,[comment for comment in outer.comments if comment['issue_url'] == issue_url])
                else:
                    self.respond(404,{})
            def do_POST(self):
                outer.requests.append(('POST',self.path))
                outer.post_count += 1
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))['body']
                value = {'id':100+outer.post_count,'body':body,'user':{'id':outer.api_author},
                         'issue_url':outer.api_url+self.path.removesuffix('/comments')}
                outer.comments.append(value)
                self.respond(201,value)
        self.server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.addAsyncCleanup(self.close_api)
        self.api_url = 'http://127.0.0.1:'+str(self.server.server_port)
        self.secret = b'fixture-webhook-secret-is-not-a-real-secret'
        environment = patch.dict(os.environ,{'HARNESS_API_KEY':'fixture-only',
            'HARNESS_TEST_GITHUB_SECRET':self.secret.decode('utf-8'),'HARNESS_TEST_GITHUB_TOKEN':'fixture-token'})
        environment.start()
        self.addCleanup(environment.stop)
        self.spec = {'version':1,'instance_id':'team','state_dir':'state','language':'en',
            'router_model':'router-model','worker_model':'worker-model','base_url':'https://fixture.invalid/v1',
            'permission_policy':'read_only','interval':0.01,'once_timeout':10,
            'sources':{'github':{'mode':'requests','actors':['github:user:1'],'threads':['issue/1','issue/2']}},
            'profiles':{'coding':{'repo':'repo','description':'Inspect regressions','sources':['github'],'tools':['read']}},
            'github':{'source_id':'github','repository':'fixture/repo','repository_id':123,'bot_login':'agent','bot_user_id':99,
                'secret_env':'HARNESS_TEST_GITHUB_SECRET','token_env':'HARNESS_TEST_GITHUB_TOKEN','api_url':self.api_url,
                'webhook_port':0,'reply_mode':'requested'}}
        self.path = self.root / 'config.json'
        self.save()
        self.worker_transport,self.router_transport = RecordingTransport(),ServiceRouterTransport()
        self.service = await self.open()

    async def close_api(self):
        await asyncio.to_thread(self.server.shutdown)
        self.server.server_close()
        await asyncio.to_thread(self.thread.join)

    def git(self,*args):
        result = run_git(self.repo,*args)
        if result.returncode:
            raise RuntimeError(result.stderr.decode('utf-8'))

    def save(self):
        self.path.write_text(json.dumps(self.spec),encoding='utf-8')

    async def open(self):
        service = await SuperagentService.open(self.path,worker_transport=self.worker_transport,router_transport=self.router_transport)
        self.addAsyncCleanup(service.aclose)
        return service

    def payload(self,*,body='@agent please inspect',comment_id=9,actor_id=1,actor_type='User',issue=1):
        user = {'id':actor_id,'login':'alice' if actor_id == 1 else 'agent','type':actor_type}
        return {'action':'created','repository':{'id':123,'full_name':'fixture/repo'},
            'issue':{'id':500+issue,'number':issue,'user':user},'sender':user,
            'comment':{'id':comment_id,'body':body,'user':user}}

    def signed(self,payload=None,*,delivery='delivery-one',kind='issue_comment'):
        raw = json.dumps(payload or self.payload(),ensure_ascii=False).encode('utf-8')
        return {'x-github-event':kind,'x-github-delivery':delivery,
                'x-hub-signature-256':'sha256='+hmac.new(self.secret,raw,hashlib.sha256).hexdigest()},raw

    def receive(self,payload=None,**kwargs):
        headers,raw = self.signed(payload,**kwargs)
        return self.service.github.receive(headers,raw)

    async def finish_hosts(self):
        tasks = [record.task for host in self.service.hosts for record in tuple(host.runtime.records.active.values())
                 if record.task is not None]
        await asyncio.wait_for(asyncio.gather(*tasks),5)

    async def draft(self):
        self.receive()
        # 本地生成候选期间不向外发言；随后单独验证显式发件箱。
        with patch.object(self.service.github,'tick',return_value=None):
            result = await self.service.serve(asyncio.Event(),once=True)
        self.assertEqual(result,0)
        self.assertEqual(self.service.results.status(),{'draft':1})

    async def test_signed_webhook_maps_verified_repository_actor_and_thread(self):
        receipt = self.receive()
        event = self.service.inbox.event(source_id='github',event_id=receipt['event_id'])
        self.assertEqual(event['event_id'],'github:comment:9')
        self.assertEqual(event['actor_id'],'github:user:1')
        self.assertEqual(event['thread_ids'],['issue/1'])
        self.assertTrue(event['human'])
        self.assertTrue(event['explicit'])
        self.assertEqual(self.worker_transport.requests,[])
        duplicate = self.receive(delivery='different-header-id')
        self.assertTrue(duplicate['duplicate'])
        self.assertEqual(len(self.service.inbox.events()['events']),1)

    async def test_bad_signature_tampering_wrong_repository_and_sender_are_rejected(self):
        headers,raw = self.signed()
        cases = [(headers,raw+b' '),({**headers,'x-hub-signature-256':'sha256=bad'},raw)]
        for bad_headers,bad_raw in cases:
            with self.assertRaises(HarnessError) as caught:
                self.service.github.receive(bad_headers,bad_raw)
            self.assertEqual(caught.exception.code,'superagent/github-signature')
        foreign = self.payload()
        foreign['repository']['id'] = 999
        with self.assertRaises(HarnessError) as caught:
            self.receive(foreign)
        self.assertEqual(caught.exception.code,'superagent/github-scope')
        spoofed = self.payload()
        spoofed['sender'] = {'id':2,'login':'other','type':'User'}
        with self.assertRaises(HarnessError):
            self.receive(spoofed)
        self.assertEqual(self.service.inbox.events()['events'],[])

    async def test_quotes_bots_and_own_posting_account_cannot_create_explicit_requests(self):
        for index,body in enumerate(('> @agent delete all','```\n@agent work\n```','`@agent` work')):
            receipt = self.receive(self.payload(body=body,comment_id=20+index))
            self.assertEqual(receipt['disposition'],'request_required')
        receipt = self.receive(self.payload(actor_id=99,body='@agent repeated request'))
        self.assertEqual(receipt['disposition'],'actor_denied')
        receipt = self.receive(self.payload(actor_type='Bot',comment_id=80))
        self.assertEqual(receipt['disposition'],'actor_denied')
        await self.service.tick()
        self.assertEqual(self.router_transport.requests,[])

    async def test_actual_http_webhook_acknowledges_before_model_work(self):
        headers,raw = self.signed()
        reader,writer = await asyncio.open_connection('127.0.0.1',self.service.github.server.port)
        request = ('POST /github HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: '+str(len(raw))+'\r\n'
            + ''.join(key+': '+value+'\r\n' for key,value in headers.items())+'\r\n').encode('ascii')+raw
        writer.write(request)
        await writer.drain()
        response = await asyncio.wait_for(reader.read(),3)
        writer.close()
        await writer.wait_closed()
        self.assertTrue(response.startswith(b'HTTP/1.1 202'))
        self.assertEqual(json.loads(response.split(b'\r\n\r\n',1)[1])['event_id'],'github:comment:9')
        self.assertEqual(json.loads(response.split(b'\r\n\r\n',1)[1])['state'],'verified')
        self.assertEqual(self.service.inbox.events()['events'],[])
        self.assertEqual(self.service.github.status()['queued'],1)
        self.assertEqual(self.router_transport.requests,[])
        await self.service.tick()
        self.assertEqual(self.service.github.status()['queued'],0)

    async def test_full_signed_channel_pipeline_posts_once_to_verified_original_issue(self):
        self.receive()
        self.assertEqual(await self.service.serve(asyncio.Event(),once=True),0)
        self.assertEqual(self.post_count,1)
        self.assertEqual(self.service.github.status()['outbound'],{'sent':1})
        self.assertEqual(self.requests[:3],[('GET','/user'),('GET','/repos/fixture/repo'),('POST','/repos/fixture/repo/issues/1/comments')])
        self.assertIn('<!-- harness-superagent:report_',self.comments[0]['body'])
        await self.service.tick()
        self.assertEqual(self.post_count,1)

    async def test_lost_create_return_reconciles_exact_remote_comment_without_reposting(self):
        await self.draft()
        request = self.service.github.client.request
        original = OSError('remote acknowledgement lost')
        async def lost(method,*args,**kwargs):
            result = await request(method,*args,**kwargs)
            if method == 'POST':
                raise original
            return result
        with patch.object(self.service.github.client,'request',side_effect=lost):
            with self.assertRaises(OSError) as caught:
                await self.service.github.tick()
            self.assertIs(caught.exception,original)
            self.assertEqual(self.service.github.status()['outbound'],{'sent':1})
            await self.service.github.tick()
        self.assertEqual(self.post_count,1)

    async def test_related_issue_reuses_worker_and_comments_return_to_each_original_issue(self):
        original = self.router_transport.reply
        def reuse(request):
            answer = original(request)
            if 'assignments' in answer:
                frame = json.loads(batch_content(request).rsplit('\n\n', 1)[1])
                existing = next((task_id for task_id in frame['workers'] if task_id.startswith('sa_')), None)
                if existing is not None:
                    for item in answer['assignments']:
                        item['task_id'] = existing
            return answer
        with patch.object(self.router_transport, 'reply', side_effect=reuse):
            for value in (self.payload(), self.payload(comment_id=10, issue=2, body='@agent continue the same investigation here'),
                          self.payload(comment_id=11, issue=2, body='Include the empty input case.')):
                headers, raw = self.signed(value)
                self.service.github.receive(headers, raw, queued=True)
                self.assertEqual(await self.service.serve(asyncio.Event(), once=True), 0)
        self.assertEqual(self.service.factory.status()['states'], {'ready': 1})
        self.assertEqual(len(self.service.hosts[1].worktrees.list()), 1)
        self.assertEqual(len(self.worker_transport.requests), 3)
        self.assertEqual(self.service.github.status()['outbound'], {'sent': 3})
        self.assertEqual([path for method, path in self.requests if method == 'POST'], [
            '/repos/fixture/repo/issues/1/comments', '/repos/fixture/repo/issues/2/comments',
            '/repos/fixture/repo/issues/2/comments'])
        rows = self.service.dispatcher.deliveries()['deliveries']
        self.assertEqual(len({row['session_id'] for row in rows}), 1)

    async def test_unknown_write_stays_unknown_when_no_exact_remote_evidence_exists(self):
        await self.draft()
        request = self.service.github.client.request
        async def unknown(method,*args,**kwargs):
            if method == 'POST':
                raise OSError('outcome unknown')
            return await request(method,*args,**kwargs)
        with patch.object(self.service.github.client,'request',side_effect=unknown) as attempted:
            with self.assertRaises(OSError):
                await self.service.github.tick()
            with self.service.github._db(write=True) as db:
                db.execute('UPDATE outbound SET next_check=0')
            await self.service.github.tick()
            self.assertEqual(sum(call.args[0] == 'POST' for call in attempted.call_args_list),1)
        self.assertEqual(self.service.github.status()['outbound'],{'unknown':1})
        self.assertEqual(self.post_count,0)

    async def test_current_source_revision_is_rechecked_after_identity_lookup(self):
        await self.draft()
        request = self.service.github.client.request
        async def revoke(method,path,*args,**kwargs):
            result = await request(method,path,*args,**kwargs)
            if path == '/user':
                self.service.inbox.configure_source('github',mode='ignore',actors=['github:user:1'],threads=['issue/1'],expected_revision=1)
            return result
        with patch.object(self.service.github.client,'request',side_effect=revoke):
            await self.service.github.tick()
        self.assertEqual(self.service.github.status()['outbound'],{'suppressed':1})
        self.assertEqual(self.post_count,0)

    async def test_wrong_token_owner_prevents_any_post(self):
        await self.draft()
        self.api_author = 88
        with self.assertRaises(HarnessError) as caught:
            await self.service.github.tick()
        self.assertEqual(caught.exception.code,'superagent/github-scope')
        self.assertEqual(self.post_count,0)

    async def test_restart_uses_original_sent_receipt_without_posting_again(self):
        self.receive()
        await self.service.serve(asyncio.Event(),once=True)
        await self.service.aclose()
        self.service = await self.open()
        self.receive(delivery='redelivery-after-restart')
        await self.service.serve(asyncio.Event(),once=True)
        self.assertEqual(self.post_count,1)
        self.assertEqual(self.service.github.status()['outbound'],{'sent':1})

    async def test_invalid_persisted_outbound_body_does_not_reach_network(self):
        await self.draft()
        self.service.github._collect()
        with closing(sqlite3.connect(self.service.github.path)) as db,db:
            db.execute("UPDATE outbound SET body='changed'")
        with self.assertRaises(HarnessError) as caught:
            await self.service.github.tick()
        self.assertEqual(caught.exception.code,'superagent/corrupt')
        self.assertEqual(self.requests,[])

    async def test_signature_errors_resolve_english_chinese_english_on_same_adapter(self):
        messages = []
        for language in ('en','zh-CN','en'):
            with current_language.set(language):
                with self.assertRaises(HarnessError) as caught:
                    self.service.github.receive({},b'{}')
                messages.append(caught.exception.message)
        self.assertEqual(messages[0],messages[2])
        self.assertIn('签名',messages[1])
        self.assertNotEqual(messages[0],messages[1])

    async def test_queued_verified_events_survive_restart_and_replay_only_intake(self):
        headers,raw = self.signed()
        self.service.github.receive(headers,raw,queued=True)
        self.assertEqual(self.service.inbox.events()['events'],[])
        await self.service.aclose()
        self.service = await self.open()
        self.assertEqual(await self.service.serve(asyncio.Event(),once=True),0)
        self.assertEqual(len(self.service.inbox.events()['events']),1)
        self.assertEqual(self.post_count,1)

    async def test_disabled_reply_mode_keeps_local_draft_without_reading_posting_token(self):
        await self.service.aclose()
        self.spec['state_dir'] = 'receive-only'
        self.spec['github']['reply_mode'] = 'disabled'
        self.save()
        with patch.dict(os.environ,{'HARNESS_TEST_GITHUB_TOKEN':''}):
            self.service = await self.open()
            self.receive()
            self.assertEqual(await self.service.serve(asyncio.Event(),once=True),0)
        self.assertEqual(self.service.results.status(),{'draft':1})
        self.assertEqual(self.requests,[])

    async def test_local_event_without_verified_origin_cannot_publish_a_comment(self):
        self.service.inbox.receive(source_id='github',event_id='local-only',actor_id='github:user:1',text='inspect',
            thread_ids=['issue/1'],human=True,explicit=True)
        self.assertEqual(await self.service.serve(asyncio.Event(),once=True),0)
        self.assertEqual(self.service.results.status(),{'draft':1})
        self.assertEqual(self.requests,[])

    async def test_scope_is_derived_from_config_not_payload_urls(self):
        value = self.payload()
        value['comment']['issue_url'] = 'https://attacker.invalid/private'
        value['issue']['url'] = 'https://attacker.invalid/private'
        self.receive(value)
        self.assertEqual(await self.service.serve(asyncio.Event(),once=True),0)
        self.assertTrue(all(path.startswith(('/repos/fixture/repo','/user')) for _,path in self.requests))
        self.assertEqual(self.post_count,1)

    async def test_shutdown_waits_for_accepted_post_and_keeps_sent_receipt(self):
        await self.draft()
        request = self.service.github.client.request
        entered,release = asyncio.Event(),asyncio.Event()
        async def held(method,*args,**kwargs):
            result = await request(method,*args,**kwargs)
            if method == 'POST':
                entered.set()
                await release.wait()
            return result
        with patch.object(self.service.github.client,'request',side_effect=held):
            tick = asyncio.create_task(self.service.tick())
            await asyncio.wait_for(entered.wait(),3)
            close = asyncio.create_task(self.service.aclose())
            await asyncio.sleep(0)
            self.assertFalse(close.done())
            release.set()
            await asyncio.wait_for(asyncio.gather(tick,close),5)
        self.assertEqual(self.post_count,1)
        self.assertEqual(self.service.github.status()['outbound'],{'sent':1})

    async def test_authenticated_api_redirect_is_rejected_before_forwarding_credentials(self):
        await self.draft()
        self.redirect_user = True
        with self.assertRaises(HarnessError) as caught:
            await self.service.github.tick()
        self.assertEqual(caught.exception.code,'superagent/github-response')
        self.assertEqual(self.requests,[('GET','/user')])
        self.assertEqual(self.post_count,0)

    async def test_concurrent_sender_ticks_claim_one_remote_post(self):
        await self.draft()
        await asyncio.gather(self.service.github.tick(),self.service.github.tick())
        self.assertEqual(self.post_count,1)
        self.assertEqual(self.service.github.status()['outbound'],{'sent':1})

    async def test_local_acknowledgement_failure_preserves_original_error_and_remote_fact(self):
        await self.draft()
        settle = self.service.github._settle
        original = OSError('local sent receipt failed')
        failed = False
        def fail_once(row,state,*args):
            nonlocal failed
            if state == 'sent' and not failed:
                failed = True
                raise original
            return settle(row,state,*args)
        with patch.object(self.service.github,'_settle',side_effect=fail_once):
            with self.assertRaises(OSError) as caught:
                await self.service.github.tick()
            self.assertIs(caught.exception,original)
        self.assertEqual(self.service.github.status()['outbound'],{'sent':1})
        self.assertEqual(self.post_count,1)

    async def test_corrupt_verified_queue_is_preserved_without_model_execution(self):
        headers,raw = self.signed()
        self.service.github.receive(headers,raw,queued=True)
        with closing(sqlite3.connect(self.service.github.path)) as db,db:
            db.execute("UPDATE origins SET normalized='{}'")
        with self.assertRaises(HarnessError) as caught:
            self.service.github.drain()
        self.assertEqual(caught.exception.code,'superagent/corrupt')
        self.assertEqual(self.router_transport.requests,[])
        with closing(sqlite3.connect(self.service.github.path)) as db:
            self.assertEqual(db.execute('SELECT normalized FROM origins').fetchone()[0],'{}')

    async def test_api_repository_letter_case_does_not_break_exact_issue_identity(self):
        await self.draft()
        request = self.service.github.client.request
        async def canonical_case(method,*args,**kwargs):
            value = await request(method,*args,**kwargs)
            if method == 'POST':
                value['issue_url'] = self.api_url+'/repos/Fixture/Repo/issues/1'
            return value
        with patch.object(self.service.github.client,'request',side_effect=canonical_case):
            await self.service.github.tick()
        self.assertEqual(self.service.github.status()['outbound'],{'sent':1})
        self.assertEqual(self.post_count,1)

    async def test_unrelated_unknown_send_does_not_block_new_authorized_work(self):
        await self.draft()
        request = self.service.github.client.request
        async def first_unknown(method,*args,**kwargs):
            if method == 'POST':
                raise OSError('first outcome unknown')
            return await request(method,*args,**kwargs)
        with patch.object(self.service.github.client,'request',side_effect=first_unknown):
            with self.assertRaises(OSError):
                await self.service.github.tick()
        self.receive(self.payload(comment_id=10,body='@agent inspect again'))
        self.assertEqual(await self.service.serve(asyncio.Event(),once=True),2)
        self.assertEqual(len(self.worker_transport.requests),2)
        self.assertEqual(self.post_count,1)
        self.assertEqual(self.service.github.status()['outbound'],{'unknown':1,'sent':1})

    async def test_cancelled_start_waits_for_bound_listener_and_releases_instance_lease(self):
        self.spec['state_dir'] = 'cancelled-start'
        self.save()
        original = asyncio.start_server
        entered,release = asyncio.Event(),asyncio.Event()
        servers = []
        async def held(*args,**kwargs):
            server = await original(*args,**kwargs)
            servers.append(server)
            entered.set()
            await release.wait()
            return server
        with patch('harness.app.superagent_http.asyncio.start_server',side_effect=held):
            operation = asyncio.create_task(self.open())
            await asyncio.wait_for(entered.wait(),3)
            operation.cancel()
            await asyncio.sleep(0)
            self.assertFalse(operation.done())
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(operation,5)
        self.assertEqual(len(servers),1)
        self.assertFalse(servers[0].is_serving())
        self.assertEqual(servers[0].sockets,())
        restarted = await self.open()
        self.assertTrue(restarted.github.server.server.is_serving())
