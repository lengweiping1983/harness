"""Windows 原生 ConPTY；创建时原子关联 Job，保留完整进程树归属。"""
from __future__ import annotations

from harness.i18n import tr

import ctypes
import contextlib
import os
import importlib.util
from functools import lru_cache
from pathlib import Path
import subprocess
import threading
from ctypes import wintypes as wt

from .windows_job import ExtendedLimits, ProcessInfo, StartupInfoEx, WindowsJobProcess, kernel_api


class Coord(ctypes.Structure):
    _fields_ = [('x', ctypes.c_short), ('y', ctypes.c_short)]


class BootstrapOutput:
    """仅代答运行库启动握手；移除该查询，避免网页重复回复污染命令行。"""
    def __init__(self, stream, stdin):
        self.stream, self.stdin = stream, stdin
        self.pending = b''
        self.handshake = False

    def read(self, count):
        if count == 0:
            return b''
        while True:
            chunk = self.stream.read(count)
            if self.handshake:
                return chunk
            data = self.pending + chunk
            self.pending = b''
            if not chunk:
                return data
            query = b'\x1b[c'
            if query in data:
                self.handshake = True
                with contextlib.suppress(OSError, ValueError):
                    self.stdin.write(b'\x1b[?1;2c')
                return data.replace(query, b'', 1) or self.read(count)
            if not data:
                return b''
            # 查询可能跨内核读取边界；只暂存最多两个字节。
            for size in (2, 1):
                if data.endswith(query[:size]):
                    self.pending, data = data[-size:], data[:-size]
                    break
            if data:
                return data

    def close(self):
        self.stream.close()


@lru_cache(maxsize=1)
def conpty_api():
    """使用新版微软运行库，避开系统 ConPTY 的句柄泄漏和阻塞关闭。"""
    specification = importlib.util.find_spec('winpty')
    if specification is None or specification.origin is None:
        raise ImportError(tr('terminal.transport.install_required'))
    directory = Path(specification.origin).parent
    if not (directory / 'OpenConsole.exe').is_file():
        raise FileNotFoundError(tr('terminal.transport.host_missing'))
    library = ctypes.WinDLL(str(directory / 'conpty.dll'), use_last_error=True)
    for name, args, result in (
        ('CreatePseudoConsole', [Coord, wt.HANDLE, wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE)], ctypes.c_long),
        ('ResizePseudoConsole', [wt.HANDLE, Coord], ctypes.c_long),
        ('ClosePseudoConsole', [wt.HANDLE], None),
    ):
        function = getattr(library, 'Conpty' + name)
        function.argtypes, function.restype = args, result
    return library


class WindowsConPTYProcess(WindowsJobProcess):
    def __init__(self, argv: list[str], *, cwd: str, env: dict[str, str]):
        import msvcrt
        self.kernel = kernel_api()
        self.api = conpty_api()
        self.job = self.process = self.console = None
        self.stdin = self.stdout = self.stderr = None
        self._code = None
        self._closed = self._confirmed = False
        self._lock = threading.RLock()
        self._changed = threading.Condition(self._lock)
        self._console_lock = threading.Lock()
        handles = []
        initialized = False
        attributes = None
        try:
            self.job = self._check(self.kernel.CreateJobObjectW(None, None))
            limits = ExtendedLimits()
            limits.basic.flags = 0x2000
            self._check(self.kernel.SetInformationJobObject(self.job, 9, ctypes.byref(limits), ctypes.sizeof(limits)))
            def pipe():
                reader, writer = wt.HANDLE(), wt.HANDLE()
                self._check(self.kernel.CreatePipe(ctypes.byref(reader), ctypes.byref(writer), None, 65536))
                handles.extend([reader.value, writer.value])
                return reader.value, writer.value
            input_read, input_write = pipe()
            output_read, output_write = pipe()
            console = wt.HANDLE()
            self._hresult(self.api.ConptyCreatePseudoConsole(Coord(80, 24), input_read, output_write, 0, ctypes.byref(console)))
            self.console = console.value
            size = ctypes.c_size_t()
            self.kernel.InitializeProcThreadAttributeList(None, 2, 0, ctypes.byref(size))
            storage = ctypes.create_string_buffer(size.value)
            attributes = ctypes.cast(storage, wt.LPVOID)
            self._check(self.kernel.InitializeProcThreadAttributeList(attributes, 2, 0, ctypes.byref(size)))
            initialized = True
            jobs = (wt.HANDLE * 1)(self.job)
            self._check(self.kernel.UpdateProcThreadAttribute(attributes, 0, 0x2000D, jobs, ctypes.sizeof(jobs), None, None))
            # 伪终端属性直接传 HPCON 值；与 Job 句柄数组的指针契约不同。
            self._check(self.kernel.UpdateProcThreadAttribute(attributes, 0, 0x20016, self.console, ctypes.sizeof(wt.HANDLE), None, None))
            info = StartupInfoEx()
            info.base.size = ctypes.sizeof(info)
            # 父进程可能把标准流重定向到日志。显式使用空句柄，
            # 禁止 Windows 的标准流复制规则绕过新伪终端。
            info.base.flags = 0x100
            info.attributes = attributes
            environment = ctypes.create_unicode_buffer('\0'.join(f'{key}={value}' for key, value in sorted(env.items(), key=lambda pair: pair[0].upper())) + '\0\0')
            process = ProcessInfo()
            self._check(self.kernel.CreateProcessW(None, ctypes.create_unicode_buffer(subprocess.list2cmdline(argv)), None, None,
                        False, 0x80000 | 0x400, environment, cwd, ctypes.byref(info), ctypes.byref(process)))
            self.process, self.pid = process.process, process.pid
            self.kernel.CloseHandle(process.thread)
            for handle in (input_read, output_write):
                self.kernel.CloseHandle(handle)
                handles.remove(handle)
            def file(handle, mode, flag):
                fd = msvcrt.open_osfhandle(handle, flag | os.O_BINARY)
                handles.remove(handle)
                try:
                    return os.fdopen(fd, mode, buffering=0)
                except BaseException:
                    os.close(fd)
                    raise
            self.stdout = file(output_read, 'rb', os.O_RDONLY)
            self.stdin = file(input_write, 'wb', os.O_WRONLY)
            self.stdout = BootstrapOutput(self.stdout, self.stdin)
        except BaseException:
            for handle in handles:
                self.kernel.CloseHandle(handle)
            self.close()
            raise
        finally:
            if initialized:
                self.kernel.DeleteProcThreadAttributeList(attributes)

    @staticmethod
    def _hresult(value):
        if value < 0:
            # HRESULT 已按有符号 32 位返回；转为无符号数会让 FormatError 溢出，遮蔽原始错误。
            raise ctypes.WinError(value)

    def resize(self, cols: int, rows: int):
        with self._console_lock:
            if not self.console:
                raise BrokenPipeError(tr('terminal.transport.conpty_closed'))
            self._hresult(self.api.ConptyResizePseudoConsole(self.console, Coord(cols, rows)))

    def close_console(self):
        """输出读取必须并行排空，避免 ClosePseudoConsole 的最后一帧死锁。"""
        with self._console_lock:
            if self.console:
                if self.stdin:
                    self.stdin.close()
                self.api.ConptyClosePseudoConsole(self.console)
                self.console = None

    def close(self):
        # 强制清理路径先断开通信；正常关闭由适配器先并行排空最后画面。
        if self.stdin:
            self.stdin.close()
        super().close()
        self.close_console()
