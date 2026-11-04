"""macOS 原生进程观察；不启动提权命令，也不隐藏观察权限失败。"""
from __future__ import annotations

import ctypes
import errno
from functools import lru_cache
import os


class _RusageInfoV0(ctypes.Structure):
    """按系统 sys/resource.h 完整声明 RUSAGE_INFO_V0，避免越界写入。"""

    _fields_ = [('ri_uuid', ctypes.c_uint8 * 16), *[
        (name, ctypes.c_uint64) for name in (
            'ri_user_time', 'ri_system_time', 'ri_pkg_idle_wkups', 'ri_interrupt_wkups',
            'ri_pageins', 'ri_wired_size', 'ri_resident_size', 'ri_phys_footprint',
            'ri_proc_start_abstime', 'ri_proc_exit_abstime')]]


@lru_cache(maxsize=1)
def _libproc():
    """延迟加载系统库，其他平台导入模块不触发 macOS 调用。"""
    library = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)
    library.proc_pid_rusage.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
    library.proc_pid_rusage.restype = ctypes.c_int
    library.proc_listchildpids.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
    library.proc_listchildpids.restype = ctypes.c_int
    return library


def _check_pid(pid):
    """拒绝进程集合选择器和隐式类型转换。"""
    if type(pid) is not int or not 0 < pid <= 0x7fffffff:
        raise ValueError('pid must be a positive pid_t')


def _native_error(operation, pid):
    code = ctypes.get_errno() or errno.EIO
    return OSError(code, f'{operation}({pid}): {os.strerror(code)}')


def resident_memory_kib(pid: int) -> int:
    """读取当前驻留内存，按既有课程回执单位换算为 KiB。"""
    _check_pid(pid)
    library = _libproc()
    usage = _RusageInfoV0()
    ctypes.set_errno(0)
    if library.proc_pid_rusage(pid, 0, ctypes.byref(usage)) != 0:
        raise _native_error('proc_pid_rusage', pid)
    return usage.ri_resident_size // 1024


def _children(pid):
    """增长缓冲区直到完整，容量耗尽明确失败，不能静默漏掉后代。"""
    capacity = 256
    library = _libproc()
    while capacity <= 65536:
        buffer = (ctypes.c_int * capacity)()
        ctypes.set_errno(0)
        count = library.proc_listchildpids(pid, buffer, ctypes.sizeof(buffer))
        if count < 0 or (count == 0 and ctypes.get_errno()):
            raise _native_error('proc_listchildpids', pid)
        if count < capacity:
            return [child for child in buffer[:count] if child > 0]
        capacity *= 2
    raise OSError(errno.EOVERFLOW, f'proc_listchildpids({pid})')


def descendant_pids(pid: int) -> list[int]:
    """父进程存活时记录后代，返回父先子后顺序供调用方反向收尾。"""
    _check_pid(pid)
    descendants, pending, seen = [], [pid], {pid}
    while pending:
        parent = pending.pop()
        try:
            children = _children(parent)
        except ProcessLookupError:
            # 枚举期间退出的进程已经消失；权限错误不能作为空树。
            continue
        for child in children:
            if child not in seen:
                seen.add(child)
                descendants.append(child)
                pending.append(child)
    return descendants
