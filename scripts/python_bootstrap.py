"""在解释器启动边界设置 UTF-8 默认，不修改 SDK 宿主。"""
from __future__ import annotations

import os
import subprocess
import sys


def configure_utf8_process() -> None:
    """仅供独立脚本入口调用；原参数保持，覆盖显式的旧 UTF-8 开关。"""
    if not sys.flags.utf8_mode:
        original = sys.orig_argv[1:]
        arguments = []
        index = 0
        while index < len(original):
            value = original[index]
            # 执行目标之后的内容全部属于应用，不能当作解释器开关删除。
            if value in ('-', '--') or not value.startswith('-') or value.startswith(('-c', '-m')):
                arguments.extend(original[index:])
                break
            if value in ('-X', '-W') and index + 1 < len(original):
                if value != '-X' or original[index + 1].split('=')[0] != 'utf8':
                    arguments.extend(original[index:index + 2])
                index += 2
                continue
            if value.startswith('-X') and value[2:].split('=')[0] == 'utf8':
                index += 1
                continue
            arguments.append(value)
            index += 1
        raise SystemExit(subprocess.call([sys.executable, '-X', 'utf8', *arguments],
                         env={**os.environ, 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'}))
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='strict')
