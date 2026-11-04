"""原生内存和后代观察的 ABI、权限失败、容量边界与真实进程回归。"""
import asyncio
import ctypes
import errno
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from harness.core import macos_process as native


class MacProcessTests(unittest.TestCase):
    def test_rusage_abi_and_byte_conversion(self):
        self.assertEqual(ctypes.sizeof(native._RusageInfoV0), 96)
        self.assertEqual(native._RusageInfoV0.ri_resident_size.offset, 64)
        def measure(pid, flavor, pointer):
            self.assertEqual((pid, flavor), (123, 0))
            usage = ctypes.cast(pointer, ctypes.POINTER(native._RusageInfoV0)).contents
            usage.ri_resident_size = 4097
            return 0
        with patch.object(native, '_libproc', return_value=SimpleNamespace(proc_pid_rusage=measure)):
            self.assertEqual(native.resident_memory_kib(123), 4)

    def test_native_failures_keep_errno_instead_of_reporting_zero(self):
        for code in (errno.EPERM, errno.ESRCH, errno.EIO):
            def denied(*args):
                ctypes.set_errno(code)
                return -1
            with self.subTest(code=code), patch.object(native, '_libproc',
                    return_value=SimpleNamespace(proc_pid_rusage=denied)):
                with self.assertRaises(OSError) as caught:
                    native.resident_memory_kib(123)
                self.assertEqual(caught.exception.errno, code)

    def test_invalid_pid_never_enters_native_code(self):
        with patch.object(native, '_libproc') as library:
            for pid in (0, -1, True, '123', 1.5, 0x80000000):
                for observe in (native.resident_memory_kib, native.descendant_pids):
                    with self.subTest(pid=pid, observe=observe), self.assertRaises(ValueError):
                        observe(pid)
            library.assert_not_called()

    def test_child_buffer_growth_and_saturation_are_explicit(self):
        sizes = []
        def listing(pid, buffer, size):
            sizes.append(size)
            if len(sizes) == 1:
                return len(buffer)
            buffer[0], buffer[1] = 12, 13
            return 2
        with patch.object(native, '_libproc', return_value=SimpleNamespace(proc_listchildpids=listing)):
            self.assertEqual(native._children(123), [12, 13])
        self.assertEqual(sizes, [256 * 4, 512 * 4])
        with patch.object(native, '_libproc', return_value=SimpleNamespace(
                proc_listchildpids=lambda pid, buffer, size: len(buffer))):
            with self.assertRaises(OSError) as caught:
                native._children(123)
            self.assertEqual(caught.exception.errno, errno.EOVERFLOW)

    def test_zero_children_does_not_hide_permission_failure(self):
        def listing(pid, buffer, size):
            ctypes.set_errno(errno.EPERM)
            return 0
        with patch.object(native, '_libproc', return_value=SimpleNamespace(proc_listchildpids=listing)):
            with self.assertRaises(PermissionError):
                native.descendant_pids(123)
        with patch.object(native, '_libproc', return_value=SimpleNamespace(proc_listchildpids=lambda *args: 0)):
            self.assertEqual(native.descendant_pids(123), [])

    def test_tree_excludes_root_and_duplicates_and_preserves_permission_errors(self):
        children = {1: [2, 3], 2: [4], 3: [1, 4], 4: []}
        with patch.object(native, '_children', side_effect=lambda pid: children[pid]):
            result = native.descendant_pids(1)
        self.assertEqual(set(result), {2, 3, 4})
        self.assertEqual(len(result), 3)
        original = PermissionError(errno.EPERM, '原始观察拒绝')
        with patch.object(native, '_children', side_effect=original):
            with self.assertRaises(PermissionError) as caught:
                native.descendant_pids(1)
        self.assertIs(caught.exception, original)
        with patch.object(native, '_children', side_effect=ProcessLookupError(errno.ESRCH, '已退出')):
            self.assertEqual(native.descendant_pids(1), [])

    @unittest.skipUnless(sys.platform == 'darwin', '仅在 macOS 实测系统 ABI')
    def test_real_child_rss_and_tree_without_ps(self):
        script = "import sys; data=bytearray(8*1024*1024); print('ready', flush=True); sys.stdin.read()"
        with subprocess.Popen([sys.executable, '-c', script], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE) as child:
            try:
                self.assertEqual(child.stdout.readline(), b'ready\n')
                with patch('subprocess.run', side_effect=PermissionError('ps denied')):
                    self.assertGreaterEqual(native.resident_memory_kib(child.pid), 8 * 1024)
                    self.assertIn(child.pid, native.descendant_pids(os.getpid()))
            finally:
                child.communicate(timeout=5)
            self.assertEqual(child.returncode, 0)

    @unittest.skipUnless(sys.platform == 'darwin', '仅在 macOS 实测系统沙箱')
    def test_real_preflight_inside_ps_denied_sandbox(self):
        script = '''
import os, subprocess
from scripts.run_python_tests import check_environment
from harness.core.macos_process import resident_memory_kib, descendant_pids
try:
    subprocess.run(['/bin/ps', '-o', 'rss=', '-p', str(os.getpid())], check=True)
except PermissionError:
    pass
else:
    raise AssertionError('探针未实际拒绝 ps')
check_environment()
assert resident_memory_kib(os.getpid()) > 0
assert descendant_pids(os.getpid()) == []
print('native preflight passed with ps denied')
'''
        result = subprocess.run(['/usr/bin/sandbox-exec', '-p',
            '(version 1)(allow default)(deny process-exec (literal "/bin/ps"))',
            sys.executable, '-X', 'utf8', '-c', script], capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8'))
        self.assertIn(b'native preflight passed with ps denied', result.stdout)


@unittest.skipUnless(sys.platform == 'darwin', '仅在 macOS 实测原生终端收尾')
class MacPtyTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_owned_uses_native_tree_when_ps_cannot_start(self):
        from harness.tools.pty_process import spawn_pty
        with tempfile.TemporaryDirectory() as directory:
            process = await spawn_pty([sys.executable, '-c',
                "import subprocess,sys; child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],"
                "start_new_session=True); print('ready',child.pid,flush=True); child.wait()"],
                cwd=directory, env=dict(os.environ))
            child_pid = None
            try:
                async with asyncio.timeout(5):
                    output = bytearray()
                    while b'ready' not in output:
                        output.extend(await process.read(4096))
                child_pid = int(output.split()[1])
                kill = os.kill
                with patch('harness.tools.pty_process.asyncio.create_subprocess_exec',
                           side_effect=PermissionError('ps denied')) as spawn, \
                        patch('harness.tools.pty_process.os.kill', wraps=kill) as delivered:
                    await process.stop_owned()
                spawn.assert_not_called()
                delivered.assert_any_call(child_pid, signal.SIGKILL)
                self.assertIsNotNone(process.returncode)
            finally:
                if child_pid is not None:
                    try:
                        os.kill(child_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                if process.returncode is None:
                    process.kill()
                    await process.wait()
                process.close()

    async def test_failed_tree_observation_prevents_false_success(self):
        from harness.tools.pty_process import PtyProcess
        from unittest.mock import AsyncMock
        process = object.__new__(PtyProcess)
        process.proc = SimpleNamespace(pid=123, returncode=None, kill=Mock(), wait=AsyncMock())
        process.fd = -1
        original = PermissionError(errno.EPERM, '原始观察拒绝')
        with patch.object(native, 'descendant_pids', side_effect=original):
            with self.assertRaises(PermissionError) as caught:
                await process.stop_owned()
        self.assertIs(caught.exception, original)
        process.proc.kill.assert_not_called()
        process.proc.wait.assert_not_awaited()
