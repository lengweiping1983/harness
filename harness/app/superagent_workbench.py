"""本机工作台管理明确连接的统一实例，不复制执行或协调账本。"""
from __future__ import annotations

import asyncio
from pathlib import Path

from ..core.cleanup import join_cleanup, raise_cleanup_errors
from .superagent_common import _error, _text, _json as strict_loads
from ..core.errors import HarnessError
from .superagent_config import read_config, config_identity
from .superagent_coordinator_store import _hash, _json
from .superagent_authorization import CoordinatorAuthorizationEngine
from .superagent_local import submit_local, control_local, steer_local, read_status, read_control_receipt, read_guidance_receipt, answer_local
from .superagent_operator import CoordinatorOperator, OperatorRequests
from .superagent_queries import CoordinatorQueries
from .superagent_state import open_state
from .superagent_service import SuperagentService


class SuperagentWorkbench:
    """所属运行和请求由同一主宿主等待收尾，取消 HTTP 等待不撤销已接受操作。"""

    def __init__(self, *, service_factory=SuperagentService.open):
        self._factory = service_factory
        self._connections,self._requests,self._runs = {},{},{}
        self._runtime_locks = {}
        self._closing = False
        self._close_task = None

    def _open(self):
        if self._closing:
            raise _error('closed')

    def connect(self, config_path):
        self._open()
        _text(config_path,'config_path',limit=4096)
        path = Path(config_path).expanduser().resolve(strict=True)
        spec = read_config(path)
        key = 'sg_' + _hash(_json(config_identity(spec)))[:32]
        if key not in self._connections and len(self._connections)>=16:
            raise _error('capacity')
        self._connections[key] = {'path':path,'spec':spec,'identity':config_identity(spec)}
        return self.connection(key)

    def _entry(self, key):
        self._open()
        if key not in self._connections:
            raise _error('workbench.connection')
        entry = self._connections[key]
        current = read_config(entry['path'])
        if config_identity(current) != entry['identity']:
            raise _error('conflict')
        return entry

    def connection(self, key):
        entry = self._entry(key)
        run = self._runs.get(key)
        return {'connection_id':key,'instance_id':entry['spec']['instance_id'], 'config_path':str(entry['path']),
            'runtime':('failed' if run and run.get('error') else 'running' if run and not run['task'].done() else 'stopped'),
            'error_code':getattr(run.get('error'),'code','runtime/failure') if run and run.get('error') else None}

    def connections(self):
        rows = []
        for key,entry in self._connections.items():
            try:
                rows.append(self.connection(key))
            except (OSError,ValueError,RuntimeError,HarnessError) as exc:
                rows.append({'connection_id':key,'instance_id':entry['spec']['instance_id'],'config_path':str(entry['path']),
                    'runtime':'unavailable','error_code':getattr(exc,'code',type(exc).__name__)})
        return rows

    def read(self, key, resource, *, mission_id=None, limit=32, cursor=None, identifier=None):
        entry = self._entry(key)
        if resource == 'receipt' and identifier in self._requests:
            raw,task = self._requests[identifier]
            frame = strict_loads(raw)
            if frame[0] != key:
                raise _error('conflict')
            if frame[1] in {'source','policy','verify','requeue','reply'} and (Path(entry['spec']['state_dir'])/'superagent.sqlite3').exists():
                store = open_state(entry['spec'],read_only=True)
                with store.transaction() as db:
                    durable = db.execute('SELECT request_id FROM coordinator_operator_requests WHERE request_id=?',(identifier,)).fetchone()
                if durable:
                    return {'instance_id':store.instance_id,**OperatorRequests.receipt(store,identifier)}
            if not task.done():
                data = {'request_id':identifier,'state':'pending'}
            else:
                try:
                    data = {'request_id':identifier,'state':'completed','receipt':task.result()}
                except Exception as exc:
                    data = {'request_id':identifier,'state':'failed','error_code':getattr(exc,'code',type(exc).__name__)}
            return {'instance_id':entry['spec']['instance_id'],**data}
        if resource == 'local_options':
            sources = entry['spec']['sources']
            enabled = {key:value['enabled'] for key,value in sources.items()}
            if (Path(entry['spec']['state_dir'])/'superagent.sqlite3').exists():
                with open_state(entry['spec'],read_only=True).transaction() as db:
                    enabled.update({row[0]:bool(row[1]) for row in db.execute('SELECT source_instance_id,enabled FROM source_instances')})
            return {'instance_id':entry['spec']['instance_id'],'sources':[{'source_id':name,'actors':value['actors'],'threads':value['threads']}
                for name,value in sources.items() if value['kind']=='local' and enabled[name]]}
        store = open_state(entry['spec'],read_only=True)
        if resource == 'mission':
            return CoordinatorQueries(store).mission(mission_id)
        if resource == 'policy':
            return {'instance_id':store.instance_id,'policy':CoordinatorAuthorizationEngine(store).current_policy()}
        if resource == 'status':
            return read_status(entry['path'])
        if resource == 'receipt':
            _text(identifier,'request_id',limit=96)
            with store.transaction() as db:
                operator = db.execute('SELECT request_id FROM coordinator_operator_requests WHERE request_id=?',(identifier,)).fetchone()
                control = db.execute('SELECT request_id FROM control_commands WHERE request_id=?',(identifier,)).fetchone()
                instruction = db.execute('SELECT request_id FROM coordinator_requests WHERE request_id=?',(identifier,)).fetchone()
            if operator:
                data = OperatorRequests.receipt(store,identifier)
            elif control:
                data = read_control_receipt(entry['path'],identifier)
            elif instruction:
                data = read_guidance_receipt(entry['path'],identifier)
            elif identifier in self._requests:
                raw,task = self._requests[identifier]
                if strict_loads(raw)[0] != key:
                    raise _error('conflict')
                data = {'request_id':identifier,'state':'pending'} if not task.done() else {'request_id':identifier,'state':'completed','receipt':task.result()}
            else:
                raise _error('query.missing')
            return {'instance_id':store.instance_id,**data}
        if resource == 'effect':
            _text(identifier,'effect_id')
            with store.transaction() as db:
                row = db.execute('SELECT * FROM effects WHERE effect_id=?',(identifier,)).fetchone()
            if row is None:
                raise _error('query.missing')
            return {'instance_id':store.instance_id,'effect':dict(row)}
        return CoordinatorQueries(store).page(resource,mission_id=mission_id,limit=limit,cursor=cursor)

    async def _run(self, key, service, stop):
        errors = []
        try:
            await service.serve(stop)
        except BaseException as exc:
            errors.append(exc)
        try:
            await service.aclose()
        except BaseException as exc:
            errors.append(exc)
        if errors:
            self._runs[key]['error'] = errors[0]
        return errors

    async def dispatch(self, key, action, payload, request_id):
        self._entry(key)
        _text(request_id,'request_id',limit=96)
        if action not in {'start','stop','submit','control','steer','source','policy','requeue','verify','media','reply','answer'} or not isinstance(payload,dict):
            raise _error('invalid',field='payload')
        raw = _json([key,action,payload])
        if len(raw.encode('utf-8'))>524288:
            raise _error('invalid',field='payload')
        prior = self._requests.get(request_id)
        if prior:
            if prior[0] != raw:
                raise _error('event-conflict')
            return await join_cleanup(prior[1])
        if len(self._requests)>=1024:
            raise _error('capacity')
        task = asyncio.create_task(self._dispatch(key,action,payload,request_id))
        self._requests[request_id] = (raw,task)
        return await join_cleanup(task)

    async def _dispatch(self, key, action, payload, request_id):
        entry = self._entry(key)
        spec,path = entry['spec'],entry['path']
        if action in {'start','stop'}:
            async with self._runtime_locks.setdefault(key,asyncio.Lock()):
                return await self._runtime(key,action,payload,request_id)
        if action == 'media':
            if set(payload) != {'trigger_id','resource_key'}:
                raise _error('invalid',field='payload')
            from .superagent_media import SourceMedia
            result = await SourceMedia(open_state(spec),spec).fetch(**payload)
        elif action == 'answer':
            if set(payload)-{'text','attention_id','source_id','actor_id','thread_id'} or not {'text','attention_id'}<=set(payload):
                raise _error('invalid',field='payload')
            result = answer_local(path,request_id=request_id,**payload)
        elif action == 'submit':
            if set(payload)-{'text','source_id','actor_id','thread_id'} or 'text' not in payload:
                raise _error('invalid',field='payload')
            result = submit_local(path,request_id=request_id,**payload)
        elif action == 'control':
            if set(payload) != {'action','mission_id'}:
                raise _error('invalid',field='payload')
            result = control_local(path,request_id=request_id,**payload)
        elif action == 'steer':
            if set(payload)-{'text','job_id','source_id','actor_id','thread_id'} or not {'text','job_id'}<=set(payload):
                raise _error('invalid',field='payload')
            result = steer_local(path,request_id=request_id,**payload)
        else:
            operator = CoordinatorOperator(open_state(spec),spec)
            if action == 'verify':
                run = self._runs.get(key)
                result = await operator.verify(payload,request_id,runtime=run['service'] if run and not run['task'].done() else None)
            else:
                result = operator.edit(action,payload,request_id)
                run = self._runs.get(key)
                if action == 'source' and run and not run['task'].done():
                    await run['service'].refresh_sources()
        return {'instance_id':spec['instance_id'],'request_id':request_id,'receipt':result}

    async def _runtime(self,key,action,payload,request_id):
        entry = self._entry(key)
        spec,path = entry['spec'],entry['path']
        if action in {'start','stop'}:
            if payload:
                raise _error('invalid',field='payload')
            run = self._runs.get(key)
            if action == 'start':
                if run is None or run['task'].done():
                    service = await self._factory(path)
                    if self._closing:
                        await service.aclose()
                        raise _error('closed')
                    stop = asyncio.Event()
                    self._runs[key] = {'service':service,'stop':stop,'error':None,
                        'task':asyncio.create_task(self._run(key,service,stop))}
            elif run is not None:
                run['stop'].set()
                errors = await join_cleanup(run['task'])
                raise_cleanup_errors(errors)
            return {'instance_id':spec['instance_id'],'request_id':request_id,'receipt':self.connection(key)}

    async def close(self):
        if self._close_task is None:
            self._closing = True
            self._close_task = asyncio.create_task(self._close())
        await join_cleanup(self._close_task)

    async def _close(self):
        errors = []
        tasks = [task for _,task in self._requests.values() if not task.done()]
        if tasks:
            await asyncio.gather(*tasks,return_exceptions=True)
        for run in self._runs.values():
            run['stop'].set()
        for run in self._runs.values():
            errors.extend(await join_cleanup(run['task']))
        raise_cleanup_errors(errors)
