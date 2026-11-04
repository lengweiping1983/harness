"""本地文件信道、独立宿主、连续执行与显式 CLI 的端到端回归。"""
import asyncio
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from harness.cli import build_parser, main
from harness.app.git_process import run_git
from harness.app.superagent_service import SuperagentService, read_config
from harness.core.errors import HarnessError
from harness.core.language import current_language
from harness.i18n import tr
from tests.agent_configuration_fixture import RecordingTransport


class SuperagentOnceDeadlineTests(unittest.IsolatedAsyncioTestCase):
    async def test_batch_deadline_preserves_accepted_work_and_original_status(self):
        release = asyncio.Event()
        task = asyncio.create_task(release.wait())
        status = {'inputs': {'pending': 1}, 'results': {}, 'batches': [], 'cursor': 0, 'input_errors': []}
        service = SuperagentService.__new__(SuperagentService)
        service.spec, service.closed = {'once_timeout': 10, 'interval': .01}, False
        service.guidance_files, service._input_count, service._draft_cursor = None, 0, 0
        service.hosts = [SimpleNamespace(runtime=SimpleNamespace(records=SimpleNamespace(active={'original': task})),
                                        jobs=SimpleNamespace(active_owners=lambda: set()))]
        service.results = SimpleNamespace(uncollected=lambda: False, drafts=lambda **_: [])
        service.inbox = SimpleNamespace(events=lambda **_: {'events': []})
        service.channels, service.router = [], SimpleNamespace()
        service.tick = AsyncMock(return_value=status)
        try:
            for language in ('en', 'zh-CN', 'en'):
                with current_language.set(language), \
                        patch('harness.app.superagent_service.time', SimpleNamespace(monotonic=Mock(side_effect=[100, 111]))):
                    with self.assertRaises(HarnessError) as caught:
                        await service.serve(asyncio.Event(), once=True)
                self.assertEqual(caught.exception.code, 'superagent/once-timeout')
                self.assertIs(service._serve_error, caught.exception)
                self.assertIs(service.hosts[0].runtime.records.active['original'], task)
                self.assertFalse(task.done())
                self.assertEqual(status['inputs']['pending'], 1)
        finally:
            release.set()
            await task


class ServiceRouterTransport(RecordingTransport):
    @staticmethod
    def reply(request):
        frame = None
        for message in reversed(request['messages']):
            content = message.get('content','')
            if isinstance(content, list):
                content = ''.join(part.get('text','') for part in content)
            if isinstance(content,str):
                for marker in ('\n\n{"events":','\n\n{"report_id":'):
                    if marker in content:
                        frame = json.loads(content[content.index(marker) + 2:])
                        break
            if frame is not None:
                break
        if frame is None:
            raise AssertionError('请求缺少协调批次或内部结果。')
        return ({'assignments': [{'trigger_id': item['id'], 'task_id': 'new:coding'} for item in frame['events']]}
                 if 'events' in frame else {'reply': 'The Worker reported a result for review.'})

    async def post_stream(self, url, *, headers, body, timeout):
        request = json.loads(body)
        self.requests.append(request)
        value = self.reply(request)
        yield ('data: ' + json.dumps({'choices': [{'delta': {'content': json.dumps(value)}, 'finish_reason': 'stop'}]}) + '\n\n').encode('utf-8')
        yield b'data: [DONE]\n\n'


class SuperagentServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        environment = patch.dict(os.environ, {'HARNESS_API_KEY':'fixture-only','OPENROUTER_API_KEY':'fixture-only',
            'HARNESS_HOME':str(self.root / 'unused-personal-home')})
        environment.start()
        self.addCleanup(environment.stop)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        self.git('init','--initial-branch=main')
        self.git('config','user.name','Fixture')
        self.git('config','user.email','fixture@example.invalid')
        (self.repo / 'main.txt').write_text('fixture\n',encoding='utf-8')
        self.git('add','main.txt')
        self.git('commit','-m','fixture')
        self.path = self.root / 'config.json'
        self.spec = {'version':1,'instance_id':'team','state_dir':'state','language':'en',
            'router_model':'router-model','worker_model':'worker-model','base_url':'https://fixture.invalid/v1',
            'permission_policy':'read_only','interval':0.01,'once_timeout':10,
            'sources':{'github':{'mode':'requests','actors':['alice'],'threads':['issue/1','issue/2']}},
            'profiles':{'coding':{'repo':'repo','description':'Inspect project regressions','sources':['github'],'tools':['read']}}}
        self.save()
        self.work_transport, self.router_transport = RecordingTransport(), ServiceRouterTransport()

    def git(self,*args):
        result = run_git(self.repo,*args)
        if result.returncode:
            raise RuntimeError(result.stderr.decode('utf-8'))

    def save(self):
        self.path.write_text(json.dumps(self.spec),encoding='utf-8')

    async def open(self, *, cleanup=True):
        service = await SuperagentService.open(self.path, worker_transport=self.work_transport,
                                               router_transport=self.router_transport)
        if cleanup:
            self.addAsyncCleanup(service.aclose)
        return service

    def event(self, service, name='one.json', **overrides):
        value = {**dict(source_id='github',event_id=name,actor_id='alice',text='/goal source $skill',
            thread_ids=['issue/1'],human=True,explicit=True), **overrides}
        (service.root / 'incoming' / name).write_text(json.dumps(value),encoding='utf-8')

    async def test_once_runs_full_local_pipeline_and_publishes_reviewable_draft(self):
        service = await self.open()
        self.event(service)
        result = await asyncio.wait_for(service.serve(asyncio.Event(),once=True),10)
        self.assertEqual(result,0)
        self.assertEqual(len(self.work_transport.requests),1)
        self.assertEqual(len(self.router_transport.requests),2)
        drafts = list((service.root / 'drafts').glob('*.json'))
        self.assertEqual(len(drafts),1)
        draft = json.loads(drafts[0].read_text(encoding='utf-8'))
        self.assertEqual(draft['state'],'draft')
        self.assertEqual(draft['reply'],'The Worker reported a result for review.')
        self.assertNotIn('sent',draft)
        self.assertEqual(len(list((service.root / 'receipts').glob('*.json'))),1)
        self.assertTrue((service.root / 'incoming' / 'one.json').exists())
        status = json.loads((service.root / 'status.json').read_text(encoding='utf-8'))
        self.assertEqual(status['results'],{'draft':1})
        self.assertEqual(status['workers']['states'],{'ready':1})
        self.assertEqual([tool['function']['name'] for tool in self.work_transport.requests[0]['tools']],['read'])
        self.assertNotEqual(service.worker_host.config.home,service.router_host.config.home)

    async def check_file_edit(self, policy):
        self.spec['permission_policy'] = policy
        self.spec['profiles']['coding']['tools'] = ['read','edit']
        self.save()
        class WriterTransport(RecordingTransport):
            async def post_stream(self,url,*,headers,body,timeout):
                self.requests.append(json.loads(body))
                index = len(self.requests)
                if index <= 2:
                    name,args = (('read',{'path':'main.txt'}) if index == 1 else
                                 ('edit',{'path':'main.txt','old_string':'fixture','new_string':'fixed'}))
                    delta = {'tool_calls':[{'index':0,'id':'call-'+str(index),'type':'function',
                        'function':{'name':name,'arguments':json.dumps(args)}}]}
                    reason = 'tool_calls'
                else:
                    delta,reason = {'content':'Worker attempt complete; inspect the recorded tool results.'},'stop'
                yield ('data: '+json.dumps({'choices':[{'delta':delta,'finish_reason':reason}]})+'\n\n').encode('utf-8')
                yield b'data: [DONE]\n\n'
        self.work_transport = WriterTransport()
        service = await self.open()
        self.event(service)
        self.assertEqual(await service.serve(asyncio.Event(),once=True),0)
        session = service.worker_host.get_session(service.factory.status()['recent'][0]['session_id'])
        expected = 'fixed\n' if policy == 'full_access' else 'fixture\n'
        self.assertEqual((Path(session.header.cwd) / 'main.txt').read_text(encoding='utf-8'),expected)
        self.assertEqual((self.repo / 'main.txt').read_text(encoding='utf-8'),'fixture\n')
        self.assertEqual(len(self.work_transport.requests),3)
        self.assertEqual(session.latest('tool/result').data['results'][0]['is_error'],policy == 'read_only')
        self.assertEqual(service.results.status(),{'draft':1})

    async def test_authorized_worker_can_edit_its_worktree_using_real_file_tools(self):
        await self.check_file_edit('full_access')

    async def test_read_only_policy_rejects_a_tool_that_is_visible_in_the_profile(self):
        await self.check_file_edit('read_only')

    async def test_restart_reuses_original_conversations_and_does_not_reexecute(self):
        service = await self.open()
        self.event(service)
        self.assertEqual(await service.serve(asyncio.Event(),once=True),0)
        before = {str(path.relative_to(service.root)):path.read_bytes() for path in (service.root / 'drafts').glob('*.json')}
        ids = [header.id for header in service.worker_host.sessions.list_sessions()]
        router_id = service.router.session_id
        await service.aclose()
        reopened = await self.open()
        with patch.object(reopened.worker_host.runtime,'submit',side_effect=AssertionError('工作被重放')), \
                patch.object(reopened.router_host.runtime,'submit',side_effect=AssertionError('研判被重放')):
            self.assertEqual(await reopened.serve(asyncio.Event(),once=True),0)
        self.assertEqual([header.id for header in reopened.worker_host.sessions.list_sessions()],ids)
        self.assertEqual(reopened.router.session_id,router_id)
        self.assertEqual({str(path.relative_to(reopened.root)):path.read_bytes() for path in (reopened.root / 'drafts').glob('*.json')},before)

    async def test_runtime_lease_rejects_second_owner_before_host_construction(self):
        await self.open()
        with patch('harness.app.superagent_service.Harness',side_effect=AssertionError('第二个宿主被创建')):
            with self.assertRaises(HarnessError) as caught:
                await SuperagentService.open(self.path,worker_transport=self.work_transport,router_transport=self.router_transport)
        self.assertEqual(caught.exception.code,'superagent/runtime-busy')

    async def test_bad_event_is_retained_and_does_not_prevent_other_work(self):
        service = await self.open()
        malformed = service.root / 'incoming' / 'bad.json'
        malformed.write_text('{"source_id":"github","source_id":"other"}',encoding='utf-8')
        self.event(service)
        self.assertEqual(await service.serve(asyncio.Event(),once=True),2)
        self.assertEqual(malformed.read_text(encoding='utf-8'),'{"source_id":"github","source_id":"other"}')
        status = json.loads((service.root / 'status.json').read_text(encoding='utf-8'))
        self.assertEqual(status['input_errors'],[{'file':'bad.json','code':'superagent/invalid'}])
        self.assertEqual(status['results'],{'draft':1})

    async def test_filtered_event_generates_receipt_without_any_model_request(self):
        service = await self.open()
        self.event(service,actor_id='outsider')
        self.assertEqual(await service.serve(asyncio.Event(),once=True),0)
        receipt = json.loads(next((service.root / 'receipts').glob('*.json')).read_text(encoding='utf-8'))
        self.assertEqual(receipt['receipt']['disposition'],'actor_denied')
        self.assertEqual(self.work_transport.requests,[])
        self.assertEqual(self.router_transport.requests,[])

    async def test_changed_configuration_is_rejected_without_rebinding_state(self):
        service = await self.open()
        await service.aclose()
        self.spec['profiles']['coding']['tools'] = ['read','write']
        self.save()
        with patch('harness.app.superagent_service.Harness',side_effect=AssertionError('旧配置被替换')):
            with self.assertRaises(HarnessError) as caught:
                await SuperagentService.open(self.path,worker_transport=self.work_transport,router_transport=self.router_transport)
        self.assertEqual(caught.exception.code,'superagent/conflict')

    async def test_shutdown_waits_for_creation_and_prevents_late_execution(self):
        service = await self.open()
        self.event(service)
        await service.tick()
        await asyncio.gather(*(record.task for record in tuple(service.router_host.runtime.records.active.values())))
        entered, release = threading.Event(),threading.Event()
        create = service.worker_host.worktrees.create_team
        def gated(*args,**kwargs):
            entered.set()
            if not release.wait(5):
                raise TimeoutError('创建屏障未释放。')
            return create(*args,**kwargs)
        with patch.object(service.worker_host.worktrees,'create_team',side_effect=gated):
            tick = asyncio.create_task(service.tick())
            self.assertTrue(await asyncio.wait_for(asyncio.to_thread(entered.wait),2))
            close = asyncio.create_task(service.aclose())
            await asyncio.sleep(0)
            self.assertFalse(close.done())
            release.set()
            await asyncio.wait_for(asyncio.gather(tick,close),5)
        self.assertEqual(len(service.worker_host.worktrees.list()),1)
        self.assertEqual(service.worker_host.sessions.list_sessions(),[])
        self.assertEqual(self.work_transport.requests,[])
        self.assertEqual(json.loads((service.root / 'status.json').read_text(encoding='utf-8'))['runtime_state'],'stopped')

    async def test_existing_modified_draft_is_preserved_with_explicit_conflict(self):
        service = await self.open(cleanup=False)
        async def close_failed_publication():
            with self.assertRaises(HarnessError):
                await service.aclose()
            self.assertTrue(all(host.runtime.closed for host in service.hosts))
        self.addAsyncCleanup(close_failed_publication)
        self.event(service)
        await service.serve(asyncio.Event(),once=True)
        path = next((service.root / 'drafts').glob('*.json'))
        path.write_text('user edits',encoding='utf-8')
        service._draft_cursor = 0
        with self.assertRaises(HarnessError) as caught:
            await service.tick()
        self.assertEqual(caught.exception.code,'superagent/conflict')
        self.assertEqual(path.read_text(encoding='utf-8'),'user edits')
        # 已观察的发布失败仍由关闭入口报告，不能以重跑覆盖原件。
        with self.assertRaises(HarnessError):
            await service.aclose()

    async def test_config_validation_rejects_unknown_fields_without_creating_state(self):
        self.spec['unexpected'] = True
        self.save()
        with self.assertRaises(HarnessError):
            read_config(self.path)
        self.assertFalse((self.root / 'state').exists())

    async def test_missing_explicit_credentials_does_not_read_personal_state_or_create_hosts(self):
        with patch.dict(os.environ,{'HARNESS_API_KEY':'','OPENROUTER_API_KEY':''}), \
                patch('harness.app.superagent_service.Harness',side_effect=AssertionError('模型宿主被创建')):
            with self.assertRaises(HarnessError) as caught:
                await SuperagentService.open(self.path)
        self.assertEqual(caught.exception.code,'superagent/credentials-required')
        self.assertFalse((self.root / 'state').exists())

    async def test_unconsumed_input_pages_are_not_mistaken_for_a_drained_batch(self):
        service = await self.open()
        for index in range(20):
            self.event(service,name=str(index).zfill(2)+'.json',actor_id='outsider')
        self.assertEqual(await service.serve(asyncio.Event(),once=True),0)
        self.assertEqual(len(list((service.root / 'receipts').glob('*.json'))),20)
        self.assertEqual(len(service.inbox.events(limit=100)['events']),20)
        self.assertEqual(service.router.status()['cursor'],20)
        self.assertEqual(self.router_transport.requests,[])

    async def test_real_cli_subprocess_drives_loopback_model_and_writes_draft(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):
                pass
            def do_GET(self):
                body = json.dumps({'data':[{'id':name,'context_length':65536,'max_output_tokens':4096}
                                           for name in ('router-model','worker-model')]}).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def do_POST(self):
                value = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.append(value)
                text = 'Worker report for review' if value['model'] == 'worker-model' else json.dumps(ServiceRouterTransport.reply(value))
                body = ('data: '+json.dumps({'choices':[{'delta':{'content':text},'finish_reason':'stop'}]})+'\n\ndata: [DONE]\n\n').encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type','text/event-stream')
                self.send_header('Content-Length',str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        self.spec['base_url'] = 'http://127.0.0.1:' + str(server.server_port) + '/v1'
        self.save()
        incoming = self.root / 'state' / 'incoming'
        incoming.mkdir(parents=True)
        (incoming / 'one.json').write_text(json.dumps(dict(source_id='github',event_id='one',actor_id='alice',
            text='Inspect this repository',thread_ids=['issue/1'],human=True,explicit=True)),encoding='utf-8')
        process = None
        try:
            process = await asyncio.create_subprocess_exec(sys.executable,'-m','harness.cli','--language','en',
                '--superagent',str(self.path),'--superagent-once',cwd=Path(__file__).resolve().parents[1],
                stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,env=dict(os.environ))
            output,error = await asyncio.wait_for(process.communicate(),20)
            self.assertEqual(process.returncode,0,error.decode('utf-8'))
            self.assertEqual(output,b'')
            self.assertIn('Superagent state directory',error.decode('utf-8'))
            self.assertEqual([request['model'] for request in requests],['router-model','worker-model','router-model'])
            drafts = list((self.root / 'state' / 'drafts').glob('*.json'))
            self.assertEqual(len(drafts),1)
            self.assertEqual(json.loads(drafts[0].read_text(encoding='utf-8'))['state'],'draft')
            self.assertFalse((self.root / 'unused-personal-home').exists())
        finally:
            if process is not None and process.returncode is None:
                process.kill()
                await process.communicate()
            await asyncio.to_thread(server.shutdown)
            server.server_close()
            await asyncio.to_thread(thread.join)


class SuperagentCliTests(unittest.TestCase):
    def test_help_tracks_english_chinese_english_before_constructing_hosts(self):
        for language in ('en','zh-CN','en'):
            with current_language.set(language):
                parser = build_parser()
                actions = {action.dest:action for action in parser._actions}
                self.assertEqual(actions['superagent'].help,tr('superagent.cli.help'))
                self.assertEqual(actions['superagent_once'].help,tr('superagent.cli.once'))

    def test_mode_conflicts_are_rejected_before_constructing_standard_host(self):
        for args in (['--superagent-once'],['--superagent','config.json','--ui'],
                     ['--superagent','config.json','--memory-export','file.jsonl'],
                     ['--superagent','config.json','--model','other']):
            with self.subTest(args=args),redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()), \
                    patch('harness.cli.make_harness',side_effect=AssertionError('普通宿主被创建')):
                with self.assertRaises(SystemExit) as caught:
                    main(args)
                self.assertEqual(caught.exception.code,2)

    def test_cli_routes_to_dedicated_runner(self):
        from unittest.mock import AsyncMock
        with redirect_stderr(io.StringIO()),patch('harness.cli.make_harness',side_effect=AssertionError('普通宿主被创建')), \
                patch('harness.superagent_cli.run',new=AsyncMock(return_value=0)) as runner:
            self.assertEqual(main(['--superagent','state.json','--superagent-once']),0)
        runner.assert_awaited_once_with(Path('state.json'),once=True)
