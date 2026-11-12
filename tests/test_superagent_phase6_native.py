"""单设备可信宿主入口的真实运行；不把原生执行当作 OS 沙箱。"""
from __future__ import annotations

import asyncio
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from harness.app.git_process import run_git
from harness.app.superagent_config import read_config
from harness.app.superagent_local import submit_local
from harness.app.superagent_remote_client import RemoteClient
from harness.app.superagent_remote_protocol import frame
from harness.app.superagent_secret_broker import ModelBroker,NativeBrokerTransport,BROKER_SCOPE
from harness.app.superagent_service import SuperagentService
from harness.core.errors import HarnessError
from harness.core.language import current_language
from harness.superagent_worker import export_worker_config,read_worker_config,open_worker_node
from tests.superagent_product_fixture import product_config,ProductTransport


class NativeWorkerTests(unittest.IsolatedAsyncioTestCase):
    def configuration(self,root,*,model_url='https://fixture.invalid/v1',file_mode=False):
        path,raw=product_config(root)
        if file_mode:raw['profiles']['work']['tools']=['read','write']
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        raw['base_url']=model_url
        raw['secrets']={'worker':'FIXTURE_NATIVE_DEVICE_KEY'}
        raw['execution']={'devices':[{'device_id':'only-worker','url':'http://127.0.0.1:'+str(port),
            'key_ref':'secret://worker','capacity':1,'runner':'native'}],'lease_seconds':30}
        path.write_text(json.dumps(raw))
        worker_path=root/'worker.json';worker=export_worker_config(path,'only-worker',worker_path)
        state=root/'device';state.mkdir()
        replica=root/'device-repo'
        self.assertEqual(run_git(root,'clone',str(root/'repo'),str(replica)).returncode,0)
        worker['host_state']=str(state)
        for profile in worker['profiles'].values():profile['repo']=str(replica)
        worker_path.write_text(json.dumps(worker))
        return path,worker_path,read_worker_config(worker_path)

    async def test_one_native_worker_opens_without_docker_unix_socket_or_container_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve();path,worker_path,config=self.configuration(root)
            self.assertEqual(len(read_config(path)['execution']['devices']),1)
            self.assertIsNone(config['sandbox']);self.assertEqual(config['runner'],'native')
            with (patch('harness.app.superagent_sandbox.sandbox_command',side_effect=AssertionError('Docker must not be probed')),
                 patch('harness.app.superagent_sandbox.SandboxNode.open',side_effect=AssertionError('Docker must not start')),
                 patch('harness.app.superagent_secret_broker.SecretBroker',side_effect=AssertionError('Unix Socket must not open'))):
                async with open_worker_node(config,api_key='fixture-model-key',broker_forward=lambda body:b'data: [DONE]\n\n') as node:
                    identity=await node.dispatch(frame(instance_id=config['instance_id'],device_id=config['device_id'],
                        identity_hash=config['identity_hash'],operation='identity',params={}))
                    self.assertEqual(identity['runner'],'native');self.assertEqual(identity['sandbox'],'none')
                    from harness.app.superagent_remote_protocol import worker_openapi
                    from harness.tools.schema import validate as validate_schema
                    self.assertEqual(validate_schema(identity,worker_openapi()['components']['schemas']['WorkerIdentity']),[])
                    lease={'mission_id':'original','device_id':config['device_id'],'generation':1,'expires_at':int(time.time())+30}
                    def message(operation):return frame(instance_id=config['instance_id'],device_id=config['device_id'],identity_hash=config['identity_hash'],operation=operation,params={},lease=lease)
                    await node.dispatch(message('heartbeat'));self.assertTrue(node.lease_valid('original',1))
                    await node.dispatch(message('revoke'));self.assertFalse(node.lease_valid('original',1))
                    with self.assertRaises(HarnessError):await node.dispatch(message('heartbeat'))
            self.assertTrue(worker_path.exists());self.assertTrue((root/'device'/'worker-native.sqlite3').is_file())

    async def test_native_transport_consumes_shared_capability_and_actual_bilingual_errors(self):
        calls=[];valid=True
        broker=ModelBroker(upstream='https://fixture.invalid/v1',api_key='do-not-export',models={'fixture'},
            lease_valid=lambda mission,generation:valid,forward=lambda body:calls.append(body) or b'data: [DONE]\n\n')
        self.addAsyncCleanup(broker.close);transport=NativeBrokerTransport(broker)
        token=broker.grant(mission_id='original',generation=1,model='fixture',max_requests=2,expires_at=int(time.time())+30)
        with BROKER_SCOPE.set(token):
            received=[raw async for raw in transport.post_stream('https://foreign.invalid',headers={'Authorization':'foreign'},body=b'{"model":"fixture"}',timeout=5)]
            self.assertEqual(received,[b'data: [DONE]\n\n']);valid=False;errors=[]
            for language in ('en','zh-CN','en'):
                with current_language.set(language),self.assertRaises(HarnessError) as caught:
                    _=[raw async for raw in transport.post_stream('https://foreign.invalid',headers={},body=b'{"model":"fixture"}',timeout=5)]
                errors.append(str(caught.exception))
            self.assertEqual(errors[0],errors[2]);self.assertNotEqual(errors[0],errors[1])
        self.assertEqual(calls,[{'model':'fixture'}]);self.assertNotIn('do-not-export',json.dumps(broker.audit))

    async def test_native_config_rejects_sandbox_limits_and_docker_failure_never_selects_native(self):
        from harness.app.superagent_common import _error
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve();_,path,config=self.configuration(root)
            malformed={**config,'sandbox':{'memory_mb':512}}
            path.write_text(json.dumps(malformed))
            with self.assertRaises(HarnessError):read_worker_config(path)
            config['runner']='docker';config['sandbox']={'image':'fixture@sha256:'+'a'*64,'memory_mb':512,'cpus':1,'pids':32,'disk_bytes':10485760}
            with (patch('harness.app.superagent_sandbox.sandbox_command',side_effect=_error('remote.sandbox-unavailable')),
                 patch('harness.superagent_worker.native_node') as native):
                with self.assertRaises(HarnessError):
                    async with open_worker_node(config,api_key='fixture'):self.fail('Unavailable Docker cannot yield a Worker')
                native.assert_not_called()

    async def test_single_native_cli_device_completes_and_reopens_original_run_without_docker(self):
        await self.native_cli()

    async def test_native_cli_file_task_passes_artifact_integration_and_independent_review(self):
        await self.native_cli(file_mode=True)

    async def native_cli(self,*,file_mode=False):
        requests=[]
        roles=ProductTransport();work=None
        if file_mode:
            from tests.test_superagent_service import ProductEntryTests
            roles,work=ProductEntryTests().artifact_transports()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.append((self.path,body,self.headers.get('Authorization')))
                if work is not None:
                    async def stream():return b''.join([raw async for raw in work.post_stream('http://fixture/v1',headers={},body=json.dumps(body).encode(),timeout=60)])
                    payload=asyncio.run(stream())
                else:
                    report={'status':'completed','summary':'Native CLI verified.','reply':'Original native result.'}
                    payload=('data: '+json.dumps({'choices':[{'delta':{'content':json.dumps(report)},'finish_reason':'stop'}]})+'\n\ndata: [DONE]\n\n').encode()
                self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever);thread.start()
        async def close_model():
            await asyncio.to_thread(server.shutdown);await asyncio.to_thread(server.server_close);await asyncio.to_thread(thread.join)
        self.addAsyncCleanup(close_model)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve();path,worker_path,config=self.configuration(root,model_url='http://127.0.0.1:'+str(server.server_port)+'/v1',file_mode=file_mode)
            key='fixture-single-native-key-at-least-32-bytes';process=None;service=None
            environment={**os.environ,config['key_env']:key,config['api_key_env']:'fixture-provider-key','PYTHONUTF8':'1',
                'DOCKER_HOST':'unix:///unavailable-native-test.sock','DOCKER_CONTEXT':'unavailable-native-test'}
            async def start():
                nonlocal process
                process=await asyncio.create_subprocess_exec(sys.executable,'-m','harness.superagent_worker','--config',str(worker_path),
                    env=environment,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
                line=await asyncio.wait_for(process.stdout.readline(),15)
                if not line:
                    self.fail((await process.stderr.read()).decode())
            async def stop():
                if process is not None and process.returncode is None:
                    process.send_signal(signal.SIGTERM)
                    try:await asyncio.wait_for(process.wait(),15)
                    except TimeoutError:process.kill();await process.wait();raise
                    if os.name!='nt':self.assertEqual(process.returncode,0,(await process.stderr.read()).decode())
            try:
                await start()
                with patch.dict(os.environ,{config['key_env']:key,'HARNESS_API_KEY':'fixture-coordinator'}):
                    service=await SuperagentService.open(path,coordinator_transport=roles)
                    submit_local(path,'Fix the typo.',request_id='native-once')
                    for _ in range(30):
                        await service.tick()
                        with service.coordinator._store.transaction() as db:
                            missions=[dict(row) for row in db.execute('SELECT * FROM missions')]
                        if missions and missions[0]['state']=='completed':break
                        await asyncio.sleep(.02)
                    self.assertEqual(missions[0]['state'],'completed')
                    with service.coordinator._store.transaction() as db:
                        self.assertEqual(db.execute('SELECT count(*) FROM remote_devices').fetchone()[0],1)
                        self.assertEqual(db.execute('SELECT count(*) FROM assignments').fetchone()[0],3 if file_mode else 1)
                        worker=dict(db.execute('SELECT * FROM workers').fetchone())
                        assignment_id=db.execute('SELECT assignment_id FROM assignments WHERE worker_id=?',(worker['worker_id'],)).fetchone()[0]
                        lease=dict(db.execute('SELECT mission_id,device_id,generation,expires_at FROM remote_mission_leases').fetchone())
                        self.assertEqual(db.execute('SELECT state FROM outbox_messages').fetchone()[0],'sent')
                        if file_mode:
                            self.assertEqual(db.execute('SELECT count(*) FROM artifacts').fetchone()[0],2)
                            self.assertEqual(db.execute('SELECT state FROM reviews').fetchone()[0],'passed')
                            self.assertEqual(db.execute("SELECT count(*) FROM deliveries WHERE state='applied'").fetchone()[0],2)
                    if file_mode:
                        self.assertEqual(len(list((root/'device').rglob('result.txt'))),3)
                        self.assertFalse((root/'repo'/'result.txt').exists());self.assertFalse((root/'device-repo'/'result.txt').exists())
                    assignment=service.coordinator._execution_service._assignment(assignment_id)
                    await service.aclose();service=None
                await stop();await start()
                client=RemoteClient(url=read_config(path)['execution']['devices'][0]['url'],key=key.encode(),instance_id=config['instance_id'],device_id=config['device_id'],identity_hash=config['identity_hash'])
                found=await asyncio.to_thread(client.request,'reconcile',{'worker':worker,'assignment':assignment},lease=lease)
                self.assertEqual(found['run_id'],assignment['run_id']);self.assertEqual(found['status'],'completed')
                self.assertEqual(len(requests),6 if file_mode else 1);self.assertEqual(requests[0][0],'/v1/chat/completions')
                self.assertEqual(requests[0][2],'Bearer fixture-provider-key')
                audit=(root/'device'/'model-proxy-audit.jsonl').read_text()
                self.assertNotIn('fixture-provider-key',audit);self.assertNotIn(key,audit)
            finally:
                from harness.core.cleanup import raise_cleanup_errors
                errors=[]
                if service is not None:
                    try:await service.aclose()
                    except BaseException as exc:errors.append(exc)
                try:await stop()
                except BaseException as exc:errors.append(exc)
                raise_cleanup_errors(errors)
