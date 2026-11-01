"""仅供离线评估的Windows进程内存探针，不增加产品依赖。"""
from __future__ import annotations

import ctypes
import sys
from functools import lru_cache


@lru_cache(maxsize=1)
def _windows_reader():
    """严格映射微软PROCESS_MEMORY_COUNTERS_EX，原生失败保持可见。"""
    class Counters(ctypes.Structure):
        _fields_ = [('cb', ctypes.c_uint32), ('PageFaultCount', ctypes.c_uint32)] + [
            (name, ctypes.c_size_t) for name in (
                'PeakWorkingSetSize', 'WorkingSetSize', 'QuotaPeakPagedPoolUsage', 'QuotaPagedPoolUsage',
                'QuotaPeakNonPagedPoolUsage', 'QuotaNonPagedPoolUsage', 'PagefileUsage',
                'PeakPagefileUsage', 'PrivateUsage')]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    psapi = ctypes.WinDLL('psapi', use_last_error=True)
    kernel.GetCurrentProcess.argtypes = []
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_uint32]
    psapi.GetProcessMemoryInfo.restype = ctypes.c_int

    def read():
        value = Counters()
        value.cb = ctypes.sizeof(value)
        if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(value), value.cb):
            raise ctypes.WinError(ctypes.get_last_error())
        return {'status': 'available', 'provider': 'windows-GetProcessMemoryInfo',
                'working_set_bytes': value.WorkingSetSize, 'private_committed_bytes': value.PrivateUsage,
                'lifetime_peak_working_set_bytes': value.PeakWorkingSetSize,
                'lifetime_peak_private_committed_bytes': value.PeakPagefileUsage}
    return read


def process_memory():
    """快照含整个进程，生命周期峰值不能冒充某一个操作的新增峰值。"""
    if sys.platform != 'win32':
        return {'status': 'unavailable', 'reason': 'probe-supports-windows-only'}
    return _windows_reader()()
