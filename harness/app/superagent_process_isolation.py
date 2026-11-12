"""容器内把不可信命令与原生账本分配给不同身份，终止使用当前内核句柄。"""
from __future__ import annotations

import base64
import asyncio
import subprocess
import threading
from copy import copy
import os
from pathlib import Path
import shlex
import select
import signal
import sys

from .superagent_common import _error
from .worktrees import WorktreeService

ALLOWED_TOOLS=frozenset({'read','write','edit','glob','grep','bash','job_output','job_kill','job_list'})


class IsolatedWorktreeService(WorktreeService):
    """内核始终使用受保护的 Git 元数据，不加载工作树中可被替换的 .git 入口。"""
    def run_git(self,cwd,*args,input=None,env=None,allowed_returncodes=(0,),output_limit=None):
        target=Path(cwd).resolve(strict=True)
        environment={'GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':'/dev/null',**(env or {})}
        prefix=('-c','core.hooksPath=/dev/null','-c','core.fsmonitor=false')
        if target.is_relative_to(self.root) and target!=self.root:
            rows=[row for row in self.list() if Path(row['path'])==target]
            if len(rows)!=1:raise _error('remote.identity')
            repository=Path(rows[0]['repo']).resolve(strict=True)
            protected=Path(super().run_git(repository,'rev-parse','--absolute-git-dir',env=environment).stdout.decode().strip())
            metadata=protected/'worktrees'/rows[0]['id']
            if (metadata.is_symlink() or not metadata.is_dir()
                or Path((metadata/'gitdir').read_text().strip())!=target/'.git'
                or (metadata/(metadata/'commondir').read_text().strip()).resolve()!=protected):raise _error('remote.identity')
            prefix=(*prefix,'--git-dir='+str(metadata),'--work-tree='+str(target))
        return super().run_git(target,*prefix,*args,input=input,env=environment,
            allowed_returncodes=allowed_returncodes,output_limit=output_limit)


def scoped_arguments(name,args,cwd):
    """文件和命令目录必须留在当前工作树，不能借绝对路径读取内核原件。"""
    root=Path(cwd).resolve(strict=True)
    for key in ('path','cwd'):
        raw=args.get(key)
        if raw is None:continue
        if not isinstance(raw,str):raise _error('remote.tool-scope')
        target=(root/raw).resolve()
        if not target.is_relative_to(root):raise _error('remote.tool-scope')
    if name=='glob':
        pattern=args.get('pattern','')
        if not isinstance(pattern,str) or Path(pattern).is_absolute() or '..' in Path(pattern).parts:raise _error('remote.tool-scope')
    return root


def command_as_user(command,*,uid,cwd,git_dir=None):
    if type(uid) is not int or uid<10000 or uid>10256 or not isinstance(command,str) or not command.strip():raise _error('remote.tool-scope')
    return shlex.join([sys.executable,'-m','harness.app.superagent_process_isolation','--uid',str(uid),'--cwd',str(cwd),
        *(['--git-dir',str(git_dir)] if git_dir else []),'--command',base64.b64encode(command.encode('utf-8')).decode('ascii')])


def private_git_view(path,repository,base_commit,workspace):
    """只在首次创建时从冻结基线建立独立 Git 视图，后续不以 root 读取用户可写配置。"""
    from .git_process import run_git
    path=Path(path)
    if path.exists():return False
    path.mkdir(mode=0o700)
    for args in (('init','--bare',str(path)),('--git-dir='+str(path),'fetch','--no-tags','--depth=1',str(repository),base_commit),
        ('--git-dir='+str(path),'update-ref','refs/heads/work',base_commit),
        ('--git-dir='+str(path),'symbolic-ref','HEAD','refs/heads/work'),
        ('--git-dir='+str(path),'--work-tree='+str(workspace),'read-tree',base_commit)):
        result=run_git(path.parent,*args)
        if result.returncode!=0:raise _error('remote.sandbox')
    return True


def verify_cgroups(limits,root=Path('/sys/fs/cgroup')):
    """启动前核对当前容器的实际资源边界，不把 Docker 参数当作生效证明。"""
    try:
        memory=int((root/'memory.max').read_text().strip())
        pids=int((root/'pids.max').read_text().strip())
        swap=int((root/'memory.swap.max').read_text().strip())
        quota,period=map(int,(root/'cpu.max').read_text().split())
        if not (0<memory<=limits['memory_mb']*1024*1024 and 0<pids<=limits['pids'] and swap==0
                and quota>0 and period>0 and quota<=period*limits['cpus']):raise ValueError
    except (OSError,ValueError,KeyError) as exc:raise _error('remote.sandbox') from exc


class ProcessIsolation:
    def __init__(self,harness,ledger):
        if sys.platform!='linux' or os.getuid()!=0 or not hasattr(os,'pidfd_open') or not hasattr(signal,'pidfd_send_signal'):
            raise _error('remote.sandbox')
        self.harness,self.ledger=harness,ledger
        self.fs_workers={};self.fs_locks={};self.fs_unknown=set();self.git_locks={}
        self.git_root=harness.worktrees.root.parent/'gitviews'
        self.git_root.mkdir(mode=0o711,exist_ok=True);self.git_root.chmod(0o711)
        self.harness.worktrees.root.chmod(0o711)
        old=harness.tools.get('bash');tool=copy(old)
        async def execute(args,ctx):
            scoped_arguments('bash',args,ctx.cwd)
            worker=self.worker(ctx.session_id)
            uid=self.uid(worker['worker_id'])
            ctx.check_execution()
            from ..core.cleanup import join_cleanup
            await join_cleanup(asyncio.create_task(asyncio.to_thread(self.prepare_command,worker,ctx.cwd,uid)))
            updated={**args,'command':command_as_user(args['command'],uid=uid,cwd=ctx.cwd,git_dir=self.git_root/str(uid))}
            return await old.execute(updated,ctx)
        tool.execute=execute;harness.tools.register(tool,replace=True)
        for name in ('read','write','edit','glob','grep'):
            original=harness.tools.get(name);definition=copy(original)
            async def filesystem(args,ctx,selected=name):
                scoped_arguments(selected,args,ctx.cwd)
                worker=self.worker(ctx.session_id);uid=self.uid(worker['worker_id'])
                self.prepare(ctx.cwd,uid)
                from ..core.cleanup import join_cleanup
                return await join_cleanup(asyncio.create_task(asyncio.to_thread(self.fs_call,worker,selected,args,ctx,uid)))
            definition.execute=filesystem;harness.tools.register(definition,replace=True)

    def worker(self,session_id):
        from .superagent_remote_protocol import decode
        with self.ledger.transaction() as db:
            rows=db.execute("SELECT body_json FROM workers WHERE json_extract(body_json,'$.session_id')=?",(session_id,)).fetchall()
        if len(rows)!=1:raise _error('remote.identity')
        return decode(rows[0][0].encode())

    def uid(self,worker_id):
        with self.ledger.transaction() as db:row=db.execute('SELECT rowid FROM workers WHERE worker_id=?',(worker_id,)).fetchone()
        if row is None or not 1<=row[0]<=256:raise _error('remote.identity')
        return 10000+row[0]

    def prepare(self,cwd,uid):
        root=Path(cwd).resolve(strict=True)
        if root.parent!=self.harness.worktrees.root:raise _error('remote.tool-scope')
        self.transfer(root,uid)

    @staticmethod
    def transfer(root,uid):
        handle=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:
            os.fchown(handle,uid,uid);os.fchmod(handle,0o700)
            for _,directories,files,parent in os.fwalk('.',dir_fd=handle,follow_symlinks=False):
                for name in [*directories,*files]:os.chown(name,uid,uid,dir_fd=parent,follow_symlinks=False)
        finally:os.close(handle)

    def prepare_command(self,worker,cwd,uid):
        self.prepare(cwd,uid)
        view=self.git_root/str(uid)
        with self.git_locks.setdefault(worker['worker_id'],threading.Lock()):
            if private_git_view(view,worker['repo'],worker['base_commit'],cwd):
                descriptor=os.open(view,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
                try:
                    for _,directories,files,parent in os.fwalk('.',dir_fd=descriptor,follow_symlinks=False):
                        for name in [*directories,*files]:os.chown(name,uid,uid,dir_fd=parent,follow_symlinks=False)
                    os.fchown(descriptor,uid,uid)
                finally:os.close(descriptor)
            elif view.is_symlink() or view.stat().st_uid not in {0,uid}:raise _error('remote.identity')
            elif view.stat().st_uid==0:self.transfer(view,uid)

    def fs_call(self,worker,name,args,ctx,uid):
        from .superagent_remote_protocol import encode,decode,MAX_FRAME
        from ..core.errors import ToolExecutionError
        with self.fs_locks.setdefault(worker['worker_id'],threading.Lock()):
            ctx.check_execution()
            process=self.fs_workers.get(worker['worker_id'])
            if process is None:
                process=subprocess.Popen([sys.executable,'-I','-m','harness.app.superagent_process_isolation',
                    '--uid',str(uid),'--cwd',ctx.cwd,'--fs-server'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                    stderr=None,env={'PATH':'/usr/local/bin:/usr/bin:/bin','PYTHONUTF8':'1'},start_new_session=True)
                self.fs_workers[worker['worker_id']]=process
            if process.poll() is not None:raise _error('remote.unknown')
            try:
                process.stdin.write(encode({'name':name,'args':args,'session_id':ctx.session_id,'call_id':ctx.call_id,
                    'turn':ctx.turn,'step':ctx.step,'language':self.harness.config.language})+b'\n');process.stdin.flush()
                if not select.select([process.stdout],[],[],60)[0]:
                    handle=os.pidfd_open(process.pid)
                    try:signal.pidfd_send_signal(handle,signal.SIGKILL)
                    finally:os.close(handle)
                    process.wait();raise _error('remote.unknown')
                result=decode(process.stdout.readline(MAX_FRAME+1))
                if set(result)!={'value','error'}:raise _error('remote.frame')
            except Exception:
                self.fs_unknown.add(worker['worker_id']);raise
            if result['error']:
                error=result['error']
                raise ToolExecutionError(error['message'],code=error['code'])
            return result['value']

    def close(self):
        for process in self.fs_workers.values():
            if process.poll() is None:
                process.stdin.close()
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    handle=os.pidfd_open(process.pid)
                    try:signal.pidfd_send_signal(handle,signal.SIGKILL)
                    finally:os.close(handle)
                    process.wait()
            process.stdout.close()
        self.fs_workers.clear()
        # 原生内核收尾后，将自己的工作树归还设备宿主身份，保留全部文件与 Git 原件。
        from .superagent_remote_protocol import decode
        with self.ledger.transaction() as db:rows=db.execute('SELECT body_json FROM workers').fetchall()
        for row in rows:
            worker=decode(row[0].encode())
            if self.processes(worker['worker_id']):raise _error('remote.unknown')
            workspace_id=worker.get('workspace_id')
            if workspace_id:
                path=self.harness.worktrees.root/workspace_id
                if path.is_dir() and not path.is_symlink():self.transfer(path,0)
            view=self.git_root/str(self.uid(worker['worker_id']))
            if view.is_dir() and not view.is_symlink():self.transfer(view,0)

    def processes(self,worker_id):
        uid=self.uid(worker_id);result=[]
        for entry in Path('/proc').iterdir():
            if not entry.name.isdigit():continue
            try:
                lines=(entry/'status').read_text().splitlines()
                actual=next(line for line in lines if line.startswith('Uid:')).split()[1:]
                state=next(line for line in lines if line.startswith('State:')).split()[1]
                if state!='Z' and [str(uid)]*4==actual:
                    helper=self.fs_workers.get(worker_id)
                    if helper is None or helper.poll() is not None or helper.pid!=int(entry.name):result.append(int(entry.name))
            except (FileNotFoundError,ProcessLookupError):continue
        return result

    def stop(self,worker_id):
        uid=self.uid(worker_id)
        for pid in self.processes(worker_id):
            try:handle=os.pidfd_open(pid)
            except ProcessLookupError:continue
            try:
                actual=next(line for line in Path('/proc',str(pid),'status').read_text().splitlines() if line.startswith('Uid:')).split()[1:]
                if actual!=[str(uid)]*4:raise _error('remote.identity')
                signal.pidfd_send_signal(handle,signal.SIGKILL)
            except (FileNotFoundError,ProcessLookupError):pass
            finally:os.close(handle)


def serve_filesystem(cwd,reader,writer):
    """文件辅助进程共享观察缓存；同步与异步内置实现使用同一有界协议。"""
    import inspect
    from ..tools.builtin.fs_tools import build_fs_tools
    from ..tools.registry import ToolRunContext
    from ..core.language import current_language
    from .superagent_remote_protocol import decode,encode,MAX_FRAME
    tools={tool.schema.name:tool for tool in build_fs_tools(cwd)}
    while raw:=reader.readline(MAX_FRAME+1):
        value=decode(raw)
        try:
            with current_language.set(value['language']):
                scoped_arguments(value['name'],value['args'],cwd)
                context=ToolRunContext(call_id=value['call_id'],session_id=value['session_id'],cwd=cwd,turn=value['turn'],step=value['step'])
                result=tools[value['name']].execute(value['args'],context)
                if inspect.isawaitable(result):result=asyncio.run(result)
            response={'value':result,'error':None}
        except Exception as exc:
            response={'value':None,'error':{'message':str(exc),'code':getattr(exc,'code','tool/execution')}}
        writer.write(encode(response)+b'\n');writer.flush()


def main():
    import argparse
    parser=argparse.ArgumentParser(add_help=False);parser.add_argument('--uid',type=int,required=True);parser.add_argument('--cwd',required=True);parser.add_argument('--git-dir');parser.add_argument('--command');parser.add_argument('--fs-server',action='store_true')
    args=parser.parse_args()
    if sys.platform!='linux' or os.getuid()!=0 or not 10001<=args.uid<=10256:raise _error('remote.sandbox')
    if bool(args.command)==args.fs_server:raise _error('remote.sandbox')
    command=base64.b64decode(args.command,validate=True).decode('utf-8') if args.command else None
    # 先清除附加组，再永久放弃内核身份；命令永远不在 root Shell 中展开。
    os.setgroups([]);os.setgid(args.uid);os.setuid(args.uid);os.umask(0o077);os.chdir(args.cwd)
    if args.fs_server:
        import ctypes
        if ctypes.CDLL(None).prctl(4,0,0,0,0)!=0:raise _error('remote.sandbox')
        serve_filesystem(args.cwd,sys.stdin.buffer,sys.stdout.buffer)
        return
    environment={'PATH':'/usr/local/bin:/usr/bin:/bin','HOME':args.cwd,'TMPDIR':'/tmp','LANG':'C.UTF-8','PYTHONUTF8':'1'}
    if args.git_dir:
        environment.update(GIT_DIR=args.git_dir,GIT_WORK_TREE=args.cwd)
    if os.environ.get('HARNESS_SESSION'):environment['HARNESS_SESSION']=os.environ['HARNESS_SESSION']
    os.execve('/bin/sh',['/bin/sh','-c',command],environment)


if __name__=='__main__':main()
