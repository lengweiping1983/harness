"""Windows 命令所属组：创建时原子关联 Job Object，不按可复用 PID 清理。"""
from __future__ import annotations

from harness.i18n import tr

import ctypes
import os
import subprocess
import threading
import time
from ctypes import wintypes as wt

from .processes import ProcessCleanupError
from .windows_shell import windows_shell_launch


class SecurityAttributes(ctypes.Structure):
    _fields_ = [('length', wt.DWORD), ('descriptor', wt.LPVOID), ('inherit', wt.BOOL)]


class StartupInfo(ctypes.Structure):
    _fields_ = [('size', wt.DWORD), ('reserved', wt.LPWSTR), ('desktop', wt.LPWSTR),
                ('title', wt.LPWSTR), ('x', wt.DWORD), ('y', wt.DWORD),
                ('width', wt.DWORD), ('height', wt.DWORD), ('xchars', wt.DWORD),
                ('ychars', wt.DWORD), ('fill', wt.DWORD), ('flags', wt.DWORD),
                ('show', wt.WORD), ('reserved_size', wt.WORD), ('reserved_data', wt.LPVOID),
                ('stdin', wt.HANDLE), ('stdout', wt.HANDLE), ('stderr', wt.HANDLE)]


class StartupInfoEx(ctypes.Structure):
    _fields_ = [('base', StartupInfo), ('attributes', wt.LPVOID)]


class ProcessInfo(ctypes.Structure):
    _fields_ = [('process', wt.HANDLE), ('thread', wt.HANDLE), ('pid', wt.DWORD), ('tid', wt.DWORD)]


class BasicLimits(ctypes.Structure):
    _fields_ = [('process_time', ctypes.c_longlong), ('job_time', ctypes.c_longlong),
                ('flags', wt.DWORD), ('min_working_set', ctypes.c_size_t),
                ('max_working_set', ctypes.c_size_t), ('process_limit', wt.DWORD),
                ('affinity', ctypes.c_size_t), ('priority', wt.DWORD), ('scheduling', wt.DWORD)]


class ExtendedLimits(ctypes.Structure):
    _fields_ = [('basic', BasicLimits), ('io', ctypes.c_ulonglong * 6),
                ('process_memory', ctypes.c_size_t), ('job_memory', ctypes.c_size_t),
                ('peak_process_memory', ctypes.c_size_t), ('peak_job_memory', ctypes.c_size_t)]


class Accounting(ctypes.Structure):
    _fields_ = [('times', ctypes.c_longlong * 4), ('page_faults', wt.DWORD),
                ('total', wt.DWORD), ('active', wt.DWORD), ('terminated', wt.DWORD)]


def kernel_api():
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    signatures = {
        'CreateJobObjectW': ([wt.LPVOID, wt.LPCWSTR], wt.HANDLE),
        'SetInformationJobObject': ([wt.HANDLE, ctypes.c_int, wt.LPVOID, wt.DWORD], wt.BOOL),
        'QueryInformationJobObject': ([wt.HANDLE, ctypes.c_int, wt.LPVOID, wt.DWORD, wt.LPVOID], wt.BOOL),
        'TerminateJobObject': ([wt.HANDLE, wt.UINT], wt.BOOL),
        'CreatePipe': ([ctypes.POINTER(wt.HANDLE), ctypes.POINTER(wt.HANDLE), wt.LPVOID, wt.DWORD], wt.BOOL),
        'SetHandleInformation': ([wt.HANDLE, wt.DWORD, wt.DWORD], wt.BOOL),
        'InitializeProcThreadAttributeList': ([wt.LPVOID, wt.DWORD, wt.DWORD, ctypes.POINTER(ctypes.c_size_t)], wt.BOOL),
        'UpdateProcThreadAttribute': ([wt.LPVOID, wt.DWORD, ctypes.c_size_t, wt.LPVOID, ctypes.c_size_t, wt.LPVOID, wt.LPVOID], wt.BOOL),
        'DeleteProcThreadAttributeList': ([wt.LPVOID], None),
        'CreateProcessW': ([wt.LPCWSTR, wt.LPWSTR, wt.LPVOID, wt.LPVOID, wt.BOOL, wt.DWORD,
                            wt.LPVOID, wt.LPCWSTR, wt.LPVOID, ctypes.POINTER(ProcessInfo)], wt.BOOL),
        'WaitForSingleObject': ([wt.HANDLE, wt.DWORD], wt.DWORD),
        'GetExitCodeProcess': ([wt.HANDLE, ctypes.POINTER(wt.DWORD)], wt.BOOL),
        'CloseHandle': ([wt.HANDLE], wt.BOOL),
    }
    for name, (args, result) in signatures.items():
        function = getattr(kernel, name)
        function.argtypes, function.restype = args, result
    return kernel


class WindowsJobProcess:
    """二进制管道进程；整个 Job 为空才确认结束，根进程退出不释放归属。"""
    def __init__(self, argv: list[str], *, cwd: str, env: dict[str, str], shell: bool = False,
                 stdin: bool = False):
        import msvcrt
        self.kernel = kernel_api()
        self.job = self.process = None
        self.stdin = self.stdout = self.stderr = None
        self._code = None
        self._closed = False
        self._confirmed = False
        self._lock = threading.RLock()
        self._changed = threading.Condition(self._lock)
        handles: list[int] = []
        attribute_list = None
        attributes_initialized = False
        try:
            self.job = self.kernel.CreateJobObjectW(None, None)
            self._check(self.job)
            limits = ExtendedLimits()
            limits.basic.flags = 0x2000  # 关闭任务对象时终止进程：JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            self._check(self.kernel.SetInformationJobObject(self.job, 9, ctypes.byref(limits), ctypes.sizeof(limits)))
            security = SecurityAttributes(ctypes.sizeof(SecurityAttributes), None, True)
            def pipe():
                reader, writer = wt.HANDLE(), wt.HANDLE()
                self._check(self.kernel.CreatePipe(ctypes.byref(reader), ctypes.byref(writer), ctypes.byref(security), 0))
                handles.extend([reader.value, writer.value])
                return reader.value, writer.value
            out_read, out_write = pipe()
            err_read, err_write = pipe()
            in_read, in_write = pipe()
            for parent in (out_read, err_read, in_write):
                self._check(self.kernel.SetHandleInformation(parent, 1, 0))
            size = ctypes.c_size_t()
            self.kernel.InitializeProcThreadAttributeList(None, 2, 0, ctypes.byref(size))
            storage = ctypes.create_string_buffer(size.value)
            attribute_list = ctypes.cast(storage, wt.LPVOID)
            self._check(self.kernel.InitializeProcThreadAttributeList(attribute_list, 2, 0, ctypes.byref(size)))
            attributes_initialized = True
            std_handles = (wt.HANDLE * 3)(in_read, out_write, err_write)
            job_handles = (wt.HANDLE * 1)(self.job)
            self._check(self.kernel.UpdateProcThreadAttribute(attribute_list, 0, 0x20002,
                        std_handles, ctypes.sizeof(std_handles), None, None))
            self._check(self.kernel.UpdateProcThreadAttribute(attribute_list, 0, 0x2000D,
                        job_handles, ctypes.sizeof(job_handles), None, None))
            info = StartupInfoEx()
            info.base.size = ctypes.sizeof(info)
            info.base.flags = 0x100 | 1  # 显式标准输入输出，隐藏窗口
            info.base.stdin, info.base.stdout, info.base.stderr = in_read, out_write, err_write
            info.attributes = attribute_list
            if shell:
                executable, command = windows_shell_launch(argv[0], env)
            else:
                executable = None
                command = subprocess.list2cmdline(argv)
            environment = ctypes.create_unicode_buffer('\0'.join(f'{key}={value}' for key, value in sorted(env.items(), key=lambda pair: pair[0].upper())) + '\0\0')
            process_info = ProcessInfo()
            self._check(self.kernel.CreateProcessW(executable, ctypes.create_unicode_buffer(command), None, None,
                        True, 0x08000000 | 0x80000 | 0x400, environment, cwd, ctypes.byref(info), ctypes.byref(process_info)))
            self.process, self.pid = process_info.process, process_info.pid
            self.kernel.CloseHandle(process_info.thread)
            for child in (in_read, out_write, err_write):
                self.kernel.CloseHandle(child)
                handles.remove(child)
            def file(handle, *, writable=False):
                fd = msvcrt.open_osfhandle(handle, (os.O_WRONLY if writable else os.O_RDONLY) | os.O_BINARY)
                handles.remove(handle)
                try:
                    return os.fdopen(fd, 'wb' if writable else 'rb', buffering=0)
                except BaseException:
                    os.close(fd)
                    raise
            self.stdout, self.stderr = file(out_read), file(err_read)
            if stdin:
                self.stdin = file(in_write, writable=True)
            else:
                # 普通命令没有输入；显式读取目录时才保留匿名管道写端。
                self.kernel.CloseHandle(in_write)
                handles.remove(in_write)
        except BaseException:
            for handle in handles:
                self.kernel.CloseHandle(handle)
            self.close()
            raise
        finally:
            if attributes_initialized:
                self.kernel.DeleteProcThreadAttributeList(attribute_list)

    def _check(self, result):
        if not result:
            raise ctypes.WinError(ctypes.get_last_error())
        return result

    def active_count(self) -> int:
        with self._lock:
            if self._closed:
                if not self._confirmed:
                    raise ProcessCleanupError(tr('python.windows_job.9ce0b12030a0'), pid=self.pid)
                return 0
            accounting = Accounting()
            self._check(self.kernel.QueryInformationJobObject(self.job, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None))
            return accounting.active

    def root_code(self) -> int | None:
        with self._lock:
            if self._code is not None:
                return self._code
            if not self.process or self._closed:
                return None
            status = self.kernel.WaitForSingleObject(self.process, 0)
            if status == 258:
                return None
            if status != 0:
                raise ProcessCleanupError(tr('python.windows_job.d7e6fbded730'), wait_status=status)
            code = wt.DWORD()
            self._check(self.kernel.GetExitCodeProcess(self.process, ctypes.byref(code)))
            self._code = code.value
            return self._code

    def wait(self, timeout: float | None = None) -> int:
        deadline = time.monotonic() + timeout if timeout is not None else None
        interval = .02
        while True:
            with self._lock:
                # Job 计数可能先归零，根进程句柄稍后才变为 signaled；两者都要确认。
                if self.active_count() == 0:
                    code = self.root_code()
                    if code is not None:
                        self._confirmed = True
                        self._changed.notify_all()
                        return code
                # 从 20ms 退避到 250ms；条件等待释放锁，停止操作可立即唤醒观察者。
                remaining = deadline - time.monotonic() if deadline is not None else None
                if remaining is not None and remaining <= 0:
                    raise ProcessCleanupError(tr('python.windows_job.e27e67f232ba'), pid=self.pid)
                self._changed.wait(interval if remaining is None else min(interval, remaining))
                interval = min(interval * 2, .25)

    def stop(self, timeout: float = 5) -> None:
        with self._lock:
            if self.active_count():
                self._check(self.kernel.TerminateJobObject(self.job, 1))
            self._changed.notify_all()
        self.wait(timeout)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            if self.job and self.process:
                try:
                    self._confirmed = self.active_count() == 0 and self.root_code() is not None
                except (OSError, ProcessCleanupError):
                    self._confirmed = False
            if self.job:
                # 先清理进程，再关闭管道，避免等待仍持有输出管道的子进程。
                self.kernel.CloseHandle(self.job)
            if self.process:
                self.kernel.CloseHandle(self.process)
            for stream in (self.stdin, self.stdout, self.stderr):
                if stream is not None:
                    stream.close()
            self._closed = True
