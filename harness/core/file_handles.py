"""Windows文件资源读取以实际文件句柄的最终路径确认边界。"""
from __future__ import annotations

import ntpath
import os


def windows_path_within(filename: str, root: str) -> bool:
    """统一Win32扩展路径与UNC表示后按路径段判断，不能按字符串前缀。"""
    def normal(value):
        if value[:8].lower() == '\\\\?\\unc\\':
            value = '\\\\' + value[8:]
        elif value.startswith('\\\\?\\'):
            value = value[4:]
        return ntpath.normcase(ntpath.normpath(value))
    filename, root = normal(filename), normal(root)
    try:
        return ntpath.commonpath((filename, root)) == root
    except ValueError:
        return False


def windows_handle_path(descriptor: int) -> str:
    """使用系统句柄查询，不重新解析可能已被替换的文件名。"""
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    get_path = kernel.GetFinalPathNameByHandleW
    get_path.argtypes = (wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD)
    get_path.restype = wintypes.DWORD
    handle = msvcrt.get_osfhandle(descriptor)
    size = 512
    while True:
        buffer = ctypes.create_unicode_buffer(size)
        length = get_path(handle, buffer, size, 0)
        if not length:
            raise ctypes.WinError(ctypes.get_last_error())
        if length < size:
            return buffer.value
        size = length + 1
        if size > 32768:
            raise OSError('resource path exceeds system limit')


def check_handle_root(descriptor: int, root) -> None:
    """非Windows由目录句柄限定来源，Windows逐次确认真实句柄路径。"""
    if os.name == 'nt' and not windows_path_within(windows_handle_path(descriptor), str(root)):
        raise OSError('resource outside root')
