"""临时夹具使用真实目录链接，Windows 普通权限下使用 NTFS 目录联接。"""
import os
from pathlib import Path


def directory_link(link: Path, target: Path) -> None:
    """不改系统权限、不复制目标，确保路径确实经过文件系统重解析。"""
    target = target.resolve(strict=True)
    if os.name == 'nt':
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
        if not link.is_junction():
            raise AssertionError('目录联接夹具未创建成功')
    else:
        link.symlink_to(target, target_is_directory=True)
    if link.resolve(strict=True) != target:
        raise AssertionError('目录链接夹具指向错误目标')
