"""远程原生 Worker 端口：保存资源绑定和操作回执，不保存协调任务状态。"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
import inspect
import re
from pathlib import Path
import sqlite3
import time

from ..core.cleanup import join_cleanup
from .superagent_common import _error
from .superagent_coordinator_store import _hash,_json
from .superagent_remote_protocol import WRITES,OPERATIONS,encode,decode


class NodeLedger:
    def __init__(self,path,identity):
        self.path=Path(path)
        if self.path.is_symlink(): raise _error('remote.identity')
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.transaction() as db:
            tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            if not tables:
                db.execute('CREATE TABLE identity(value TEXT NOT NULL)')
                db.execute('INSERT INTO identity VALUES(?)',(_json(identity),))
                db.execute('CREATE TABLE operations(operation_id TEXT PRIMARY KEY,payload_hash TEXT NOT NULL,state TEXT NOT NULL,result_json TEXT,error TEXT)')
                db.execute('CREATE TABLE workers(worker_id TEXT PRIMARY KEY,identity_hash TEXT NOT NULL,body_json TEXT NOT NULL)')
                db.execute('CREATE TABLE leases(mission_id TEXT PRIMARY KEY,generation INTEGER NOT NULL,expires_at INTEGER NOT NULL)')
                db.execute('PRAGMA user_version=1')
            elif (tables!={'identity','operations','workers','leases'} or db.execute('PRAGMA user_version').fetchone()[0]!=1
                    or [row[0] for row in db.execute('SELECT value FROM identity')]!=[_json(identity)]):
                raise _error('remote.identity')

    @contextmanager
    def transaction(self):
        db=sqlite3.connect(self.path,timeout=10,isolation_level=None); db.row_factory=sqlite3.Row
        try:
            db.execute('BEGIN IMMEDIATE'); yield db; db.commit()
        except BaseException:
            db.rollback(); raise
        finally: db.close()

    def worker(self,worker_id):
        with self.transaction() as db: row=db.execute('SELECT * FROM workers WHERE worker_id=?',(worker_id,)).fetchone()
        if row is None:return None
        body=decode(row['body_json'].encode())
        stable={key:body.get(key) for key in ('worker_id','mission_id','job_id','stage','profile_id','repo','base_commit','runtime_instance_id','identity_hash','ticket_id')}
        if _hash(_json(stable))!=row['identity_hash']:raise _error('corrupt')
        return body

    def lease_valid(self,mission_id):
        with self.transaction() as db: row=db.execute('SELECT expires_at FROM leases WHERE mission_id=?',(mission_id,)).fetchone()
        return row is not None and row[0]>time.time()


class NativeWorkerNode:
    """复用唯一 HarnessExecutorAdapter，远端不能通过 RPC 注册任意方法或 Profile。"""
    def __init__(self,adapter,ledger,*,coordinator_profiles,coordinator_repos,sandbox='docker',broker_scope=None,isolated_processes=False,repository_sources=None,provider_root=None,runtime_stack=None):
        self.adapter,self.ledger=adapter,ledger
        self.profiles,self.repos=coordinator_profiles,coordinator_repos
        self.repository_sources=repository_sources or {}
        self.provider_root=provider_root
        self.runtime_stack=runtime_stack;self._close_task=None
        self.sandbox,self.broker_scope=sandbox,broker_scope
        if (set(adapter.profiles)!=set(coordinator_profiles) or set(adapter.profiles)!=set(coordinator_repos)
                or any(profile.fingerprint_for_repo(coordinator_repos[key])!=coordinator_profiles[key] for key,profile in adapter.profiles.items())):
            raise _error('remote.identity')
        self.lock=asyncio.Lock(); self.closing=False;self.active_assignments={};self.lease_errors=[]
        adapter.set_worker_lookup(ledger.worker)
        self.isolation=None
        if isolated_processes:
            from .superagent_process_isolation import ProcessIsolation
            self.isolation=ProcessIsolation(adapter.harness,ledger)
        def admission(tool,args,ctx):
            # 原生绑定仍校验 Worker；按确切 Session 查找租约，不把模型参数当作身份。
            with ledger.transaction() as db:
                rows=db.execute("SELECT body_json FROM workers WHERE json_extract(body_json,'$.session_id')=?",(ctx.session_id,)).fetchall()
            worker=decode(rows[0][0].encode()) if len(rows)==1 else None
            from .superagent_process_isolation import scoped_arguments
            if tool.schema.name in {'read','write','edit','glob','grep','bash'}:
                try:scoped_arguments(tool.schema.name,args,ctx.cwd)
                except Exception as exc:
                    if getattr(exc,'code',None)!='superagent/remote.tool-scope':raise
                    from ..i18n import tr
                    return tr('superagent.remote.tool-scope')
            if worker is None or not ledger.lease_valid(worker['mission_id']):
                from ..i18n import tr
                return tr('superagent.remote.lease')
        adapter.harness.tools.add_execution_check(admission)
        self.watcher=asyncio.create_task(self._expire())

    def _worker(self,worker,lease,*,update):
        if not isinstance(worker,dict) or worker.get('mission_id')!=lease['mission_id'] or worker.get('runtime_instance_id')!=self.adapter.instance_id:
            raise _error('remote.identity')
        if not isinstance(worker.get('base_commit'),str) or not re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})',worker['base_commit']):raise _error('remote.identity')
        profile=self.adapter.profiles.get(worker.get('profile_id'))
        if profile is None or worker.get('repo')!=self.repos[profile.profile_id]: raise _error('remote.identity')
        expected_id='worker_'+_hash(_json([self.adapter.instance_id,worker['mission_id'],worker.get('job_id'),worker.get('stage')]))[:32]
        expected_hash=_hash(_json({'instance_id':self.adapter.instance_id,'ticket_id':worker.get('ticket_id'),
            'mission_id':worker['mission_id'],'job_id':worker.get('job_id'),'stage':worker.get('stage'),
            'profile_id':profile.profile_id,'profile_fingerprint':self.profiles[profile.profile_id],
            'repo':self.repos[profile.profile_id],'base_commit':worker.get('base_commit')}))
        if worker.get('worker_id')!=expected_id or worker.get('identity_hash')!=expected_hash:raise _error('remote.identity')
        actual={**worker,'repo':profile.repo}
        stable={key:actual.get(key) for key in ('worker_id','mission_id','job_id','stage','profile_id','repo','base_commit','runtime_instance_id','identity_hash','ticket_id')}
        digest=_hash(_json(stable))
        with self.ledger.transaction() as db:
            row=db.execute('SELECT * FROM workers WHERE worker_id=?',(worker['worker_id'],)).fetchone()
            if row and row['identity_hash']!=digest: raise _error('remote.identity')
            if update:
                if row is None:
                    if db.execute('SELECT count(*) FROM workers').fetchone()[0]>=self.adapter.max_workers: raise _error('capacity')
                    db.execute('INSERT INTO workers VALUES(?,?,?)',(worker['worker_id'],digest,_json(actual)))
                else: db.execute('UPDATE workers SET body_json=? WHERE worker_id=?',(_json(actual),worker['worker_id']))
            elif row is None:
                # 未知创建只读回查可检查原生资源，但不为其补造新资源绑定。
                return actual,profile
        return actual,profile

    def _lease(self,lease,*,mutating):
        with self.ledger.transaction() as db:
            row=db.execute('SELECT * FROM leases WHERE mission_id=?',(lease['mission_id'],)).fetchone()
            if row and row['generation']!=lease['generation']: raise _error('remote.lease')
            if row and row['expires_at']==0:
                if mutating:raise _error('remote.lease')
                return
            if mutating and lease['expires_at']<=time.time(): raise _error('remote.lease')
            if row is None:
                db.execute('INSERT INTO leases VALUES(?,?,?)',(lease['mission_id'],lease['generation'],lease['expires_at']))
            elif lease['expires_at']>row['expires_at']:
                db.execute('UPDATE leases SET expires_at=? WHERE mission_id=?',(lease['expires_at'],lease['mission_id']))

    async def dispatch(self,value):
        if self.closing: raise _error('closed')
        operation,params,key,lease=value['operation'],value['params'],value['operation_id'],value['lease']
        if operation not in OPERATIONS: raise _error('remote.frame')
        async with self.lock:
            if operation=='identity':
                if params:raise _error('remote.frame')
                runtime=self.adapter.runtime_manifest()
                if self.provider_root is not None:runtime['provider_root']=self.provider_root
                return {'protocol':1,'profiles':self.profiles,'runner':'docker' if self.sandbox=='docker' else 'native','sandbox':self.sandbox,'runtime':runtime}
            if operation=='operation_receipt':
                if set(params)!={'operation_id'}: raise _error('remote.frame')
                with self.ledger.transaction() as db: row=db.execute('SELECT * FROM operations WHERE operation_id=?',(params['operation_id'],)).fetchone()
                return {'state':row['state'],'result':decode(row['result_json'].encode())['value'] if row['result_json'] else None} if row else {'state':'absent'}
            if operation=='revoke':
                if params:raise _error('remote.frame')
                digest=_hash(_json([operation,lease['mission_id'],lease['generation']]))
                receipt={'mission_id':lease['mission_id'],'generation':lease['generation'],'revoked':True}
                with self.ledger.transaction() as db:
                    prior=db.execute('SELECT * FROM operations WHERE operation_id=?',(key,)).fetchone()
                    if prior:
                        if prior['payload_hash']!=digest or prior['state']!='completed':raise _error('remote.identity')
                        return decode(prior['result_json'].encode())['value']
                    row=db.execute('SELECT * FROM leases WHERE mission_id=?',(lease['mission_id'],)).fetchone()
                    if row and row['generation']!=lease['generation']:raise _error('remote.lease')
                    db.execute('INSERT INTO leases VALUES(?,?,0) ON CONFLICT(mission_id) DO UPDATE SET expires_at=0',
                        (lease['mission_id'],lease['generation']))
                    db.execute("INSERT INTO operations VALUES(?,?,'completed',?,NULL)",(key,digest,_json({'value':receipt})))
                return receipt
            self._lease(lease,mutating=operation not in {'stop','reconcile','inspect','find_worker','snapshot_workspace','artifact_manifest','reconcile_input','reconcile_instruction'})
            if operation=='heartbeat':
                if params:raise _error('remote.frame')
                return {'generation':lease['generation'],'expires_at':lease['expires_at']}
            fields={'find_worker':set(),'create_workspace':set(),'create_session':set(),'submit':{'assignment','prompt','input_ids'},
                'reconcile':{'assignment'},'inspect':{'assignment'},'snapshot_workspace':set(),'capture_workspace':set(),
                'artifact_manifest':{'receipt'},'plan_artifact_delivery':{'artifacts'},'apply_artifact_delivery':{'artifacts','plan'},
                'deliver_input':{'assignment','text','operation_key'},'reconcile_input':{'assignment','operation_key'},
                'instruction_turn':{'assignment','operation_key'},'deliver_instruction':{'assignment','text','operation_key','turn_id'},
                'reconcile_instruction':{'assignment','operation_key','turn_id','expected_text'},'stop':{'assignment'}}
            if set(params)!={'worker'}|fields[operation]:raise _error('remote.frame')
            digest=_hash(_json([operation,params,lease['mission_id'],lease['generation']]))
            if operation in WRITES:
                with self.ledger.transaction() as db:
                    prior=db.execute('SELECT * FROM operations WHERE operation_id=?',(key,)).fetchone()
                    if prior:
                        if prior['payload_hash']!=digest:raise _error('remote.identity')
                        if prior['state']=='completed':return decode(prior['result_json'].encode())['value']
                        # pending 包含崩溃或丢失回执，绝不重新调用原生写入。
                        raise _error('remote.unknown')
                    db.execute("INSERT INTO operations VALUES(?,?,'pending',NULL,NULL)",(key,digest))
            worker,profile=self._worker(params['worker'],lease,update=operation in WRITES)
            call={name:argument for name,argument in params.items() if name!='worker'}
            if 'assignment' in call:
                assignment=call['assignment']
                if (not isinstance(assignment,dict) or any(assignment.get(field)!=worker.get(field) for field in ('worker_id','workspace_id','session_id'))
                        or assignment.get('mission_id')!=worker['mission_id']):raise _error('remote.identity')
            if operation in {'find_worker','create_workspace','create_session'}:call['profile']=profile
            if operation in {'deliver_input','deliver_instruction'}:
                def check():
                    if not self.ledger.lease_valid(worker['mission_id']):raise _error('remote.lease')
                call['before_accept']=check
            if operation=='stop':self.active_assignments.pop(worker['worker_id'],None)
            token=None
            if operation=='submit' and self.broker_scope:
                token=self.broker_scope.set(value.get('broker_capability'))
            try:
                if operation=='create_workspace' and profile.repo in self.repository_sources:
                    from .git_process import run_git
                    present=run_git(profile.repo,'cat-file','-e',worker['base_commit']+'^{commit}')
                    if present.returncode!=0:
                        fetched=run_git(profile.repo,'fetch','--no-tags',self.repository_sources[profile.repo],worker['base_commit'])
                        if fetched.returncode!=0:raise _error('remote.operation')
                result=getattr(self.adapter,operation)(worker,**call)
                if inspect.isawaitable(result):result=await join_cleanup(asyncio.create_task(result))
                encode({'value':result})
                if self.isolation and operation in {'inspect','reconcile'} and isinstance(result,dict) and result.get('found'):
                    result['unsettled_background']=result.get('unsettled_background',0)+len(self.isolation.processes(worker['worker_id']))
                    result['unconfirmed_tools']=result.get('unconfirmed_tools',0)+int(worker['worker_id'] in self.isolation.fs_unknown)
                if self.isolation and operation=='stop':
                    self.isolation.stop(worker['worker_id'])
                if operation=='submit':
                    self.active_assignments[worker['worker_id']]=(worker,{**call['assignment'],'run_id':result['run_id']})
            finally:
                if token is not None:self.broker_scope.reset(token)
            if operation in WRITES:
                with self.ledger.transaction() as db:
                    db.execute("UPDATE operations SET state='completed',result_json=? WHERE operation_id=? AND state='pending'",(_json({'value':result}),key))
            return result

    async def _expire(self):
        while not self.closing:
            for key,(worker,assignment) in tuple(self.active_assignments.items()):
                if self.ledger.lease_valid(worker['mission_id']):continue
                self.active_assignments.pop(key,None)
                try:
                    await join_cleanup(asyncio.create_task(self.adapter.stop(worker,assignment)))
                    if self.isolation:self.isolation.stop(worker['worker_id'])
                except Exception as exc:
                    self.lease_errors.append(exc)
                else:self.active_assignments.pop(key,None)
            await asyncio.sleep(.25)

    async def close(self):
        if self._close_task is None:
            self.closing=True
            self._close_task=asyncio.create_task(self._close())
        await join_cleanup(self._close_task)

    async def _close(self):
        self.closing=True
        self.watcher.cancel();await asyncio.gather(self.watcher,return_exceptions=True)
        from ..core.cleanup import raise_cleanup_errors
        errors=list(self.lease_errors)
        async with self.lock:
            try:await self.adapter.harness.close()
            except BaseException as exc:errors.append(exc)
            if self.isolation:
                for worker,assignment in self.active_assignments.values():
                    try:self.isolation.stop(worker['worker_id'])
                    except BaseException as exc:errors.append(exc)
                try:await asyncio.to_thread(self.isolation.close)
                except BaseException as exc:errors.append(exc)
        if self.runtime_stack is not None:
            try:self.runtime_stack.close()
            except BaseException as exc:errors.append(exc)
        raise_cleanup_errors(errors)
