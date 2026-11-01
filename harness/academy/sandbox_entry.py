"""在操作系统沙箱内设置资源上限，再运行课程 Python 文件。"""
from __future__ import annotations

import os
import resource
import runpy
import sys


if __name__ == '__main__':
    root, script = sys.argv[1:3]
    arguments = sys.argv[3:]
    resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
    resource.setrlimit(resource.RLIMIT_FSIZE, (8 * 1024 * 1024, 8 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    # Linux 使用地址空间硬上限；macOS 由宿主监视进程组实际内存。
    if sys.platform != 'darwin':
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    if hasattr(resource, 'RLIMIT_NPROC'):
        resource.setrlimit(resource.RLIMIT_NPROC, (32, 32))
    os.chdir(root)
    sys.path.insert(0, root)
    sys.argv = [script, *arguments]
    runpy.run_path(script, run_name='__main__')
