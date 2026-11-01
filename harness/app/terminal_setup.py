"""Windows 终端运行库的固定安装、真实探测与可取消生命周期。"""
from __future__ import annotations

import asyncio
import copy
import importlib
import os
import sys
import time
from collections import OrderedDict
from pathlib import Path
from uuid import UUID

from ..core.errors import HarnessError
from ..core.cleanup import join_cleanup
from ..core.text_encoding import TextDecoder, utf8_env
from ..i18n import tr
from ..tools.owned_process import spawn_owned

PACKAGE = 'pywinpty>=3.0.5,<4'


def _failure(key, code):
    """当前调用保留本地错误正文，浏览器通过资源身份呈现。"""
    return HarnessError(tr(key), code=code, copy={'key': key})


def detect_terminal():
    if os.name != 'nt':
        return {'supported': False, 'ready': True, 'error': None}
    try:
        from ..tools.windows_conpty import conpty_api
        conpty_api()
        return {'supported': True, 'ready': True, 'error': None}
    except ImportError:
        return {'supported': True, 'ready': False, 'error': None}
    except (OSError, AttributeError, ValueError):
        return {'supported': True, 'ready': False, 'error': {'key': 'ui.terminalSetup.runtime.broken'}}


async def verify_terminal():
    """验证实际运行库与控制台，不能把 pip 成功当成终端可用。"""
    from ..tools.conpty_process import spawn_conpty
    import tempfile
    with tempfile.TemporaryDirectory(prefix='harness-conpty-check-') as directory:
        proc = await spawn_conpty([sys.executable, '-u', '-c',
            "import os,sys;print('HARNESS_CONPTY_READY' if sys.stdin.isatty() and sys.stdout.isatty() else 'NO_TTY');print('中文终端');print(*os.get_terminal_size())"],
            cwd=directory, env=utf8_env())
        try:
            async with asyncio.timeout(15):
                output = bytearray()
                while chunk := await proc.stdout.read(4096):
                    output.extend(chunk)
                    if len(output) > 65536:
                        raise ValueError(tr('runtime.validation.e588b0160cb7'))
                code = await proc.wait()
            if code or b'HARNESS_CONPTY_READY' not in output or '中文终端'.encode() not in output:
                raise ValueError(tr('runtime.validation.21d40539b2d4'))
        finally:
            async def finish():
                await proc.stop_owned()
                proc.close()
            await join_cleanup(asyncio.create_task(finish()))


class TerminalSetupService:
    def __init__(self):
        self.jobs = OrderedDict()
        self.task = None
        self.closed = False

    @property
    def running(self):
        return self.task is not None and not self.task.done()

    def snapshot(self, job_id=None):
        if job_id is None:
            job_id = next(reversed(self.jobs), None)
        row = self.jobs.get(job_id)
        if job_id is not None and row is None:
            raise HarnessError(tr('python.terminal_setup.missing'), code='terminal/install-missing')
        return copy.deepcopy(row)

    async def inspect(self):
        return {**await asyncio.to_thread(detect_terminal), 'installation': self.snapshot()}

    async def start(self, args):
        if not isinstance(args, dict) or set(args) != {'request_id'}:
            raise HarnessError(tr('python.terminal_setup.invalid'), code='terminal/install-invalid')
        job_id = args['request_id']
        try:
            if not isinstance(job_id, str) or str(UUID(job_id)) != job_id:
                raise ValueError()
        except ValueError:
            raise HarnessError(tr('python.terminal_setup.invalid'), code='terminal/install-invalid') from None
        if job_id in self.jobs:
            return self.snapshot(job_id)
        if self.closed or self.running:
            raise HarnessError(tr('python.terminal_setup.busy'), code='terminal/install-busy')
        if os.name != 'nt':
            raise HarnessError(tr('python.terminal_setup.platform'), code='terminal/install-platform')
        self.jobs[job_id] = {'id': job_id, 'status': 'running', 'phase': 'detecting',
                             'message': {'key': 'ui.terminalSetup.runtime.detecting'}, 'error': None,
                             'started_at': time.time(), 'ended_at': None}
        while len(self.jobs) > 16:
            self.jobs.popitem(last=False)
        self.task = asyncio.create_task(self._run(job_id), name='terminal-install-' + job_id)
        self.task.add_done_callback(lambda task: self._settled(job_id, task))
        return self.snapshot(job_id)

    def _settled(self, job_id, task):
        job = self.jobs.get(job_id)
        if job is not None and task.cancelled() and job['status'] == 'running':
            job.update(status='cancelled', message={'key': 'ui.terminalSetup.runtime.cancelled'}, ended_at=time.time())

    def _update(self, job_id, phase, message):
        job = self.jobs.get(job_id)
        if job is not None:
            job.update(phase=phase, message=message)

    async def _run(self, job_id):
        state = self.jobs[job_id]
        try:
            detected = await asyncio.to_thread(detect_terminal)
            if not detected['ready']:
                self._update(job_id, 'dependency', {'key': 'ui.terminalSetup.runtime.downloading'})
                await self._command(job_id)
                importlib.invalidate_caches()
            self._update(job_id, 'verifying', {'key': 'ui.terminalSetup.runtime.verifying'})
            await verify_terminal()
            state.update(status='succeeded', phase='complete', message={'key': 'ui.terminalSetup.runtime.complete'})
        except asyncio.CancelledError:
            state.update(status='cancelled', message={'key': 'ui.terminalSetup.runtime.cancelled'})
        except HarnessError as exc:
            state.update(status='failed', message=exc.details.get('copy', exc.message),
                         error={'code': exc.code, 'message': exc.details.get('copy', exc.message)})
        except Exception:
            message = {'key': 'ui.terminalSetup.runtime.failed'}
            state.update(status='failed', message=message, error={'code': 'terminal/install-failed', 'message': message})
        finally:
            state['ended_at'] = time.time()

    async def _command(self, job_id):
        command = [sys.executable, '-m', 'pip', '--disable-pip-version-check', 'install', '--no-input',
                   '--no-warn-script-location', '--only-binary=:all:', '--force-reinstall', PACKAGE]
        proc = await spawn_owned(command, cwd=str(Path(sys.executable).parent), env=utf8_env())
        hints = set()
        async def read(stream):
            decoder = TextDecoder()
            buffer = ''
            while chunk := await stream.read(1024):
                buffer = (buffer + decoder.decode(chunk)).lower()[-4096:]
                for name, markers in {
                    'network': ('connectionerror', 'connection refused', 'could not resolve', 'timed out', 'certificate verify failed'),
                    'permission': ('permission denied', 'access is denied', 'externally-managed-environment'),
                    'pip': ('no module named pip',),
                }.items():
                    if any(marker in buffer for marker in markers):
                        hints.add(name)
                if 'installing collected packages:' in buffer:
                    self._update(job_id, 'dependency', {'key': 'ui.terminalSetup.runtime.installing'})
            decoder.decode(b"", final=True)
            if decoder.error:
                raise _failure('ui.terminalSetup.runtime.encoding', 'terminal/install-encoding')
        readers = [asyncio.create_task(read(stream)) for stream in (proc.stdout, proc.stderr)]
        try:
            try:
                async with asyncio.timeout(180):
                    code = await proc.wait()
                    await asyncio.gather(*readers)
            except TimeoutError:
                raise _failure('ui.terminalSetup.runtime.timeout', 'terminal/install-timeout') from None
            if code:
                hint = next((name for name in ('pip', 'permission', 'network') if name in hints), 'package')
                raise _failure('ui.terminalSetup.runtime.' + hint, 'terminal/install-command')
        finally:
            async def finish():
                await proc.stop_owned()
                await asyncio.gather(*readers, return_exceptions=True)
                proc.close()
            await join_cleanup(asyncio.create_task(finish()))

    async def cancel(self, job_id):
        self.snapshot(job_id)
        if job_id != next(reversed(self.jobs), None):
            raise HarnessError(tr('python.terminal_setup.changed'), code='terminal/install-changed')
        if self.running and not self.task.cancelling():
            self.task.cancel()
        if self.task:
            await asyncio.gather(self.task, return_exceptions=True)
        return self.snapshot(job_id)

    async def close(self):
        self.closed = True
        if self.running:
            await self.cancel(next(reversed(self.jobs)))
