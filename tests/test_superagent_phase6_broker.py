"""凭据代理与隔离命令的定向边界；不将命令生成当作 Docker 实机隔离。"""
import asyncio
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from harness.app.superagent_secret_broker import SecretBroker,BrokerTransport,BROKER_SCOPE
from harness.app.superagent_sandbox import sandbox_command
from harness.core.errors import HarnessError
from harness.core.language import current_language


class BrokerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.calls=[];self.valid=True
        def forward(body):self.calls.append(body);return b'data: [DONE]\n\n'
        self.broker=SecretBroker(self.root/'broker.sock',upstream='https://model.invalid/v1',api_key='never-expose-fixture-key',
            models={'fixture'},lease_valid=lambda mission,generation:self.valid and (mission,generation)==('mission',1),forward=forward)
        self.addAsyncCleanup(self.broker.close)

    def grant(self,**values):
        return self.broker.grant(mission_id='mission',generation=1,model='fixture',max_requests=1,expires_at=int(time.time())+30,**values)

    async def test_real_unix_transport_consumes_scoped_capability_without_forwarding_worker_headers(self):
        token=self.grant();transport=BrokerTransport(self.root/'broker.sock')
        context=BROKER_SCOPE.set(token)
        try:
            with self.assertRaises(HarnessError):
                _=[chunk async for chunk in transport.post_stream('https://arbitrary.invalid',headers={},body=b'{"model":"foreign"}',timeout=5)]
            self.assertEqual(self.calls,[])
            chunks=[chunk async for chunk in transport.post_stream('https://arbitrary.invalid',headers={'Authorization':'attacker'},body=b'{"model":"fixture","messages":[]}',timeout=5)]
        finally:BROKER_SCOPE.reset(context)
        self.assertEqual(chunks,[b'data: [DONE]\n\n'])
        self.assertEqual(self.calls,[{'model':'fixture','messages':[]}])
        self.assertNotIn('never-expose-fixture-key',json.dumps(self.broker.audit))
        self.assertNotIn(token,json.dumps(self.broker.audit))
        with self.assertRaises(HarnessError):self.broker.request({'token':token,'body':{'model':'fixture'}})

    async def test_expired_revoked_wrong_model_and_exhausted_capabilities_never_call_upstream(self):
        token=self.grant()
        with self.assertRaises(HarnessError):self.broker.request({'token':token,'body':{'model':'foreign'}})
        self.valid=False
        with self.assertRaises(HarnessError):self.broker.request({'token':token,'body':{'model':'fixture'}})
        self.valid=True
        self.broker.capabilities[next(iter(self.broker.capabilities))]['expires_at']=int(time.time())-1
        with self.assertRaises(HarnessError):self.broker.request({'token':token,'body':{'model':'fixture'}})
        self.assertEqual(self.calls,[])

    async def test_same_broker_error_resolves_english_chinese_english_without_retrying(self):
        errors=[]
        for language in ('en','zh-CN','en'):
            with current_language.set(language),self.assertRaises(HarnessError) as caught:
                self.broker.request({'token':'missing','body':{'model':'fixture'}})
            errors.append(str(caught.exception))
            self.assertEqual(caught.exception.code,'superagent/remote.capability')
        self.assertEqual(errors[0],errors[2]);self.assertNotEqual(errors[0],errors[1]);self.assertEqual(self.calls,[])

    async def test_failed_broker_bind_releases_audit_handle_and_repeated_close_waits_original_cleanup(self):
        opened=[];original=os.fdopen
        def capture(*args,**kwargs):
            stream=original(*args,**kwargs);opened.append(stream);return stream
        with patch('os.fdopen',side_effect=capture),patch('socketserver.ThreadingUnixStreamServer.server_bind',side_effect=OSError('fixture bind rejected')):
            with self.assertRaises(OSError):
                SecretBroker(self.root/'failed.sock',upstream='https://model.invalid/v1',api_key='fixture',models={'fixture'},
                    lease_valid=lambda *args:True,audit_path=self.root/'audit.jsonl')
        self.assertEqual(len(opened),1);self.assertTrue(opened[0].closed)
        await asyncio.gather(self.broker.close(),self.broker.close())
        self.assertFalse((self.root/'broker.sock').exists())


class SandboxCommandTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve()
        for name in ('state','repo','broker','state/workspaces','state/gitviews'):(self.root/name).mkdir()
        self.limits={'image':'example/worker@sha256:'+'a'*64,'memory_mb':512,'cpus':1,'pids':64,'disk_bytes':10485760}
        self.runtime={'host_state':str(self.root/'state'),'profiles':{'work':{'repo':str(self.root/'repo')}}}

    def test_fixed_docker_command_has_no_network_privilege_host_home_or_plaintext_credentials(self):
        with patch('harness.app.superagent_sandbox.shutil.which',return_value='/fixture/docker'),patch('os.getuid',return_value=501):
            argv,roots=sandbox_command(self.limits,broker_dir=self.root/'broker',runtime_config=self.runtime)
        for flag in ('--read-only','--network=none','--cap-drop=ALL','--security-opt=no-new-privileges:true','--pids-limit','--memory','--cpus','--pull=never'):
            self.assertIn(flag,argv)
        self.assertEqual(roots,{str(self.root/'repo'):'/worker/seeds/work'})
        self.assertNotIn('--privileged',argv);self.assertNotIn('--net=host',argv)
        self.assertFalse(any('API_KEY' in arg or '/var/run/docker.sock' in arg for arg in argv))
        self.assertNotIn(str(Path.home()),' '.join(argv))

    def test_unavailable_sandbox_and_unpinned_or_root_config_fail_without_a_fallback(self):
        with patch('harness.app.superagent_sandbox.shutil.which',return_value=None),patch('os.getuid',return_value=501),self.assertRaises(HarnessError) as caught:
            sandbox_command(self.limits,broker_dir=self.root/'broker',runtime_config=self.runtime)
        self.assertEqual(caught.exception.code,'superagent/remote.sandbox-unavailable')
        for change in ({'image':'worker:latest'},{'uid':0},{'memory_mb':0}):
            with self.subTest(change=change),self.assertRaises(HarnessError):
                sandbox_command({**self.limits,**change},broker_dir=self.root/'broker',runtime_config=self.runtime)


class ToolBoundaryTests(unittest.TestCase):
    def test_absolute_targets_symlinks_and_glob_escape_are_rejected_but_original_workspace_is_allowed(self):
        from harness.app.superagent_process_isolation import scoped_arguments,command_as_user
        import base64
        import shlex
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);workspace=root/'workspace';workspace.mkdir();outside=root/'private';outside.write_text('preserve')
            (workspace/'link').symlink_to(outside)
            for name,args in (('read',{'path':str(outside)}),('write',{'path':'link'}),('bash',{'cwd':'..'}),('glob',{'pattern':'../*'})):
                with self.subTest(name=name,args=args),self.assertRaises(HarnessError):scoped_arguments(name,args,str(workspace))
            self.assertEqual(scoped_arguments('write',{'path':'new.txt'},str(workspace)),workspace.resolve())
            command='printf "$(id)"; touch "$HOME/result"'
            words=shlex.split(command_as_user(command,uid=10001,cwd=str(workspace)))
            self.assertNotIn(command,words)
            self.assertEqual(base64.b64decode(words[-1]).decode(),command)
            self.assertEqual(outside.read_text(),'preserve')

    def test_namespace_uid_setup_fails_closed_on_this_unsupported_host_without_changing_identity(self):
        from harness.app.superagent_process_isolation import ProcessIsolation
        with patch('harness.app.superagent_process_isolation.sys.platform','darwin'),self.assertRaises(HarnessError):
            ProcessIsolation(None,None)

    def test_queued_filesystem_request_revalidates_revoked_lease_before_starting_helper(self):
        from concurrent.futures import ThreadPoolExecutor
        from types import SimpleNamespace
        import threading
        from harness.app.superagent_process_isolation import ProcessIsolation
        from harness.app.superagent_common import _error
        isolated=ProcessIsolation.__new__(ProcessIsolation);lock=threading.Lock();lock.acquire()
        isolated.fs_locks={'worker':lock};isolated.fs_workers={};valid=True;entered=threading.Event()
        def check():
            if not valid:raise _error('remote.lease')
        def invoke():
            entered.set();return isolated.fs_call({'worker_id':'worker'},'write',{'path':'file','content':'unsafe'},SimpleNamespace(check_execution=check),10001)
        with patch('subprocess.Popen') as spawn,ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(invoke)
            try:self.assertTrue(entered.wait(1));valid=False
            finally:lock.release()
            with self.assertRaises(HarnessError):future.result(timeout=2)
            spawn.assert_not_called()
        self.assertEqual(isolated.fs_workers,{})

    def test_filesystem_helper_keeps_observation_cache_and_consumes_async_grep(self):
        from harness.app.superagent_process_isolation import serve_filesystem
        from harness.app.superagent_remote_protocol import encode,decode
        import io
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'file.txt').write_text('original\n')
            calls=[('read',{'path':'file.txt'}),('write',{'path':'file.txt','content':'updated\n'}),('grep',{'pattern':'updated','path':'.'})]
            packets=[{'name':name,'args':args,'session_id':'session','call_id':str(index),'turn':1,'step':index,'language':'en'} for index,(name,args) in enumerate(calls)]
            output=io.BytesIO();serve_filesystem(str(root),io.BytesIO(b''.join(encode(value)+b'\n' for value in packets)),output)
            results=[decode(line) for line in output.getvalue().splitlines()]
            self.assertTrue(all(value['error'] is None for value in results),results)
            self.assertEqual((root/'file.txt').read_text(),'updated\n')
            self.assertIn('updated',str(results[-1]['value']))

    def test_same_filesystem_bridge_consumes_scope_error_english_chinese_english(self):
        from harness.app.superagent_process_isolation import serve_filesystem
        from harness.app.superagent_remote_protocol import encode,decode
        import io
        with tempfile.TemporaryDirectory() as directory:
            output=io.BytesIO()
            packets=[{'name':'read','args':{'path':'/outside-private'},'session_id':'session','call_id':str(index),'turn':1,'step':index,'language':lang} for index,lang in enumerate(('en','zh-CN','en'))]
            serve_filesystem(directory,io.BytesIO(b''.join(encode(value)+b'\n' for value in packets)),output)
            errors=[decode(line)['error'] for line in output.getvalue().splitlines()]
            self.assertEqual(errors[0]['message'],errors[2]['message']);self.assertNotEqual(errors[0]['message'],errors[1]['message'])
            self.assertTrue(all(error['code']=='superagent/remote.tool-scope' for error in errors))

    def test_private_git_view_supports_git_without_mutating_kernel_refs(self):
        from harness.app.superagent_process_isolation import private_git_view
        from harness.app.git_process import run_git
        from tests.superagent_product_fixture import product_config
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve();product_config(root)
            source=root/'repo';workspace=root/'workspace';view=root/'git-view'
            base=run_git(source,'rev-parse','HEAD').stdout.decode().strip()
            self.assertEqual(run_git(source,'worktree','add','--detach',str(workspace),base).returncode,0)
            pointer=(workspace/'.git').read_bytes()
            self.assertTrue(private_git_view(view,source,base,workspace))
            self.assertFalse(private_git_view(view,source,base,workspace))
            (workspace/'main.txt').write_text('private view change\n')
            environment={'GIT_DIR':str(view),'GIT_WORK_TREE':str(workspace)}
            self.assertIn(b'private view change',run_git(workspace,'diff',env=environment).stdout)
            self.assertEqual(run_git(workspace,'add','main.txt',env=environment).returncode,0)
            self.assertEqual(run_git(workspace,'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-m','private',env=environment).returncode,0)
            self.assertEqual(run_git(source,'rev-parse','HEAD').stdout.decode().strip(),base)
            self.assertEqual((workspace/'.git').read_bytes(),pointer)

    def test_actual_cgroup_values_must_be_bounded_and_complete_before_execution(self):
        from harness.app.superagent_process_isolation import verify_cgroups
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            values={'memory.max':str(512*1024*1024),'pids.max':'64','memory.swap.max':'0','cpu.max':'100000 100000'}
            for key,value in values.items():(root/key).write_text(value)
            limits={'memory_mb':512,'pids':64,'cpus':1}
            verify_cgroups(limits,root)
            for key,value in (('memory.max','max'),('pids.max','128'),('memory.swap.max','1'),('cpu.max','max 100000')):
                (root/key).write_text(value)
                with self.subTest(key=key),self.assertRaises(HarnessError):verify_cgroups(limits,root)
                (root/key).write_text(values[key])
            (root/'cpu.max').unlink()
            with self.assertRaises(HarnessError):verify_cgroups(limits,root)

    def test_kernel_capture_ignores_worker_replaced_git_config_and_keeps_bounded_object_reads(self):
        from harness.app.superagent_process_isolation import IsolatedWorktreeService
        from harness.app.git_process import run_git
        from tests.superagent_product_fixture import product_config
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve();product_config(root)
            manager=IsolatedWorktreeService(root/'workspaces')
            row=manager.create(str(root/'repo'));workspace=Path(row['path'])
            attacker=root/'attacker';run_git(root,'init','--bare',str(attacker))
            marker=root/'untrusted-config-was-executed'
            run_git(attacker,'config','core.fsmonitor','touch '+str(marker))
            (workspace/'.git').write_text('gitdir: '+str(attacker)+'\n')
            (workspace/'main.txt').write_text('actual delivery\n')
            receipt=manager.capture_delivery(row['id'])
            content=manager.run_git(workspace,'show',receipt['ref']+':main.txt',output_limit=8).stdout
            self.assertEqual(content,b'actual de')
            self.assertFalse(marker.exists())
            self.assertEqual(manager.git(workspace,'rev-parse','--show-toplevel').strip(),str(workspace))


class DockerContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_owned_container_kill_keeps_pinned_engine_and_filtered_environment(self):
        from harness.app.superagent_sandbox import SandboxNode
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        node=SandboxNode.__new__(SandboxNode);node.process=SimpleNamespace(returncode=None)
        node.command=['/fixture/docker','--host','unix:///owned.sock','run','--name','owned-container'];node.container='owned-container'
        node.docker_environment={'PATH':'/usr/bin:/bin','HOME':'/fixture/home'}
        killer=SimpleNamespace(returncode=0,communicate=AsyncMock(return_value=(b'',b'')))
        with patch.dict(os.environ,{'DOCKER_CONTEXT':'foreign','HARNESS_API_KEY':'never-forward'}),patch('asyncio.create_subprocess_exec',return_value=killer) as spawn:
            await node._kill()
        self.assertEqual(spawn.call_args.args,('/fixture/docker','--host','unix:///owned.sock','kill','owned-container'))
        self.assertEqual(spawn.call_args.kwargs['env'],node.docker_environment)
        self.assertNotIn('DOCKER_CONTEXT',spawn.call_args.kwargs['env'])

    async def test_probe_pins_rootless_socket_and_does_not_pass_model_secrets_to_docker_client(self):
        from harness.app.superagent_sandbox import docker_engine
        from unittest.mock import AsyncMock
        calls=[]
        async def spawn(*command,**kwargs):
            calls.append((command,kwargs))
            output=b'"unix:///run/user/501/docker.sock"' if 'inspect' in command else b'["name=rootless","name=seccomp,profile=builtin"]'
            return type('Process',(),{'communicate':AsyncMock(return_value=(output,b'')),'returncode':0})()
        with patch.dict(os.environ,{'DOCKER_CONTEXT':'rootless','DOCKER_HOST':'','HARNESS_API_KEY':'do-not-forward'},clear=False),patch('asyncio.create_subprocess_exec',side_effect=spawn):
            engine,environment=await docker_engine('/fixture/docker')
        self.assertEqual(engine,['/fixture/docker','--host','unix:///run/user/501/docker.sock'])
        self.assertNotIn('HARNESS_API_KEY',environment)
        self.assertIn('--host',calls[-1][0]);self.assertNotIn('DOCKER_CONTEXT',environment)

    async def test_rootful_or_remote_engine_is_rejected_instead_of_falling_back(self):
        from harness.app.superagent_sandbox import docker_engine
        from unittest.mock import AsyncMock
        process=type('Process',(),{'communicate':AsyncMock(return_value=(b'["name=seccomp"]',b'')),'returncode':0})()
        with patch.dict(os.environ,{'DOCKER_HOST':'unix:///fixture.sock'}),patch('asyncio.create_subprocess_exec',return_value=process),self.assertRaises(HarnessError):
            await docker_engine('/fixture/docker')
        with patch.dict(os.environ,{'DOCKER_HOST':'tcp://engine.invalid:2375'}),self.assertRaises(HarnessError):
            await docker_engine('/fixture/docker')
