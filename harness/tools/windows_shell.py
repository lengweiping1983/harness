"""统一 Windows cmd 的启动与 UTF-8 默认，不让命令正文经过额外 Shell 解析。"""
from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Mapping


def windows_shell_command(command: str, env: Mapping[str, str]) -> tuple[str, str]:
    """只构造实际 cmd 的原生命令行，保留原有引号、操作符和退出码。"""
    executable = env.get('COMSPEC') or os.path.join(os.environ['SystemRoot'], 'System32', 'cmd.exe')
    return executable, f'{subprocess.list2cmdline([executable])} /d /s /c "{command}"'


# cmd 在启动时缓存管道输出代码页，同一个 cmd 中先 chcp 再 dir 仍可能输出旧编码。
# 初始化由标准库桥接进程执行，成功后才启动实际 cmd，用户正文只被 cmd 解析一次。
WINDOWS_UTF8_SETUP = """if sys.platform == 'win32':
    subprocess.run([os.path.join(os.environ['SystemRoot'], 'System32', 'chcp.com'), '65001'],
                   stdout=subprocess.DEVNULL, check=True)
"""
WINDOWS_SHELL_BRIDGE = 'import os,subprocess,sys\n' + WINDOWS_UTF8_SETUP + """
sys.exit(subprocess.call(sys.argv[2], executable=sys.argv[1], shell=False))
"""


def windows_shell_launch(command: str, env: Mapping[str, str]) -> tuple[str, str]:
    """将原生命令行作为桥接参数传递，CRT 转义只用于 Python 参数向量。"""
    executable, native = windows_shell_command(command, env)
    argv = [sys.executable, '-I', '-X', 'utf8', '-c', WINDOWS_SHELL_BRIDGE, executable, native]
    return sys.executable, subprocess.list2cmdline(argv)


WINDOWS_CONSOLE_BRIDGE = 'import json,os,subprocess,sys\n' + WINDOWS_UTF8_SETUP + """
sys.exit(subprocess.call(json.loads(sys.argv[1]), shell=False))
"""


def windows_console_argv(argv: list[str]) -> list[str]:
    """终端管道回退也在私有控制台初始化后启动实际 Shell，不拼接终端输入。"""
    import json
    return [sys.executable, '-I', '-X', 'utf8', '-c', WINDOWS_CONSOLE_BRIDGE, json.dumps(argv)]
