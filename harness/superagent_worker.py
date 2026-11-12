"""远程 Worker 设备入口；装配明确选择的原生 Executor，不运行第二个协调器。"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import fields
from contextlib import asynccontextmanager
import os
import stat
from pathlib import Path
import signal
import ssl
import sys
import tempfile

from .app.superagent_common import _error,_text
from .app.superagent_remote_protocol import decode,encode,MAX_FRAME


def _read_worker_config(path):
    value=decode(Path(path).expanduser().resolve(strict=True).read_bytes())
    required={'version','instance_id','device_id','identity_hash','host_state','model','base_url','language','permission_policy',
        'profiles','coordinator_profiles','coordinator_repos','default_profiles','max_workers','key_env','api_key_env','sandbox','listen'}
    if (set(value)-required-{'runner'} or not required<=set(value) or type(value['version']) is not int
        or value['version']!=1 or value.get('runner','docker') not in ('native','docker')):raise _error('remote.config')
    if value.get('runner','docker')=='native' and value['sandbox'] is not None:raise _error('remote.config')
    if value.get('runner','docker')=='docker' and not isinstance(value['sandbox'],dict):raise _error('remote.config')
    import re
    from .app.superagent_executor import WorkerProfile
    from .app.superagent_http import api_root
    for key in ('instance_id','device_id','model'):_text(value[key],key,limit=128)
    if not isinstance(value['identity_hash'],str) or not re.fullmatch('[0-9a-f]{64}',value['identity_hash']):raise _error('remote.config')
    if value['language'] not in {'en','zh-CN'} or value['permission_policy'] not in {'read_only','full_access'}:raise _error('remote.config')
    value['base_url']=api_root(value['base_url'])
    for key in ('key_env','api_key_env'):
        if not isinstance(value[key],str) or not re.fullmatch('[A-Za-z_][A-Za-z0-9_]{0,127}',value[key]):raise _error('remote.config')
    if type(value['max_workers']) is not int or not 1<=value['max_workers']<=256:raise _error('remote.config')
    root=Path(value['host_state']).expanduser()
    if root.is_symlink() or not root.is_dir():raise _error('remote.config')
    value['host_state']=str(root.resolve())
    if not isinstance(value['profiles'],dict) or not 1<=len(value['profiles'])<=32:raise _error('remote.config')
    for key,profile in value['profiles'].items():
        if not re.fullmatch('[A-Za-z0-9_-]{1,128}',key):raise _error('remote.config')
        WorkerProfile(profile_id=key,**profile)
    if (not isinstance(value['coordinator_profiles'],dict) or set(value['profiles'])!=set(value['coordinator_profiles'])
        or not isinstance(value['coordinator_repos'],dict) or set(value['profiles'])!=set(value['coordinator_repos'])
        or any(not isinstance(item,str) or not re.fullmatch('[0-9a-f]{64}',item) for item in value['coordinator_profiles'].values())):raise _error('remote.config')
    for repo in value['coordinator_repos'].values():_text(repo,'coordinator_repo',limit=4096)
    listen=value['listen']
    if (not isinstance(listen,dict) or set(listen)-{'host','port','cert','key'} or not {'host','port'}<=set(listen)
        or type(listen['port']) is not int or not 1<=listen['port']<=65535):raise _error('remote.config')
    if bool(listen.get('cert'))!=bool(listen.get('key')):raise _error('remote.config')
    import ipaddress
    try:address=ipaddress.ip_address(listen['host'])
    except (ValueError,TypeError) as exc:raise _error('remote.config') from exc
    if not address.is_loopback and not listen.get('cert'):raise _error('remote.tls')
    return value


def read_worker_config(path):
    """格式和声明错误使用固定资源，原配置和原始异常保持可核查。"""
    from .core.errors import HarnessError
    try:return _read_worker_config(path)
    except HarnessError as exc:
        if exc.code=='superagent/remote.frame':raise _error('remote.config') from exc
        raise
    except (ValueError,TypeError,KeyError,UnicodeError,RecursionError) as exc:raise _error('remote.config') from exc


async def native_node(config,*,transport=None):
    """同一设备状态只允许一个原生内核持锁；竞争或未知旧内核从不自动接管。"""
    from contextlib import ExitStack
    from .session.write_lock import event_write_lock
    root=Path(config['state_dir'])
    if root.is_symlink() or root.is_junction():raise _error('remote.identity')
    root.mkdir(parents=True,exist_ok=True)
    stack=ExitStack()
    try:
        try:stack.enter_context(event_write_lock(root/'native-runtime'))
        except OSError as exc:raise _error('runtime-busy') from exc
        return await _native_node(config,transport=transport,runtime_stack=stack)
    except BaseException as original:
        try:stack.close()
        except BaseException as cleanup:
            from .core.cleanup import raise_cleanup_errors
            raise_cleanup_errors([original,cleanup])
        raise


async def _native_node(config,*,transport=None,runtime_stack=None):
    from .app.config import HarnessConfig
    from .app.harness import Harness
    from .app.superagent_executor import HarnessExecutorAdapter,WorkerProfile
    from .app.superagent_remote_node import NativeWorkerNode,NodeLedger
    from .app.superagent_secret_broker import BrokerTransport,BROKER_SCOPE
    root=Path(config['state_dir']);root.mkdir(parents=True,exist_ok=True)
    flags={item.name:False for item in fields(HarnessConfig) if item.name.startswith('enable_')}
    flags.update(enable_fs=True,enable_shell=True,enable_worktrees=True,enable_todo=True)
    if config.get('isolated_processes'):
        if sys.platform!='linux' or os.getuid()!=0:raise _error('remote.sandbox')
        os.umask(0o077);root.chmod(0o700)
        from .app.git_process import run_git
        from .app.superagent_process_isolation import ALLOWED_TOOLS,verify_cgroups
        verify_cgroups(config['limits'])
        for profile in config['profiles'].values():
            if not set(profile['tools'])<=ALLOWED_TOOLS:raise _error('remote.sandbox')
            target=Path(profile['repo'])
            if not target.exists():
                seed=next(value for value in config['seed_repos'].values() if value.rsplit('/',1)[1]==target.name)
                target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
                cloned=run_git(target.parent,'clone','--no-local','--',seed,str(target))
                if cloned.returncode!=0:raise _error('remote.sandbox')
    harness=Harness(HarnessConfig(home=root/'runtime',cwd=root,api_key='broker',model=config['model'],
        base_url='http://broker.invalid/v1',language=config['language'],permission_policy=config['permission_policy'],
        max_steps=64,max_retries=0,**flags),transport=transport or BrokerTransport(config['broker_socket']))
    try:
        if config.get('isolated_processes'):
            from .app.superagent_process_isolation import IsolatedWorktreeService
            harness.worktrees=IsolatedWorktreeService(Path(config['workspace_root']))
        owners=[item for item in harness.sessions.list_sessions() if item.metadata.get('superagent_owner')==config['instance_id']]
        if len(owners)>1:raise _error('remote.identity')
        owner=harness.get_session(owners[0].id) if owners else harness.create_session(metadata={'superagent_owner':config['instance_id']})
        profiles=[WorkerProfile(profile_id=key,**value) for key,value in config['profiles'].items()]
        adapter=HarnessExecutorAdapter(harness,instance_id=config['instance_id'],owner_session_id=owner.id,profiles=profiles,
            max_workers=config['max_workers'],default_profiles=config['default_profiles'])
        if config['permission_policy']=='read_only':
            from .i18n import tr
            def check(tool,args,ctx):
                return tr('superagent.read-only') if tool.effect!='read' or tool.dangerous or tool.approval_required else None
            harness.tools.add_execution_check(check)
        ledger=NodeLedger(root/'worker-native.sqlite3',{'instance_id':config['instance_id'],'device_id':config['device_id'],
            'identity_hash':config['identity_hash'],'runtime':adapter.runtime_manifest(),
            'profiles':{key:profile.fingerprint for key,profile in adapter.profiles.items()}})
        return NativeWorkerNode(adapter,ledger,coordinator_profiles=config['coordinator_profiles'],coordinator_repos=config['coordinator_repos'],sandbox='docker' if config.get('isolated_processes') else 'none',runtime_stack=runtime_stack,broker_scope=BROKER_SCOPE,provider_root=config.get('provider_root',config.get('base_url')),isolated_processes=config.get('isolated_processes',False),repository_sources={profile['repo']:next(seed for seed in config.get('seed_repos',{}).values() if seed.rsplit('/',1)[1]==Path(profile['repo']).name) for profile in config['profiles'].values()} if config.get('isolated_processes') else {})
    except BaseException as original:
        try:await harness.close()
        except BaseException as cleanup:
            from .core.cleanup import raise_cleanup_errors
            raise_cleanup_errors([original,cleanup])
        raise


async def stdio():
    if os.environ.get('HARNESS_WORKER_SANDBOX')!='1':raise _error('remote.sandbox')
    config=decode(sys.stdin.buffer.readline(MAX_FRAME+1))
    from .core.language import current_language
    current_language.set(config['language'])
    node=await native_node(config)
    sys.stdout.buffer.write(encode({'ready':True,'protocol':1})+b'\n');sys.stdout.buffer.flush()
    quota=None
    if config.get('isolated_processes'):
        async def enforce_quota():
            from .i18n import tr
            while True:
                def usage():
                    total=0
                    for path in Path(config['state_dir']).rglob('*'):
                        try:value=path.lstat()
                        except FileNotFoundError:continue
                        if stat.S_ISREG(value.st_mode):total+=value.st_size
                    return total
                try:used=await asyncio.to_thread(usage)
                except OSError:
                    print(tr('superagent.remote.quota-observation'),file=sys.stderr,flush=True);os._exit(79)
                if used>config['disk_bytes']:
                    from .i18n import tr
                    print(tr('superagent.remote.quota'),file=sys.stderr,flush=True)
                    # 资源越限立即结束容器内核；未完成原操作保留 unknown，不伪造回执。
                    os._exit(78)
                await asyncio.sleep(.5)
        quota=asyncio.create_task(enforce_quota())
    try:
        while raw:=await asyncio.to_thread(sys.stdin.buffer.readline,MAX_FRAME+1):
            value=decode(raw)
            try:result,error=await node.dispatch(value),None
            except Exception as exc:result,error=None,getattr(exc,'code','remote/operation')
            sys.stdout.buffer.write(encode({'operation_id':value['operation_id'],'result':result,'error':error})+b'\n');sys.stdout.buffer.flush()
    finally:
        if quota:
            quota.cancel();await asyncio.gather(quota,return_exceptions=True)
        await node.close()


@asynccontextmanager
async def open_worker_node(config,*,api_key,broker_forward=None):
    """只启动显式选择的后端；可信宿主路径不探测或启动 Docker。"""
    from .app.superagent_sandbox import SandboxNode,sandbox_command
    from .app.superagent_secret_broker import SecretBroker,ModelBroker,NativeBrokerTransport
    from .app.superagent_native_runner import NativeRunner
    from contextlib import nullcontext
    runner=config.get('runner','docker')
    if runner not in ('native','docker'):raise _error('remote.config')
    if runner=='native' and config['sandbox'] is not None:raise _error('remote.config')
    context=tempfile.TemporaryDirectory(prefix='harness-worker-broker-') if runner=='docker' else nullcontext(None)
    with context as temporary:
        node=None
        if runner=='docker':
            (Path(config['host_state'])/'workspaces').mkdir(mode=0o711,exist_ok=True)
            (Path(config['host_state'])/'gitviews').mkdir(mode=0o711,exist_ok=True)
            sandbox_command(config['sandbox'],broker_dir=temporary,runtime_config=config)
        options=dict(upstream=config['base_url'],api_key=api_key,
            models={config['model'],*(value.get('model') or config['model'] for value in config['profiles'].values())},
            audit_path=Path(config['host_state'])/'model-proxy-audit.jsonl',forward=broker_forward,
            lease_valid=lambda mission,generation:bool(node and node.lease_valid(mission,generation)))
        broker=SecretBroker(Path(temporary)/'model.sock',**options) if runner=='docker' else ModelBroker(**options)
        try:
            if runner=='docker':node=await SandboxNode.open(config,broker)
            else:
                native=await native_node({**config,'state_dir':config['host_state']},transport=NativeBrokerTransport(broker))
                node=NativeRunner(native,config,broker)
            yield node
        finally:
            from .core.cleanup import raise_cleanup_errors
            errors=[]
            if node:
                try:await node.close()
                except BaseException as exc:errors.append(exc)
            try:await broker.close()
            except BaseException as exc:errors.append(exc)
            raise_cleanup_errors(errors)


async def serve(path):
    from .app.superagent_remote_gateway import WorkerGateway
    config=read_worker_config(path)
    from .core.language import current_language
    current_language.set(config['language'])
    key=os.environ.get(config['key_env'],'').encode();api_key=os.environ.get(config['api_key_env'],'')
    if len(key)<32 or not api_key:raise _error('credentials-required')
    tls=None;listen=config['listen']
    if 'cert' in listen or 'key' in listen:
        tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);tls.minimum_version=ssl.TLSVersion.TLSv1_2
        tls.load_cert_chain(listen['cert'],listen['key'])
    async with open_worker_node(config,api_key=api_key) as node:
        gateway=WorkerGateway(node,instance_id=config['instance_id'],device_id=config['device_id'],identity_hash=config['identity_hash'],
            key=key,host=listen['host'],port=listen['port'],tls=tls)
        previous={};stop=asyncio.Event();loop=asyncio.get_running_loop()
        try:
            # 同步信号入口适用于本机各事件循环；关闭后恢复原宿主处理器。
            for sig in (signal.SIGINT,signal.SIGTERM):
                previous[sig]=signal.signal(sig,lambda *args:loop.call_soon_threadsafe(stop.set))
            from .i18n import tr
            print(tr('superagent.remote.listening',device=config['device_id'],port=gateway.port),flush=True)
            await stop.wait()
        finally:
            for sig,handler in previous.items():signal.signal(sig,handler)
            await gateway.close()


def export_worker_config(config_path,device_id,output):
    """只导出无凭据值的显式设备模板，保留已存在文件，不创建运行状态。"""
    from .app.superagent_config import read_config,config_identity
    from .app.superagent_coordinator_store import _hash,_json
    from .app.superagent_executor import WorkerProfile
    from urllib.parse import urlsplit
    spec=read_config(config_path)
    device=next((item for item in spec.get('execution',{}).get('devices',[]) if item['device_id']==device_id),None)
    if device is None:raise _error('remote.config')
    value={'version':1,'instance_id':spec['instance_id'],'device_id':device_id,'identity_hash':_hash(_json(config_identity(spec))),
        'host_state':'/path/to/worker/state','model':spec['model'],'base_url':spec['base_url'],'language':spec['language'],
        'permission_policy':spec['permission_policy'],'profiles':{key:{**profile,'repo':'/path/to/worker/repo'} for key,profile in spec['profiles'].items()},
        'coordinator_profiles':{key:WorkerProfile(profile_id=key,**profile).fingerprint for key,profile in spec['profiles'].items()},
        'coordinator_repos':{key:profile['repo'] for key,profile in spec['profiles'].items()},'default_profiles':spec['default_profiles'],
        'max_workers':spec['max_workers'],'key_env':spec['secrets'][device['key_ref'][9:]],'api_key_env':'HARNESS_WORKER_MODEL_API_KEY',
        'runner':device.get('runner','docker'),
        'sandbox':None if device.get('runner','docker')=='native' else {'image':'REQUIRED_IMAGE@sha256:REQUIRED_DIGEST','memory_mb':2048,'cpus':2,'pids':128,'disk_bytes':10737418240},
        'listen':{'host':'127.0.0.1','port':urlsplit(device['url']).port or 443}}
    with Path(output).expanduser().open('xb') as stream:stream.write(encode(value)+b'\n')
    return value


def main():
    from .i18n import tr
    parser=argparse.ArgumentParser(description=tr('superagent.remote.cli'))
    parser.add_argument('--config');parser.add_argument('--export-config');parser.add_argument('--device');parser.add_argument('--output');parser.add_argument('--stdio',action='store_true',help=argparse.SUPPRESS)
    args=parser.parse_args()
    if args.export_config:
        if args.config or args.stdio or not args.device or not args.output:parser.error(tr('superagent.remote.config'))
        export_worker_config(args.export_config,args.device,args.output);return
    if args.device or args.output:parser.error(tr('superagent.remote.config'))
    if bool(args.config)==args.stdio:parser.error(tr('superagent.remote.config'))
    from .core.errors import HarnessError
    try:asyncio.run(stdio() if args.stdio else serve(args.config))
    except HarnessError as exc:
        print(str(exc),file=sys.stderr);raise SystemExit(2) from None


if __name__=='__main__':main()
