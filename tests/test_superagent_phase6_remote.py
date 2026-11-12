"""远程端口以真实 Harness、隔离 Git 和签名回环 HTTP 验证原运行，不接触外部设备。"""
from __future__ import annotations

import asyncio
from concurrent.futures import Future
import copy
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from harness.app.superagent_config import read_config,config_identity
from harness.app.superagent_coordinator_store import _hash,_json
from harness.app.superagent_executor import WorkerProfile
from harness.app.superagent_remote_client import RemoteClient
from harness.app.superagent_remote_gateway import WorkerGateway
from harness.app.superagent_remote_protocol import frame
from harness.app.superagent_service import SuperagentService
from harness.app.superagent_local import submit_local
from harness.core.errors import HarnessError
from harness.superagent_worker import native_node
from tests.superagent_product_fixture import product_config,ProductTransport
from harness.app.git_process import run_git


class RemoteFixture(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.path,self.raw=product_config(self.root)
        if getattr(self,'file_mode',False):self.raw['profiles']['work']['tools']=['read','write']
        self.remote_repo=self.root/'device-repo';run_git(self.root,'clone',str(self.root/'repo'),str(self.remote_repo))
        with socket.socket() as selected:selected.bind(('127.0.0.1',0));port=selected.getsockname()[1]
        self.key=b'fixture-device-key-32-bytes-or-more'
        self.multi=getattr(self,'multi_devices',False)
        if self.multi or getattr(self,'multiple_threads',False):self.raw['sources']['local']['threads']=['work','work-b']
        if self.multi:
            with socket.socket() as selected:selected.bind(('127.0.0.1',0));port_b=selected.getsockname()[1]
            self.remote_repo_b=self.root/'device-repo-b';run_git(self.root,'clone',str(self.root/'repo'),str(self.remote_repo_b))
        self.raw.update(execution={'devices':[{'device_id':'device-a','url':'http://127.0.0.1:'+str(port),'key_ref':'secret://device','capacity':1,'runner':'native'}],
            'lease_seconds':30},secrets={'device':'FIXTURE_DEVICE_KEY'})
        if self.multi:
            self.raw['execution']['devices'].append({'device_id':'device-b','url':'http://127.0.0.1:'+str(port_b),'key_ref':'secret://device-b','capacity':1,'runner':'native'})
            self.raw['secrets']['device-b']='FIXTURE_DEVICE_KEY_B'
        self.path.write_text(json.dumps(self.raw));self.spec=read_config(self.path)
        self.identity_hash=_hash(_json(config_identity(self.spec)))
        self.roles,self.work=ProductTransport(),ProductTransport()
        self.work_b=ProductTransport()
        if getattr(self,'file_mode',False):
            from tests.test_superagent_service import ProductEntryTests
            self.roles,self.work=ProductEntryTests().artifact_transports()
        self.native_config={'instance_id':self.spec['instance_id'],'device_id':'device-a','identity_hash':self.identity_hash,
            'state_dir':str(self.root/'device-state'),'base_url':self.spec['base_url'],'model':'fixture','language':'en','permission_policy':'full_access',
            'profiles':{key:{**value,'repo':str(self.remote_repo)} for key,value in self.spec['profiles'].items()},
            'default_profiles':self.spec['default_profiles'],'max_workers':32,
            'coordinator_profiles':{key:WorkerProfile(profile_id=key,**value).fingerprint for key,value in self.spec['profiles'].items()},
            'coordinator_repos':{key:value['repo'] for key,value in self.spec['profiles'].items()}}
        self.ready=Future();self.device_loop=asyncio.new_event_loop()
        def run():
            asyncio.set_event_loop(self.device_loop)
            async def start():
                self.node=await native_node(self.native_config,transport=self.work)
                self.gateway=WorkerGateway(self.node,instance_id=self.spec['instance_id'],device_id='device-a',identity_hash=self.identity_hash,
                    key=self.key,port=port)
            async def start_all():
                await start()
                if self.multi:
                    other={**self.native_config,'device_id':'device-b','state_dir':str(self.root/'device-state-b'),
                        'profiles':{key:{**value,'repo':str(self.remote_repo_b)} for key,value in self.spec['profiles'].items()}}
                    self.node_b=await native_node(other,transport=self.work_b)
                    self.gateway_b=WorkerGateway(self.node_b,instance_id=self.spec['instance_id'],device_id='device-b',
                        identity_hash=self.identity_hash,key=self.key+b'-second',port=port_b)
            try:self.device_loop.run_until_complete(start_all());self.ready.set_result(None);self.device_loop.run_forever()
            except BaseException as exc:
                if not self.ready.done():self.ready.set_exception(exc)
            finally:self.device_loop.run_until_complete(self.device_loop.shutdown_asyncgens());self.device_loop.close()
        self.device_thread=threading.Thread(target=run);self.device_thread.start()
        await asyncio.wrap_future(self.ready)
        self.addAsyncCleanup(self.close_device)
        environment=patch.dict(os.environ,{'HARNESS_API_KEY':'fixture','FIXTURE_DEVICE_KEY':self.key.decode(),'FIXTURE_DEVICE_KEY_B':(self.key+b'-second').decode()});environment.start();self.addCleanup(environment.stop)
        self.service=await SuperagentService.open(self.path,coordinator_transport=self.roles,worker_transport=ProductTransport())
        self.addAsyncCleanup(self.service.aclose)
        self.client=RemoteClient(url=self.spec['execution']['devices'][0]['url'],key=self.key,instance_id=self.spec['instance_id'],device_id='device-a',identity_hash=self.identity_hash)

    async def close_device(self):
        if self.device_loop.is_running():
            await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(self.gateway.close(),self.device_loop))
            if self.multi:await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(self.gateway_b.close(),self.device_loop))
            self.device_loop.call_soon_threadsafe(self.device_loop.stop)
        await asyncio.to_thread(self.device_thread.join,10)
        self.assertFalse(self.device_thread.is_alive())

    def rows(self,sql,args=()):
        with self.service.coordinator._store.transaction() as db:return [dict(row) for row in db.execute(sql,args)]

    async def complete(self):
        submit_local(self.path,'Fix the typo.',request_id='work')
        for _ in range(30):
            await self.service.tick()
            rows=self.rows('SELECT * FROM missions')
            if rows and rows[0]['state']=='completed':return rows[0]
            await asyncio.sleep(.02)
        self.fail(str(rows))

class RemoteExecutionTests(RemoteFixture):
    async def test_real_remote_harness_completes_through_original_executor_and_ledger(self):
        mission=await self.complete()
        assignment=self.rows('SELECT * FROM assignments')[0]
        self.assertEqual(assignment['state'],'succeeded')
        self.assertIsNotNone(assignment['started_at']);self.assertIsNotNone(assignment['ended_at'])
        self.assertEqual(len(self.work.requests),1)
        self.assertEqual(len(self.roles.requests),2)
        self.assertEqual(self.rows('SELECT device_id FROM remote_mission_leases'),[{'device_id':'device-a'}])
        page=self.service.coordinator.queries.page('leases',mission_id=mission['mission_id'])
        self.assertEqual(page['items'][0]['device_id'],'device-a')
        self.assertEqual(page['items'][0]['mission_id'],mission['mission_id'])
        devices=self.service.coordinator.queries.page('devices')
        self.assertNotIn(self.key.decode(),json.dumps([page,devices]))
        self.assertEqual(len(self.rows('SELECT * FROM workers')),1)
        self.assertEqual(self.rows('SELECT * FROM outbox_messages')[0]['state'],'sent')
        native=self.node.ledger.worker(assignment['worker_id'])
        self.assertEqual(native['repo'],str(self.remote_repo.resolve()))
        self.assertNotEqual(native['repo'],mission['repo'])
        self.assertEqual(self.node.adapter.harness.runtime.get(assignment['run_id']).status,'completed')

    async def test_lost_remote_submit_ack_only_queries_original_run_and_never_resubmits(self):
        invoke=self.gateway.node.dispatch
        dropped=False
        async def lose(value):
            nonlocal dropped
            result=await invoke(value)
            if value['operation']=='submit' and not dropped:
                dropped=True
                raise ConnectionError('fixture acknowledgement lost')
            return result
        self.gateway.node.dispatch=lose
        await self.complete()
        self.assertTrue(dropped)
        self.assertEqual(len(self.work.requests),1)
        self.assertEqual(len(self.rows('SELECT * FROM assignments')),1)
        with self.node.ledger.transaction() as db:
            rows=db.execute("SELECT * FROM operations WHERE state='completed'").fetchall()
            self.assertTrue(rows)
        self.assertEqual(self.rows('SELECT count(*) AS count FROM remote_mission_leases')[0]['count'],1)

    async def test_lost_workspace_ack_reuses_original_native_resource(self):
        invoke=self.node.dispatch;lost=False
        async def drop(value):
            nonlocal lost
            result=await invoke(value)
            if value['operation']=='create_workspace' and not lost:
                lost=True;raise ConnectionError('fixture original workspace acknowledgement lost')
            return result
        self.node.dispatch=drop
        await self.complete()
        self.assertTrue(lost);self.assertEqual(len(self.node.adapter.harness.worktrees.list()),1)
        self.assertEqual(len(self.work.requests),1)

    async def test_native_kernel_restart_reads_original_run_without_new_model_calls(self):
        await self.complete()
        worker=self.rows('SELECT * FROM workers')[0]
        assignment=self.service.coordinator._execution_service._assignment(self.rows('SELECT assignment_id FROM assignments')[0]['assignment_id'])
        async def restart():
            await self.node.close()
            self.node=await native_node(self.native_config,transport=self.work)
            self.gateway.node=self.node
        await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(restart(),self.device_loop))
        observed=self.service.coordinator._execution_service._executor.reconcile(worker,assignment)
        self.assertEqual(observed['run_id'],assignment['run_id']);self.assertEqual(observed['status'],'completed')
        self.assertEqual(len(self.work.requests),1)

    async def test_second_native_kernel_cannot_open_the_same_device_state(self):
        async def duplicate():return await native_node(self.native_config,transport=ProductTransport())
        with self.assertRaises(HarnessError) as caught:
            await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(duplicate(),self.device_loop))
        self.assertEqual(caught.exception.code,'superagent/runtime-busy')
        self.assertEqual(len(self.work.requests),0)

    async def test_signature_device_and_profile_identity_fail_before_native_resources(self):
        self.client.key=b'wrong-fixture-key-of-required-length'
        with self.assertRaises(HarnessError):await asyncio.to_thread(self.client.request,'identity',{})
        self.client.key=self.key;self.client.device_id='foreign'
        with self.assertRaises(HarnessError):await asyncio.to_thread(self.client.request,'identity',{})
        self.assertEqual(list((self.root/'device-state'/'runtime'/'sessions').glob('**/events*')),[])
        self.assertEqual(len(self.work.requests),0)

    async def test_runtime_policy_mismatch_fails_handshake_before_worker_creation(self):
        self.node.adapter.harness.config.permission_policy='read_only'
        executor=self.service.coordinator._execution_service._executor
        with self.assertRaises(HarnessError):
            await asyncio.to_thread(executor._verify,executor.clients['device-a'])
        self.assertEqual(self.node.adapter.harness.worktrees.list(),[])
        self.assertEqual(len(self.work.requests),0)

    async def test_native_device_cannot_satisfy_an_explicit_docker_runner_requirement(self):
        executor=self.service.coordinator._execution_service._executor
        executor.runners['device-a']='docker'
        with self.assertRaises(HarnessError):
            await asyncio.to_thread(executor._verify,executor.clients['device-a'])
        self.assertEqual(self.node.adapter.harness.worktrees.list(),[])
        self.assertEqual(len(self.work.requests),0)

    async def test_revoked_mission_stays_pinned_and_late_remote_receipt_is_fenced(self):
        await self.complete()
        executor=self.service.coordinator._execution_service._executor
        assignment=self.service.coordinator._execution_service._assignment(self.rows('SELECT assignment_id FROM assignments')[0]['assignment_id'])
        worker=self.rows('SELECT * FROM workers')[0]
        original=executor.clients['device-a'].request
        def revoke(operation,*args,**kwargs):
            result=original(operation,*args,**kwargs)
            if operation=='inspect':executor.leases.revoke(worker['mission_id'])
            return result
        executor.clients['device-a'].request=revoke
        with self.assertRaises(HarnessError):executor.inspect(worker,assignment)
        self.assertEqual(self.rows('SELECT state,device_id FROM remote_mission_leases'),[{'state':'revoked','device_id':'device-a'}])
        with self.assertRaises(HarnessError):executor.leases.for_mission(worker['mission_id'],allocate=True,renew=True)
        stopped=await executor.stop(worker,assignment)
        self.assertTrue(stopped['run_settled'])

    async def test_remote_native_pending_operation_is_not_replayed_after_reopen(self):
        await self.complete()
        worker=self.rows('SELECT * FROM workers')[0]
        lease=self.service.coordinator._execution_service._executor.leases.for_mission(worker['mission_id'])
        params={'worker':worker}
        value=frame(instance_id=self.spec['instance_id'],device_id='device-a',identity_hash=self.identity_hash,
            operation='capture_workspace',params=params,lease=lease,operation_id='pending-original')
        digest=_hash(_json(['capture_workspace',params,lease['mission_id'],lease['generation']]))
        with self.node.ledger.transaction() as db:db.execute("INSERT INTO operations VALUES(?,?,'pending',NULL,NULL)",('pending-original',digest))
        with self.assertRaises(HarnessError):await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(self.node.dispatch(value),self.device_loop))
        receipt=await asyncio.to_thread(self.client.request,'operation_receipt',{'operation_id':'pending-original'})
        self.assertEqual(receipt,{'state':'pending','result':None})
        self.assertEqual(len(self.work.requests),1)

    async def test_disconnect_keeps_service_alive_and_reconnect_reads_original_run(self):
        async def hold():
            self.work.gate=asyncio.Event();self.work.entered=asyncio.Event()
        await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(hold(),self.device_loop))
        submit_local(self.path,'Fix the typo.',request_id='work')
        await self.service.tick()
        executor=self.service.coordinator._execution_service._executor
        client=executor.clients['device-a'];original=client.request
        def disconnected(*args,**kwargs):raise ConnectionError('fixture offline')
        client.request=disconnected
        status=await self.service.tick()
        self.assertFalse(self.service.closed)
        self.assertEqual(len(status['remote_lease_errors']),1)
        self.assertEqual(len(self.work.requests),1)
        self.assertEqual(self.rows('SELECT state FROM assignments')[0]['state'],'unknown')
        client.request=original
        self.device_loop.call_soon_threadsafe(self.work.gate.set)
        await self.complete()
        self.assertEqual(len(self.work.requests),1)
        self.assertEqual(len(self.rows('SELECT * FROM assignments')),1)

    async def test_lease_expiry_stops_the_owned_run_and_does_not_start_a_replacement(self):
        async def gate():
            self.work.gate=asyncio.Event();self.work.entered=asyncio.Event()
        await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(gate(),self.device_loop))
        submit_local(self.path,'Fix the typo.',request_id='leased')
        await self.service.tick()
        assignment=self.rows('SELECT * FROM assignments')[0]
        self.assertEqual(len(self.work.requests),1)
        with self.node.ledger.transaction() as db:db.execute('UPDATE leases SET expires_at=0')
        for _ in range(100):
            native=self.node.adapter.harness.runtime.get(assignment['run_id'])
            if native.finished_at is not None:break
            await asyncio.sleep(.02)
        self.assertIsNotNone(native.finished_at)
        self.assertEqual(native.status,'interrupted')
        self.assertEqual(len(self.work.requests),1)
        self.assertEqual(len(self.rows('SELECT * FROM assignments')),1)

    async def test_coordinator_close_stops_original_remote_run_and_waits_for_receipt(self):
        async def hold():
            self.work.gate=asyncio.Event();self.work.entered=asyncio.Event()
        await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(hold(),self.device_loop))
        submit_local(self.path,'Fix the typo.',request_id='closing')
        await self.service.tick()
        assignment=self.rows('SELECT * FROM assignments')[0]
        store=self.service.coordinator._store
        await self.service.aclose()
        native=self.node.adapter.harness.runtime.get(assignment['run_id'])
        self.assertEqual(native.status,'interrupted');self.assertIsNotNone(native.finished_at)
        with store.transaction() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM audit_events WHERE event_type='remote.close_confirmed'").fetchone()[0],1)
        self.assertEqual(len(self.work.requests),1)

    async def test_source_revocation_reaches_native_gate_and_cannot_be_undone_by_old_heartbeat(self):
        async def hold():
            self.work.gate=asyncio.Event();self.work.entered=asyncio.Event()
        await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(hold(),self.device_loop))
        submit_local(self.path,'Fix the typo.',request_id='revoked')
        await self.service.tick()
        assignment=self.rows('SELECT * FROM assignments')[0]
        executor=self.service.coordinator._execution_service._executor
        old=executor.leases.for_mission(self.rows('SELECT mission_id FROM missions')[0]['mission_id'])
        source=self.service.coordinator.sources.get_instance('local')
        self.service.coordinator.sources.update_instance('local',expected_revision=source.config_revision,enabled=False)
        await self.service.tick()
        self.assertEqual(self.rows('SELECT state FROM remote_mission_leases'),[{'state':'revoked'}])
        with self.node.ledger.transaction() as db:self.assertEqual(db.execute('SELECT expires_at FROM leases').fetchone()[0],0)
        with self.assertRaises(HarnessError):await asyncio.to_thread(self.client.request,'heartbeat',{},lease=old)
        for _ in range(100):
            native=self.node.adapter.harness.runtime.get(assignment['run_id'])
            if native.finished_at is not None:break
            await asyncio.sleep(.02)
        self.assertIsNotNone(native.finished_at);self.assertEqual(len(self.work.requests),1)


class RemoteFileExecutionTests(RemoteFixture):
    file_mode=True

    async def test_remote_file_quality_gate(self):
        await self.complete()
        self.assertEqual(len(self.rows('SELECT * FROM assignments')),3)
        self.assertEqual(len(self.rows('SELECT * FROM artifacts')),2)
        self.assertEqual(self.rows('SELECT state FROM reviews'),[{'state':'passed'}])
        self.assertEqual(self.rows("SELECT count(*) AS count FROM deliveries WHERE state='applied'")[0]['count'],2)
        self.assertEqual(self.rows('SELECT count(*) AS count FROM remote_mission_leases')[0]['count'],1)
        self.assertFalse((self.root/'repo'/'result.txt').exists())
        workspaces=self.node.adapter.harness.worktrees.list()
        self.assertEqual(len(workspaces),3)
        self.assertTrue(all((Path(item['path'])/'result.txt').read_text()=='verified content\n' for item in workspaces))


class MultiDeviceExecutionTests(RemoteFixture):
    multi_devices=True

    async def test_two_logical_devices_run_distinct_missions_and_never_exchange_receipts(self):
        async def hold():
            for transport in (self.work,self.work_b):transport.gate=asyncio.Event();transport.entered=asyncio.Event()
        await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(hold(),self.device_loop))
        submit_local(self.path,'Fix the typo.',thread_id='work',request_id='first')
        submit_local(self.path,'Fix another typo.',thread_id='work-b',request_id='second')
        for _ in range(10):
            await self.service.tick()
            if len(self.rows('SELECT * FROM remote_mission_leases'))==2:break
        leases=self.rows('SELECT * FROM remote_mission_leases')
        self.assertEqual({row['device_id'] for row in leases},{'device-a','device-b'})
        self.assertEqual((len(self.work.requests),len(self.work_b.requests)),(1,1))
        for transport in (self.work,self.work_b):self.device_loop.call_soon_threadsafe(transport.gate.set)
        for _ in range(30):
            await self.service.tick()
            if self.rows("SELECT count(*) AS count FROM missions WHERE state='completed'")[0]['count']==2:break
            await asyncio.sleep(.02)
        self.assertEqual(self.rows("SELECT count(*) AS count FROM missions WHERE state='completed'")[0]['count'],2)
        assignments=self.rows('SELECT * FROM assignments')
        self.assertEqual(len({row['run_id'] for row in assignments}),2)
        for node in (self.node,self.node_b):
            with node.ledger.transaction() as db:self.assertEqual(db.execute('SELECT count(*) FROM workers').fetchone()[0],1)
        self.assertEqual(self.rows("SELECT count(*) AS count FROM outbox_messages WHERE state='sent'")[0]['count'],2)


class SingleDeviceQueueTests(RemoteFixture):
    multiple_threads=True

    async def test_one_capacity_device_queues_another_mission_without_unknown_worker_or_duplicate_run(self):
        async def hold():self.work.gate=asyncio.Event();self.work.entered=asyncio.Event()
        await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(hold(),self.device_loop))
        submit_local(self.path,'Fix the typo.',thread_id='work',request_id='first')
        submit_local(self.path,'Fix another typo.',thread_id='work-b',request_id='second')
        try:
            for _ in range(8):
                await self.service.tick()
                if len(self.rows('SELECT * FROM missions'))==2 and len(self.work.requests)==1:break
            self.assertEqual(len(self.rows('SELECT * FROM missions')),2)
            self.assertEqual(len(self.rows('SELECT * FROM remote_devices')),1)
            self.assertEqual(len(self.rows('SELECT * FROM remote_mission_leases')),1)
            self.assertEqual(len(self.rows('SELECT * FROM workers')),1)
            self.assertEqual(len(self.work.requests),1)
            self.assertEqual(self.rows("SELECT count(*) AS n FROM workers WHERE state='unknown'")[0]['n'],0)
        finally:self.device_loop.call_soon_threadsafe(self.work.gate.set)
        for _ in range(30):
            await self.service.tick()
            if self.rows("SELECT count(*) AS n FROM missions WHERE state='completed'")[0]['n']==2:break
            await asyncio.sleep(.02)
        self.assertEqual(self.rows("SELECT count(*) AS n FROM missions WHERE state='completed'")[0]['n'],2)
        self.assertEqual({row['device_id'] for row in self.rows('SELECT * FROM remote_mission_leases')},{'device-a'})
        self.assertEqual(len(self.work.requests),2)
        self.assertEqual(len({row['run_id'] for row in self.rows('SELECT * FROM assignments')}),2)
