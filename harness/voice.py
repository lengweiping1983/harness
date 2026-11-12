"""显式启动 Hex 原生听写应用，为任意前台应用提供全局快捷键。"""
from __future__ import annotations

import os
import platform
import subprocess
import sys
import threading

from .i18n import tr
from .tools.builtin.hex_tools import HexToolError, hex_binary_path, hex_cpu_supported, require_hex_cpu


_launch_lock = threading.Lock()
_app_process: subprocess.Popen | None = None


def dictation_capabilities() -> dict:
    """只检查本机安装，不启动应用、下载模型或推断系统授权。"""
    system = {'darwin': 'macos', 'win32': 'windows'}.get(sys.platform, 'unsupported')
    download = None
    if system == 'macos':
        if platform.machine().lower() in {'arm64', 'aarch64'}:
            download = 'https://anoma.ly/hex/download/macos-arm64-dmg'
    elif system == 'windows':
        arch = 'arm64' if platform.machine().lower() in {'arm64', 'aarch64'} else 'x64'
        download = f'https://anoma.ly/hex/download/windows-{arch}-setup'
    reason = None
    try:
        cpu_supported = hex_cpu_supported()
    except HexToolError as error:
        cpu_supported = False
        reason = error.copy
    if not cpu_supported:
        reason = reason or {'key': 'ui.voice.global.cpuUnsupported'}
    elif system == 'unsupported':
        reason = {'key': 'ui.voice.dictation.unsupported'}
    else:
        try:
            binary = hex_binary_path()
            if not binary.is_file():
                reason = {'key': 'ui.voice.dictation.missing'}
        except HexToolError as error:
            reason = error.copy
            if reason is None:
                raise
    return {'available': reason is None, 'cpu_supported': cpu_supported, 'platform': system,
            'download_url': download, 'reason': reason}


def open_dictation() -> None:
    """显式打开独立原生应用；接受打开请求不代表模型及权限已就绪。"""
    global _app_process
    with _launch_lock:
        require_hex_cpu()
        binary = hex_binary_path()
        if not binary.is_file():
            raise HexToolError(tr('ui.voice.dictation.missing'), copy={'key': 'ui.voice.dictation.missing'})
        try:
            bundle = binary.parents[2]
            if sys.platform == 'darwin' and bundle.suffix == '.app':
                # LaunchServices 保留签名应用身份，重复打开只唤起原窗口。
                command = ['/usr/bin/open']
                for name, value in sorted(os.environ.items()):
                    if name.startswith('HEX_'):
                        command.extend(['--env', f'{name}={value}'])
                command.append(str(bundle))
                result = subprocess.run(command, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.DEVNULL, timeout=10)
                if result.returncode != 0:
                    raise OSError(f'open: {result.returncode}')
            elif sys.platform == 'win32':
                # ShellExecute 与 Hex 原生单实例入口共同负责唤起已有窗口。
                os.startfile(str(binary), arguments='app')
            else:
                if _app_process is not None and _app_process.poll() is None:
                    return
                # 源码二进制没有应用包；独立会话不随 Harness 退出，等待线程负责回收退出码。
                process = subprocess.Popen([str(binary), 'app'], stdin=subprocess.DEVNULL,
                                           start_new_session=True)
                _app_process = process
                threading.Thread(target=process.wait, name='harness-voice-app', daemon=True).start()
        except subprocess.TimeoutExpired as error:
            raise HexToolError(tr('ui.voice.dictation.unconfirmed'),
                               copy={'key': 'ui.voice.dictation.unconfirmed'}) from error
        except RuntimeError as error:
            # 等待线程启动失败时，原生进程可能已接受启动，保留句柄且不重放。
            raise HexToolError(tr('ui.voice.dictation.unconfirmed'),
                               copy={'key': 'ui.voice.dictation.unconfirmed'}) from error
        except OSError as error:
            raise HexToolError(tr('ui.voice.dictation.launchFailed'),
                               copy={'key': 'ui.voice.dictation.launchFailed'}) from error


def main() -> int:
    """无参数打开原生应用；其他参数原样交给 Hex 公共命令树。"""
    if not sys.argv[1:] and sys.platform in {'darwin', 'win32'}:
        try:
            open_dictation()
        except HexToolError as error:
            print(str(error), file=sys.stderr)
            return 2
        return 0
    try:
        require_hex_cpu()
        binary = hex_binary_path()
    except HexToolError as error:
        print(str(error), file=sys.stderr)
        return 2
    if not binary.is_file():
        print(tr('runtime.voice.missing', path=str(binary)), file=sys.stderr)
        return 2
    # 全局监听、系统权限、快捷键配置和粘贴均由 Hex 自有原生实现负责。
    try:
        os.execv(str(binary), [str(binary), *(sys.argv[1:] or ['app'])])
    except OSError as error:
        print(tr('runtime.voice.launchFailed', detail=str(error)), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
