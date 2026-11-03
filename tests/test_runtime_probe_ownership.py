"""真实隔离解释器验证版本探测的输出上限及后代回收。"""
import asyncio
import json
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from harness.tools.runtime_probe import OUTPUT_LIMIT, probe_version


class RuntimeProbeOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_version_command_reports_unknown_after_supervisor_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(await probe_version([str(Path(directory) / 'missing-runtime')], 2), '')

    async def test_public_entry_preserves_temporary_directory_cleanup_failure(self):
        from harness.tools import runtime_probe
        from harness.tools.builtin.workspace_tools import _version_of
        original = PermissionError('Original temporary directory cleanup failure')
        with tempfile.TemporaryDirectory() as directory:
            @contextmanager
            def broken_directory(**kwargs):
                yield directory
                raise original
            with patch.object(runtime_probe.tempfile, 'TemporaryDirectory', broken_directory):
                with self.assertRaises(PermissionError) as caught:
                    await asyncio.to_thread(_version_of, sys.executable)
            self.assertIs(caught.exception, original)

    async def test_version_probe_uses_temporary_directory_and_disables_corepack_installation(self):
        original_cwd = os.getcwd()
        keys = ('COREPACK_ENABLE_NETWORK', 'COREPACK_ENABLE_AUTO_PIN', 'COREPACK_ENABLE_PROJECT_SPEC')
        code = f"import json,os;print(json.dumps({{'cwd':os.getcwd(),'flags':[os.environ.get(key) for key in {keys!r}]}}))"
        with patch.dict(os.environ, {key: '1' for key in keys}):
            value = json.loads(await probe_version([sys.executable, '-c', code], 2))
            self.assertEqual([os.environ[key] for key in keys], ['1', '1', '1'])
        self.assertEqual(value['flags'], ['0', '0', '0'])
        self.assertNotEqual(value['cwd'], original_cwd)
        self.assertFalse(Path(value['cwd']).exists())
        self.assertEqual(os.getcwd(), original_cwd)

    async def test_original_unicode_first_line_and_stderr_fallback(self):
        for code in ("print('Original 版本');print('second')", "import sys;print('Original 版本',file=sys.stderr)"):
            self.assertEqual(await probe_version([sys.executable,'-X','utf8','-c',code],2),'Original 版本')

    async def test_large_output_and_invalid_utf8_do_not_become_versions(self):
        for code in (f"import sys;sys.stdout.write('x'*{OUTPUT_LIMIT+1})", "import os;os.write(1,b'bad\\xff')"):
            self.assertEqual(await probe_version([sys.executable,'-c',code],2),'')

    async def test_timeout_is_empty_only_after_owned_cleanup(self):
        self.assertEqual(await probe_version([sys.executable,'-c','import time;time.sleep(30)'],.1),'')

    async def test_repeated_cancel_waits_for_original_cleanup(self):
        entered,release=asyncio.Event(),asyncio.Event()
        from harness.tools import runtime_probe
        original=runtime_probe.stop_process
        async def stop(process):
            entered.set();await release.wait();await original(process)
        with patch.object(runtime_probe,'stop_process',side_effect=stop):
            task=asyncio.create_task(probe_version([sys.executable,'-c',"print('Original')"],2))
            try:
                await entered.wait();task.cancel();await asyncio.sleep(0);task.cancel();await asyncio.sleep(0)
                self.assertFalse(task.done())
            finally:
                release.set()
                with self.assertRaises(asyncio.CancelledError):await task

    @unittest.skipIf(os.name=='nt','进程存在性用POSIX信号验证，Windows需实机作业树证据')
    async def test_exited_command_cannot_leave_a_pipe_holding_descendant(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'pid'
            child="import time;time.sleep(30)"
            code=f"import subprocess,sys,pathlib;p=subprocess.Popen([sys.executable,'-c',{child!r}]);pathlib.Path({str(path)!r}).write_text(str(p.pid));print('Original',flush=True)"
            self.assertEqual(await probe_version([sys.executable,'-c',code],.5),'')
            pid=int(path.read_text())
            for _ in range(50):
                try:os.kill(pid,0)
                except ProcessLookupError:break
                await asyncio.sleep(.02)
            else:
                # Linux 保留已回收进程的僵尸目录时核对其终态，不能当作存活。
                state=Path(f'/proc/{pid}/stat')
                self.assertTrue(state.exists() and state.read_text().split()[2]=='Z')

    async def test_actual_public_version_entry_runs_in_probe_worker(self):
        from harness.tools.builtin.workspace_tools import _version_of
        value=await asyncio.to_thread(_version_of,sys.executable)
        self.assertEqual(value,'Python '+sys.version.split()[0])

    async def test_exact_output_limit_is_not_mistaken_for_truncation(self):
        code=f"import sys;sys.stdout.write('x'*{OUTPUT_LIMIT})"
        self.assertEqual(await probe_version([sys.executable,'-c',code],2),'x'*OUTPUT_LIMIT)

    async def test_failed_command_diagnostic_is_not_reported_as_version(self):
        for channel in ('stdout', 'stderr'):
            code = f"import sys;print('Original failure diagnostic 原文',file=sys.{channel});sys.exit(3)"
            self.assertEqual(await probe_version([sys.executable,'-X','utf8','-c',code],2), '')

    async def test_blank_first_line_does_not_promote_later_diagnostic_to_version(self):
        """首行为空时保持未知，不能去除前导换行后拿第二行冒充版本。"""
        for prefix in ('\n', '  \n', '\r\n'):
            code = f"import sys;sys.stdout.write({prefix!r}+'Original later diagnostic')"
            self.assertEqual(await probe_version([sys.executable, '-c', code], 2), '')
