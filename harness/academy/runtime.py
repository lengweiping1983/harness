"""课程 Python 环境与操作系统级隔离，缺少隔离器时拒绝执行。"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import venv
from pathlib import Path

from harness.core.cleanup import raise_cleanup_errors
from harness.core.text_encoding import diagnostic_text
from harness.core.macos_process import resident_memory_kib

from .errors import AcademyError
from .files import file_path


class AcademyRuntime:
    """只执行登记的项目内 Python 文件，宿主凭据不进入子进程。"""

    def __init__(self, project_root: Path, *, timeout=15.0, output_limit=32768):
        self.root = project_root.resolve()
        self.timeout = timeout
        self.output_limit = output_limit

    @staticmethod
    def backend() -> str:
        if sys.platform == 'darwin' and Path('/usr/bin/sandbox-exec').is_file():
            return 'seatbelt'
        if sys.platform.startswith('linux') and shutil.which('bwrap'):
            return 'bubblewrap'
        return 'unavailable'

    @property
    def python(self):
        return self.root / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')

    def ensure_environment(self):
        if self.backend() == 'unavailable':
            raise AcademyError('sandbox-unavailable')
        if (self.root / '.venv').is_symlink():
            raise AcademyError('invalid-path')
        if not self.python.exists():
            venv.EnvBuilder(with_pip=False, symlinks=False).create(self.root / '.venv')
        return self.python

    def command(self, command: list[str], check_script: Path | None = None) -> list[str]:
        if not isinstance(command, list) or len(command) < 2 or command[0] != 'python' or not command[1].endswith('.py'):
            raise AcademyError('command-denied')
        script = file_path(self.root, command[1])
        if not script.is_file():
            raise AcademyError('source-not-found')
        python = self.ensure_environment()
        entry = Path(__file__).with_name('sandbox_entry.py').resolve()
        argv = [str(python), '-I', '-B', str(entry), str(self.root), str(check_script or script), *command[2:]]
        allowed = {str(Path(sys.base_prefix).resolve()), str(Path(sys.prefix).resolve()), str(Path(sys.executable).resolve().parent.parent), str(entry.parent), str(self.root)}
        if check_script is not None:
            allowed.add(str(check_script.resolve()))
        if self.backend() == 'seatbelt':
            reads = '\n'.join('(subpath ' + json.dumps(path) + ')' for path in sorted(allowed))
            profile = f'''(version 1)
(deny default)
(allow process*)
(deny process-fork)
(allow file-read-metadata)
(allow sysctl-read)
(allow mach-lookup)
(allow file-read* (literal "/") {reads} (subpath "/usr") (subpath "/System") (subpath "/Library/Apple") (subpath "/private/var/db/dyld") (subpath "/dev") (literal "/private/etc/localtime"))
(allow file-write* (subpath {json.dumps(str(self.root))}) (literal "/dev/null"))
(deny file-write* (subpath {json.dumps(str(self.root / '.venv'))}))
'''
            return ['/usr/bin/sandbox-exec', '-p', profile, *argv]
        mounts = []
        for raw in sorted(allowed | {'/usr', '/lib', '/lib64', '/bin', '/etc/ld.so.cache'}):
            if Path(raw).exists() and not Path(raw).is_relative_to(self.root):
                mounts.extend(['--ro-bind', raw, raw])
        return [shutil.which('bwrap'), '--die-with-parent', '--new-session', '--unshare-all',
                *mounts, '--bind', str(self.root), str(self.root), '--ro-bind', str(self.root / '.venv'), str(self.root / '.venv'),
                '--proc', '/proc', '--dev', '/dev', '--chdir', str(self.root), *argv]

    def run(self, command: list[str], *, check_script: Path | None = None, on_started=None):
        argv = self.command(command, check_script)
        temp = self.root / '.tmp'
        temp.mkdir(exist_ok=True)
        if temp.is_symlink():
            raise AcademyError('invalid-path')
        env = {'PATH': '/usr/bin:/bin', 'HOME': str(self.root), 'TMPDIR': str(temp),
               'PYTHONUTF8': '1', 'PYTHONNOUSERSITE': '1', 'LANG': 'en_US.UTF-8'}
        started = time.time()
        process = subprocess.Popen(argv, cwd=self.root, env=env, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        outputs = [bytearray(), bytearray()]
        truncated = [False, False]
        output_errors = [[], []]
        failures, cleanup_errors, readers = [], [], []
        streams = (process.stdout, process.stderr)
        def drain(stream, index):
            try:
                while chunk := stream.read(4096):
                    room = self.output_limit - len(outputs[index])
                    outputs[index].extend(chunk[:room])
                    if len(chunk) > room:
                        truncated[index] = True
            except BaseException as exc:
                # 读取失败属于本次执行，不能在线程内丢失后返回成功的空输出。
                output_errors[index].append(exc)
            finally:
                try:
                    stream.close()
                except BaseException as exc:
                    # 关闭失败独立汇集，不能只在异常上下文中保留先前读取错误。
                    output_errors[index].append(exc)
        timed_out = False
        memory_limited = False
        peak_memory_kib = 0
        try:
            # 创建之后立即接管收尾，登记回调和任一读取线程启动失败也不遗落进程。
            if on_started is not None:
                on_started()
            for index, stream in enumerate(streams):
                reader = threading.Thread(target=drain, args=(stream, index), daemon=True)
                readers.append(reader)
                reader.start()
            deadline = time.monotonic() + self.timeout
            while process.poll() is None:
                if any(output_errors):
                    break
                if time.monotonic() >= deadline:
                    timed_out = True
                    break
                if sys.platform == 'darwin':
                    try:
                        rss = resident_memory_kib(process.pid)
                    except ProcessLookupError:
                        if process.poll() is not None:
                            break
                        raise
                    peak_memory_kib = max(peak_memory_kib, rss)
                    if rss > 512 * 1024:
                        memory_limited = True
                        break
                time.sleep(0.05)
        except BaseException as exc:
            failures.append(exc)
        finally:
            # 逐项尝试收尾，信号失败不能跳过 wait、其他读取器或未接管的管道。
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except BaseException as exc:
                cleanup_errors.append(exc)
            try:
                process.wait(timeout=5)
            except BaseException as exc:
                cleanup_errors.append(exc)
            for reader in readers:
                if reader.ident is not None:
                    try:
                        reader.join(timeout=5)
                    except BaseException as exc:
                        cleanup_errors.append(exc)
            for index, stream in enumerate(streams):
                if index >= len(readers) or readers[index].ident is None:
                    try:
                        stream.close()
                    except BaseException as exc:
                        cleanup_errors.append(exc)
            if any(reader.is_alive() for reader in readers):
                cleanup_errors.append(AcademyError('cleanup-failed'))
        raise_cleanup_errors([*failures, *(error for errors in output_errors for error in errors), *cleanup_errors])
        return {'status': 'timeout' if timed_out else 'passed' if process.returncode == 0 else 'failed',
                'exit_code': process.returncode, 'stdout': diagnostic_text(bytes(outputs[0]), truncated=truncated[0]),
                'stderr': diagnostic_text(bytes(outputs[1]), truncated=truncated[1]), 'truncated': any(truncated),
                'duration_ms': int((time.time() - started) * 1000), 'sandbox': self.backend(),
                'memory_limited': memory_limited, 'peak_memory_kib': peak_memory_kib}
