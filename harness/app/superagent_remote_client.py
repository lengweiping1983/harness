"""签名远程 Executor；同步读取沿原同步 Port，写入线程由所属任务收尾。"""
from __future__ import annotations

import asyncio
import hmac
import time
import urllib.error
import uuid

from ..core.cleanup import join_cleanup
from ..net.http import ipv4_preferred_get
from .superagent_common import _error
from .superagent_coordinator_store import _hash,_json
from .superagent_executor import HarnessExecutorAdapter
from .superagent_remote_protocol import MAX_FRAME,encode,decode,signature,frame,operation_key
from .superagent_http import api_root
from .superagent_remote_leases import RemoteLeases


class RemoteClient:
    def __init__(self,*,url,key,instance_id,device_id,identity_hash,timeout=10):
        if not isinstance(key,bytes) or len(key)<32:raise _error('remote.identity')
        url=api_root(url)
        self.url,self.key,self.instance_id,self.device_id,self.identity_hash,self.timeout=url,key,instance_id,device_id,identity_hash,timeout

    def request(self,operation,params,*,lease=None,operation_id=None):
        value=frame(instance_id=self.instance_id,device_id=self.device_id,identity_hash=self.identity_hash,
            operation=operation,params=params,lease=lease,operation_id=operation_id)
        raw=encode(value)
        def no_redirect(_): raise _error('remote.identity')
        try:
            with ipv4_preferred_get(self.url+'/executor/v1',method='POST',data=raw,
                    headers={'Content-Type':'application/json','X-Harness-Signature':signature(self.key,raw)},timeout=self.timeout,url_validator=no_redirect) as response:
                body=response.read(MAX_FRAME+1)
                if response.status!=200 or not hmac.compare_digest(response.headers.get('X-Harness-Signature',''),signature(self.key,body)):
                    raise _error('remote.identity')
        except (OSError,urllib.error.URLError) as exc:
            raise _error('remote.unavailable') from exc
        result=decode(body)
        if (set(result)!={'version','device_id','nonce','operation_id','result','error'} or type(result['version']) is not int or result['version']!=1
                or result['device_id']!=self.device_id or result['nonce']!=value['nonce'] or result['operation_id']!=value['operation_id']):
            raise _error('remote.identity')
        if result['error'] is not None: raise _error('remote.operation')
        return result['result']


class RemoteExecutionAdapter(HarnessExecutorAdapter):
    """只扩展传输和资源归属；调度、质量门、证据与 Outbox 仍归原协调器。"""
    def __init__(self,*args,store,spec,secret_resolver,**kwargs):
        config=spec['execution']
        keys={item['device_id']:secret_resolver(item['key_ref']) for item in config['devices']}
        if (any(not isinstance(key,bytes) or len(key)<32 for key in keys.values())
            or len(set(keys.values()))!=len(keys)):raise _error('remote.identity')
        super().__init__(*args,**kwargs)
        identity_hash=_hash(_json({'config':spec}))
        self.leases=RemoteLeases(store,config['devices'],identity_hash,config['lease_seconds'])
        self.clients={item['device_id']:RemoteClient(url=item['url'],key=keys[item['device_id']],
            instance_id=self.instance_id,device_id=item['device_id'],identity_hash=identity_hash) for item in config['devices']}
        self.runners={item['device_id']:item.get('runner','docker') for item in config['devices']}
        self.verified=set()

    def reserve_mission(self,mission_id):
        """设备满载时在创建 Worker 前背压，不能把未发出的创建误记为未知。"""
        self._open()
        self.leases.for_mission(mission_id,allocate=True)

    def _request(self,operation,worker,params=None,*,write=False):
        self._open()
        lease=self.leases.for_mission(worker['mission_id'],allocate=operation=='create_workspace',renew=write and operation!='stop',original_stop=operation=='stop')
        client=self.clients[lease['device_id']]
        self._verify(client)
        if write and operation!='stop' and not self._authorized(worker['mission_id']):raise _error('mandate.unavailable')
        values={'worker':dict(worker),**(params or {})}
        key=operation_key(operation,worker,values) if write else uuid.uuid4().hex
        result=client.request(operation,values,lease=lease,operation_id=key)
        # 旧设备的迟到回执不能落入已撤销的 Mission。
        self.leases.check(lease,original_stop=operation=='stop')
        return result

    def _verify(self,client):
        if client.device_id not in self.verified:
            identity=client.request('identity',{})
            expected={key:profile.fingerprint for key,profile in self.profiles.items()}
            if (not isinstance(identity,dict) or identity.get('profiles')!=expected or type(identity.get('protocol')) is not int or identity.get('protocol')!=1
                    or identity.get('runner')!=self.runners[client.device_id]
                    or identity.get('sandbox')!=('docker' if self.runners[client.device_id]=='docker' else 'none')
                    or identity.get('runtime')!=self.runtime_manifest()):
                raise _error('remote.identity')
            self.verified.add(client.device_id)

    async def _write(self,operation,worker,params=None):
        return await join_cleanup(asyncio.create_task(asyncio.to_thread(self._request,operation,worker,params,write=True)))

    def find_worker(self,worker,profile): return self._request('find_worker',worker)
    async def create_workspace(self,worker,profile): return await self._write('create_workspace',worker)
    async def create_session(self,worker,profile): return await self._write('create_session',worker)
    async def submit(self,worker,assignment,prompt,input_ids):
        return await self._write('submit',worker,{'assignment':assignment,'prompt':prompt,'input_ids':input_ids})
    def reconcile(self,worker,assignment): return self._request('reconcile',worker,{'assignment':assignment})
    def inspect(self,worker,assignment): return self._request('inspect',worker,{'assignment':assignment})
    def snapshot_workspace(self,worker): return self._request('snapshot_workspace',worker)
    async def capture_workspace(self,worker): return await self._write('capture_workspace',worker)
    def artifact_manifest(self,worker,receipt): return self._request('artifact_manifest',worker,{'receipt':receipt})
    async def plan_artifact_delivery(self,worker,artifacts): return await self._write('plan_artifact_delivery',worker,{'artifacts':artifacts})
    async def apply_artifact_delivery(self,worker,artifacts,plan): return await self._write('apply_artifact_delivery',worker,{'artifacts':artifacts,'plan':plan})
    async def deliver_input(self,worker,assignment,text,operation_key,before_accept):
        before_accept()
        return await self._write('deliver_input',worker,{'assignment':assignment,'text':text,'operation_key':operation_key})
    def reconcile_input(self,worker,assignment,operation_key):
        return self._request('reconcile_input',worker,{'assignment':assignment,'operation_key':operation_key})
    def instruction_turn(self,worker,assignment,operation_key):
        return self._request('instruction_turn',worker,{'assignment':assignment,'operation_key':operation_key})
    async def deliver_instruction(self,worker,assignment,text,operation_key,turn_id,before_accept):
        before_accept()
        return await self._write('deliver_instruction',worker,{'assignment':assignment,'text':text,'operation_key':operation_key,'turn_id':turn_id})
    def reconcile_instruction(self,worker,assignment,operation_key,turn_id,expected_text):
        return self._request('reconcile_instruction',worker,{'assignment':assignment,'operation_key':operation_key,'turn_id':turn_id,'expected_text':expected_text})
    async def stop(self,worker,assignment): return await self._write('stop',worker,{'assignment':assignment})

    def _authorized(self,mission_id):
        from .superagent_authorization import CoordinatorAuthorizationEngine
        with self.leases.store.transaction() as db:
            row=db.execute('SELECT d.mandate_id FROM missions m JOIN mandates d ON d.ticket_id=m.ticket_id JOIN tickets t ON t.ticket_id=m.ticket_id '
                "WHERE m.mission_id=? AND t.state='open'",(mission_id,)).fetchone()
        return bool(row and CoordinatorAuthorizationEngine(self.leases.store).revalidate_mandate(row['mandate_id'],actor={'kind':'host'})['status'] in {'valid','affected'})

    async def refresh_leases(self):
        with self.leases.store.transaction() as db:
            missions=[row[0] for row in db.execute("SELECT l.mission_id FROM remote_mission_leases l JOIN missions m ON m.mission_id=l.mission_id "
                "WHERE l.state='active' AND m.state NOT IN ('completed','cancelled','rejected','suppressed')")]
        errors=[]
        for mission in missions:
            lease=self.leases.for_mission(mission)
            if not self._authorized(mission):
                self.leases.revoke(mission)
                try:
                    await join_cleanup(asyncio.create_task(asyncio.to_thread(self.clients[lease['device_id']].request,'revoke',{},
                        lease=lease,operation_id='revoke_'+_hash(_json([mission,lease['generation']])))))
                except Exception as exc:
                    errors.append({'mission_id':mission,'device_id':lease['device_id'],'code':getattr(exc,'code',type(exc).__name__)})
                continue
            lease=self.leases.for_mission(mission,renew=True)
            try:
                await join_cleanup(asyncio.create_task(asyncio.to_thread(self.clients[lease['device_id']].request,'heartbeat',{},lease=lease)))
            except Exception as exc:
                errors.append({'mission_id':mission,'device_id':lease['device_id'],'code':getattr(exc,'code',type(exc).__name__)})
        return errors

    async def aclose(self):
        """关闭只停止确切原运行；失联保留未知和原租约，不释放给另一设备。"""
        if getattr(self,'_remote_close_task',None) is None:
            async def finish():
                from ..core.cleanup import raise_cleanup_errors
                errors=[]
                with self.leases.store.transaction() as db:
                    rows=db.execute("SELECT a.*,j.mission_id FROM assignments a JOIN jobs j ON j.job_id=a.job_id "
                        "JOIN remote_mission_leases l ON l.mission_id=j.mission_id WHERE a.state IN ('submitting','accepted','running','capturing','stopping','unknown')").fetchall()
                    workers={row['worker_id']:dict(row) for row in db.execute('SELECT * FROM workers')}
                for row in rows:
                    assignment=dict(row);worker=workers.get(assignment['worker_id'])
                    if worker is None or worker.get('session_id') is None:continue
                    try:
                        lease=self.leases.for_mission(assignment['mission_id'],original_stop=True)
                        client=self.clients[lease['device_id']]
                        if not assignment.get('run_id'):
                            found=await join_cleanup(asyncio.create_task(asyncio.to_thread(client.request,'reconcile',
                                {'worker':worker,'assignment':assignment},lease=lease)))
                            if found.get('found') is False and found.get('authoritative') is True:continue
                            if not found.get('run_id'):raise _error('remote.unknown')
                            assignment['run_id']=found['run_id']
                        params={'worker':worker,'assignment':assignment}
                        result=await join_cleanup(asyncio.create_task(asyncio.to_thread(client.request,'stop',params,
                            lease=lease,operation_id=operation_key('stop',worker,params))))
                        if result.get('run_settled') is not True:
                            deadline=time.monotonic()+10
                            while time.monotonic()<deadline:
                                observed=await join_cleanup(asyncio.create_task(asyncio.to_thread(client.request,'inspect',params,lease=lease)))
                                if (observed.get('run_id')==assignment['run_id'] and observed.get('settled') is True
                                        and not observed.get('unsettled_background',0)):
                                    result={**result,'run_settled':True,'settlement':observed['run_reference']};break
                                await asyncio.sleep(.05)
                            else:raise _error('remote.unknown')
                        with self.leases.store.transaction(write=True) as db:
                            self.leases.store.append_audit(db,aggregate_type='assignment',aggregate_id=assignment['assignment_id'],
                                event_type='remote.close_confirmed',payload={'run_id':assignment['run_id'],'device_id':lease['device_id'],'receipt':result},actor={'kind':'host'})
                    except BaseException as exc:errors.append(exc)
                await super(RemoteExecutionAdapter,self).aclose()
                raise_cleanup_errors(errors)
            self._remote_close_task=asyncio.create_task(finish())
        await join_cleanup(self._remote_close_task)
