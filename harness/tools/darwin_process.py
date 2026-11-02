"""macOS 所属进程组：原生状态查询与未回收组长共同固定执行归属。"""
from __future__ import annotations

import ctypes
import errno
import os

from harness.i18n import tr
from .owned_process import PosixGroupProcess
from .processes import ProcessCleanupError


class _SigInfo(ctypes.Structure):
    """对应 macOS SDK siginfo_t，供直接调用系统 waitid 使用。

    不依赖具体 Python 构建是否暴露 waitid/WNOWAIT；返回字段仍按 SDK 布局读取。
    """

    _fields_ = [(name, ctypes.c_int) for name in ('signo', 'error', 'code', 'pid')] + [
        ('uid', ctypes.c_uint32), ('status', ctypes.c_int),
        ('address', ctypes.c_void_p), ('value', ctypes.c_void_p), ('band', ctypes.c_long),
        ('reserved', ctypes.c_ulong * 7),
    ]


class DarwinGroupProcess(PosixGroupProcess):
    """检查进程组成员；只在活动成员全部退出后回收组长。"""

    def __init__(self, argv, *, cwd, env, shell=False, stdin=False):
        try:
            self._lib = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)
            self._lib.proc_listpids.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_int]
            self._lib.proc_listpids.restype = ctypes.c_int
            self._system = ctypes.CDLL(None, use_errno=True)
            self._system.waitid.argtypes = [ctypes.c_int, ctypes.c_uint32, ctypes.POINTER(_SigInfo), ctypes.c_int]
            self._system.waitid.restype = ctypes.c_int
        except (OSError, AttributeError) as exc:
            raise ProcessCleanupError(tr('process.darwin.unavailable')) from exc
        # 在创建任何子进程之前确认原生查询可用。
        ctypes.set_errno(0)
        if self._lib.proc_listpids(2, os.getpgrp(), None, 0) <= 0:
            raise ProcessCleanupError(tr('process.darwin.unavailable'), native_errno=ctypes.get_errno())
        super().__init__(argv, cwd=cwd, env=env, shell=shell, stdin=stdin)

    def root_code(self):
        with self._lock:
            if self._code is not None:
                return self._code
            info = _SigInfo()
            while True:
                ctypes.set_errno(0)
                # SDK 常量：P_PID=1，WEXITED=4，WNOHANG=1，WNOWAIT=32。
                result = self._system.waitid(1, self.pid, ctypes.byref(info), 4 | 1 | 32)
                error = ctypes.get_errno()
                if result == 0:
                    break
                if error != errno.EINTR:
                    raise ProcessCleanupError(tr('process.darwin.inspect_failed'), pid=self.pid, native_errno=error)
            if info.pid:
                if info.pid != self.pid or info.code not in {1, 2, 3}:
                    raise ProcessCleanupError(tr('process.darwin.inspect_failed'), pid=self.pid)
                # CLD_EXITED=1；CLD_KILLED=2、CLD_DUMPED=3 的 status 为信号号。
                self._code = info.status if info.code == 1 else -info.status
            return self._code

    def _members(self):
        capacity = 64
        while True:
            buffer = (ctypes.c_int * capacity)()
            ctypes.set_errno(0)
            # PROC_PGRP_ONLY=2；返回字节数，不是 PID 数量。
            size = self._lib.proc_listpids(2, self.pid, buffer, ctypes.sizeof(buffer))
            if size <= 0 or size > ctypes.sizeof(buffer) or size % ctypes.sizeof(ctypes.c_int):
                raise ProcessCleanupError(tr('process.darwin.inspect_failed'), pid=self.pid,
                                          native_errno=ctypes.get_errno())
            if size < ctypes.sizeof(buffer):
                members = set(buffer[:size // ctypes.sizeof(ctypes.c_int)])
                if self.pid not in members:
                    raise ProcessCleanupError(tr('process.darwin.root_missing'), pid=self.pid)
                return members
            capacity *= 2

    def _scan_count(self):
        if self._closed or self._finished:
            return 0
        root_active = self.root_code() is None
        # libproc 的一次内核快照同时包含活动成员和僵尸。保守地等待
        # 所有组内后代被回收，避免逐 PID 查询时漏掉刚派生又退出的父进程。
        # 组长由本对象固定为未回收状态，集合只剩组长才可确认清理。
        return len(self._members()) - 1 + int(root_active)
