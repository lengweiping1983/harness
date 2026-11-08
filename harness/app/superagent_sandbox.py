"""固定镜像的隔离 Worker 进程：无网络、Rootless 身份、只挂载登记资源，失败不降级。"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import re
import shutil
import time
import uuid

from ..core.cleanup import join_cleanup,raise_cleanup_errors
from .superagent_common import _error
from .superagent_remote_protocol import encode,decode,MAX_FRAME


def sandbox_command(config,*,broker_dir,runtime_config):
    required={'image','memory_mb','cpus','pids','disk_bytes'}
    if (not isinstance(config,dict) or set(config)!=required or not isinstance(config['image'],str)
            or not re.fullmatch(r'(?:[A-Za-z0-9./:_-]+@)?sha256:[0-9a-f]{64}',config['image'])):raise _error('remote.sandbox')
    for key,low,high in (('memory_mb',128,65536),('cpus',1,64),('pids',16,1024),('disk_bytes',1048576,107374182400)):
        if type(config[key]) is not int or not low<=config[key]<=high:raise _error('remote.sandbox')
    if not hasattr(os,'getuid') or os.getuid()==0:raise _error('remote.sandbox')
    executable=shutil.which('docker')
    if executable is None:raise _error('remote.sandbox-unavailable')
    command=[executable,'run','--name','harness-worker-'+uuid.uuid4().hex,'--rm','--pull=never','--interactive','--read-only','--network=none','--cap-drop=ALL',
        '--security-opt=no-new-privileges:true','--cgroupns=private','--user','0:0',
        '--cap-add=SETUID','--cap-add=SETGID','--cap-add=CHOWN','--cap-add=DAC_OVERRIDE','--cap-add=FOWNER','--cap-add=KILL',
        '--memory',str(config['memory_mb'])+'m','--memory-swap',str(config['memory_mb'])+'m',
        '--cpus',str(config['cpus']),'--pids-limit',str(config['pids']),
        '--tmpfs','/tmp:rw,noexec,nosuid,nodev,size=128m','--ulimit','nofile=256:256',
        '--ulimit','fsize='+str(config['disk_bytes'])+':'+str(config['disk_bytes']),
        '--env','HOME=/worker/state/home','--env','TMPDIR=/tmp','--env','PYTHONUTF8=1',
        '--env','HARNESS_WORKER_SANDBOX=1','--env','PYTHONNOUSERSITE=1','--env','PYTHONDONTWRITEBYTECODE=1',
        '--env','PATH=/usr/local/bin:/usr/bin:/bin','--entrypoint','python']
    mounts=[(runtime_config['host_state'],'/worker/state',False),(str(broker_dir),'/worker/broker',True),
        (str(Path(runtime_config['host_state'])/'workspaces'),'/worker/workspaces',False),
        (str(Path(runtime_config['host_state'])/'gitviews'),'/worker/gitviews',False)]
    roots={}
    for key in sorted(runtime_config['profiles']):
        profile=runtime_config['profiles'][key]
        roots.setdefault(profile['repo'],'/worker/seeds/'+key)
    mounts.extend((source,target,True) for source,target in roots.items())
    for source,target,readonly in mounts:
        path=Path(source).expanduser().resolve(strict=True)
        if not path.is_dir() or path==Path.home() or path==Path('/') or ',' in str(path) or '\n' in str(path):raise _error('remote.sandbox')
        command.extend(['--mount','type=bind,src='+str(path)+',dst='+target+(',readonly' if readonly else '')])
    command.extend([config['image'],'-m','harness.superagent_worker','--stdio'])
    return command,roots


async def docker_engine(executable):
    """固定已验证的本机 Engine 套接字，避免探测与启动使用不同 Docker 上下文。"""
    import json
    allowed={'PATH','HOME','DOCKER_CONFIG','XDG_RUNTIME_DIR','SSL_CERT_FILE','SSL_CERT_DIR'}
    environment={key:value for key,value in os.environ.items() if key in allowed}
    environment['HOME']=str(Path.home())
    endpoint=os.environ.get('DOCKER_HOST')
    async def run(*args):
        process=await asyncio.create_subprocess_exec(executable,*args,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,env=environment)
        try:output,_=await asyncio.wait_for(process.communicate(),15)
        except BaseException:
            if process.returncode is None:process.kill()
            await process.wait();raise
        if process.returncode!=0:raise _error('remote.sandbox-unavailable')
        return output.decode('utf-8').strip()
    if not endpoint:
        context=os.environ.get('DOCKER_CONTEXT') or await run('context','show')
        endpoint=json.loads(await run('context','inspect','--format','{{json .Endpoints.docker.Host}}','--',context))
    if not isinstance(endpoint,str) or not endpoint.startswith('unix:///') or '\n' in endpoint:raise _error('remote.sandbox')
    base=[executable,'--host',endpoint]
    options=json.loads(await run('--host',endpoint,'info','--format','{{json .SecurityOptions}}'))
    if not isinstance(options,list) or not any(isinstance(item,str) and item.startswith('name=rootless') for item in options):raise _error('remote.sandbox')
    return base,environment


class SandboxNode:
    """隔离进程只接收原生端口帧；已发出的未知操作从不在新进程重放。"""
    def __init__(self,process,config,broker,roots,command,environment=None):
        self.process,self.config,self.broker,self.roots=process,config,broker,roots
        self.command=command;self.container=command[command.index('--name')+1]
        self.docker_environment=dict(environment or {})
        self.lock=asyncio.Lock();self.closed=False;self.failure=None
        self.monitor=asyncio.create_task(self._monitor());self.errors=bytearray()
        self.stderr=asyncio.create_task(self._stderr());self._close_task=None

    @classmethod
    async def open(cls,config,broker):
        workspace_root=Path(config['host_state'])/'workspaces'
        workspace_root.mkdir(mode=0o711,exist_ok=True)
        (Path(config['host_state'])/'gitviews').mkdir(mode=0o711,exist_ok=True)
        command,roots=sandbox_command(config['sandbox'],broker_dir=broker.path.parent,runtime_config=config)
        engine,environment=await docker_engine(command[0])
        command=[*engine,*command[1:]]
        native={key:value for key,value in config.items() if key in {'instance_id','device_id','identity_hash','model','language','profiles','default_profiles','max_workers','permission_policy','coordinator_profiles','coordinator_repos'}}
        native['provider_root']=config['base_url']
        native['seed_repos']=roots
        native['profiles']={key:{**value,'repo':'/worker/state/repos/'+roots[value['repo']].rsplit('/',1)[1]} for key,value in native['profiles'].items()}
        native['limits']=config['sandbox'];native['isolated_processes']=True;native['workspace_root']='/worker/workspaces';native['disk_bytes']=config['sandbox']['disk_bytes']
        native['state_dir']='/worker/state';native['broker_socket']='/worker/broker/'+broker.path.name
        process=await asyncio.create_subprocess_exec(*command,stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,
            env=environment,start_new_session=True,limit=MAX_FRAME+1)
        node=cls(process,config,broker,roots,command,environment)
        try:
            process.stdin.write(encode(native)+b'\n');await process.stdin.drain()
            ready=decode(await asyncio.wait_for(process.stdout.readline(),30))
            if ready!={'ready':True,'protocol':1}:raise _error('remote.sandbox')
            return node
        except BaseException as original:
            try:await node.close()
            except BaseException as cleanup:raise_cleanup_errors([original,cleanup])
            raise

    async def _stderr(self):
        while data:=await self.process.stderr.read(8192):
            self.errors.extend(data);del self.errors[:-32768]

    async def _monitor(self):
        code=await self.process.wait()
        if code!=0:self.failure=_error('remote.quota' if code==78 else 'remote.quota-observation' if code==79 else 'remote.sandbox')

    def lease_valid(self,mission_id,generation):
        lease=self.leases.get(mission_id) if hasattr(self,'leases') else None
        return bool(lease and lease['generation']==generation and lease['expires_at']>time.time() and not self.closed and self.failure is None and self.process.returncode is None)

    async def dispatch(self,value):
        async with self.lock:
            if self.closed or self.failure or self.process.returncode is not None:raise self.failure or _error('remote.unavailable')
            value=dict(value)
            lease=value.get('lease')
            if lease:
                if not hasattr(self,'leases'):self.leases={}
                old=self.leases.get(lease['mission_id'])
                if old and old['generation']!=lease['generation']:raise _error('remote.lease')
                revoked=value['operation']=='revoke' or old is not None and old['expires_at']==0
                self.leases[lease['mission_id']]={**lease,'expires_at':0 if revoked else max(lease['expires_at'],old['expires_at'] if old else 0)}
                if not revoked:self.broker.renew(lease)
            if value['operation']=='submit':
                worker=value['params']['worker'];profile=self.config['profiles'][worker['profile_id']]
                value['broker_capability']=self.broker.grant(mission_id=worker['mission_id'],generation=lease['generation'],
                    model=profile.get('model') or self.config['model'],max_requests=profile.get('max_turns',1)*64,expires_at=lease['expires_at'])
            self.process.stdin.write(encode(value)+b'\n');await self.process.stdin.drain()
            # 不能在超时后发送下一帧并误消费上一操作的结果；管道失效保留未知。
            try:
                result=decode(await asyncio.wait_for(self.process.stdout.readline(),120))
                if set(result)!={'operation_id','result','error'} or result['operation_id']!=value['operation_id']:raise _error('remote.frame')
                if result['error']:raise _error('remote.operation')
                return result['result']
            except (TimeoutError,ValueError,OSError) as exc:
                self.failure=exc;raise _error('remote.unknown') from exc

    async def _kill(self):
        if self.process.returncode is not None:return
        base=self.command[:self.command.index('run')] if 'run' in self.command else [self.command[0]]
        killer=await asyncio.create_subprocess_exec(*base,'kill',self.container,
            stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,env=self.docker_environment)
        try:await asyncio.wait_for(killer.communicate(),15)
        except BaseException:
            if killer.returncode is None:killer.kill()
            await killer.wait();raise _error('remote.unknown')
        if killer.returncode!=0 and self.process.returncode is None:raise _error('remote.unknown')

    async def close(self):
        if self._close_task is None:
            self.closed=True
            async def finish():
                async with self.lock:
                    self.process.stdin.close()
                    try:await asyncio.wait_for(self.process.wait(),30)
                    except TimeoutError:
                        await self._kill()
                        try:await asyncio.wait_for(self.process.wait(),10)
                        except TimeoutError:
                            self.process.kill();await self.process.wait()
                            raise _error('remote.unknown')
                self.monitor.cancel();await asyncio.gather(self.monitor,return_exceptions=True);await self.stderr
                if self.failure:raise self.failure
                if self.process.returncode!=0:raise _error('remote.sandbox')
            self._close_task=asyncio.create_task(finish())
        await join_cleanup(self._close_task)
