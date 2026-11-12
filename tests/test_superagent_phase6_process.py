"""独立原生进程、短期代理和 stdio 传输的集成验证；不作为容器隔离证据。"""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

from harness.app.superagent_coordinator_store import _hash,_json
from harness.app.superagent_executor import WorkerProfile
from harness.app.superagent_remote_protocol import encode,decode,frame,operation_key
from harness.app.superagent_sandbox import SandboxNode
from harness.app.superagent_secret_broker import SecretBroker
from tests.superagent_product_fixture import product_config


class NativeProcessTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_child_process_uses_unix_broker_and_preserves_original_submission(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        root=Path(temporary.name).resolve();_,spec=product_config(root)
        config={'instance_id':'product','device_id':'child','identity_hash':'a'*64,'state_dir':str(root/'native'),
            'host_state':str(root/'native'),'model':'fixture','language':'en','permission_policy':'full_access',
            'profiles':spec['profiles'],'default_profiles':spec['default_profiles'],'max_workers':32,
            'coordinator_profiles':{key:WorkerProfile(profile_id=key,**value).fingerprint for key,value in spec['profiles'].items()},
            'coordinator_repos':{key:str(root/'repo') for key in spec['profiles']},'sandbox':{'disk_bytes':100*1024*1024}}
        (root/'native').mkdir()
        answer={'status':'completed','summary':'Separate process.','reply':'Confirmed.'}
        chunks=('data: '+json.dumps({'choices':[{'delta':{'content':json.dumps(answer)},'finish_reason':'stop'}]})+'\n\ndata: [DONE]\n\n').encode()
        calls=[];node=None
        def forward(body):calls.append(body);return chunks
        broker=SecretBroker(root/'model.sock',upstream='https://fixture.invalid/v1',api_key='never-in-child',models={'fixture'},
            lease_valid=lambda mission,generation:bool(node and node.lease_valid(mission,generation)),forward=forward)
        self.addAsyncCleanup(broker.close)
        config['broker_socket']=str(root/'model.sock')
        environment={'PATH':os.environ.get('PATH','/usr/bin:/bin'),'PYTHONUTF8':'1','HARNESS_WORKER_SANDBOX':'1'}
        process=await asyncio.create_subprocess_exec(sys.executable,'-m','harness.superagent_worker','--stdio',stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,env=environment,limit=8*1024*1024+1)
        # 测试注入原生进程用于验证帧与代理；生产入口始终通过固定 Docker 命令创建进程。
        node=SandboxNode(process,config,broker,{},['fixture-docker','--name','fixture-child'])
        self.addAsyncCleanup(node.close)
        process.stdin.write(encode(config)+b'\n');await process.stdin.drain()
        self.assertEqual(decode(await asyncio.wait_for(process.stdout.readline(),15)),{'ready':True,'protocol':1})
        mission,job,stage='mission','job',1;profile=WorkerProfile(profile_id='work',**spec['profiles']['work'])
        base=node.config['profiles']['work']['repo']
        from harness.app.git_process import run_git
        commit=run_git(root/'repo','rev-parse','HEAD').stdout.decode().strip()
        worker={'worker_id':'worker_'+_hash(_json(['product',mission,job,stage]))[:32],'ticket_id':'ticket','mission_id':mission,
            'job_id':job,'stage':stage,'profile_id':'work','repo':base,'base_commit':commit,'runtime_instance_id':'product',
            'state':'workspace_creating','workspace_id':None,'session_id':None,'identity_hash':_hash(_json({'instance_id':'product',
                'ticket_id':'ticket','mission_id':mission,'job_id':job,'stage':stage,'profile_id':'work','profile_fingerprint':profile.fingerprint,'repo':base,'base_commit':commit}))}
        lease={'mission_id':mission,'device_id':'child','generation':1,'expires_at':int(time.time())+60}
        async def send(operation,extra=None,identifier=None):
            params={'worker':worker,**(extra or {})}
            value=frame(instance_id='product',device_id='child',identity_hash='a'*64,operation=operation,params=params,lease=lease,
                operation_id=identifier or operation_key(operation,worker,params))
            return await node.dispatch(value)
        workspace=await send('create_workspace');worker.update(workspace_id=workspace['workspace_id'],state='session_creating')
        worker.update(session_id=await send('create_session'),state='ready')
        assignment={'assignment_id':'assignment','mission_id':mission,'worker_id':worker['worker_id'],'workspace_id':worker['workspace_id'],
            'session_id':worker['session_id'],'submission_key':'fixture-child-original','max_seconds':60}
        params={'assignment':assignment,'prompt':'Answer the authorized request.','input_ids':[]}
        receipt=await send('submit',params,'submit-original');assignment['run_id']=receipt['run_id']
        for _ in range(100):
            snapshot=await send('inspect',{'assignment':assignment})
            if snapshot['settled']:break
            await asyncio.sleep(.02)
        self.assertEqual(snapshot['status'],'completed');self.assertIn('Separate process.',snapshot['output_text'])
        self.assertEqual(len(calls),1);self.assertNotEqual(process.pid,os.getpid())
        self.assertNotIn('never-in-child',str(config));self.assertEqual(snapshot['run_id'],receipt['run_id'])
