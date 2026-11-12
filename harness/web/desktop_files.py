"""通过宿主桌面打开已验证的工作区文件，不使用 Shell。"""
from __future__ import annotations

from harness.i18n import tr

import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading

from .errors import BadRequest, ServiceUnavailable


def open_desktop_file(path: Path, action: str) -> None:
    if action not in {'default', 'terminal', 'folder'}:
        raise BadRequest(tr('python.desktop_files.a4b8a179dcf3'), code='file/invalid-action')
    parent = str(path.parent)
    argv: list[str]
    if sys.platform == 'darwin':
        argv = ['/usr/bin/open', '-a', 'Terminal', parent] if action == 'terminal' else (
            ['/usr/bin/open', '-R', str(path)] if action == 'folder' else ['/usr/bin/open', str(path)])
    elif sys.platform == 'win32':
        if action == 'default':
            try:
                os.startfile(str(path))
            except OSError as exc:
                raise ServiceUnavailable(tr('python.desktop_files.2a9f8c4ac3d8'), code='file/open-failed') from exc
            return
        terminal = shutil.which('wt.exe') if action == 'terminal' else None
        argv = ['explorer.exe', '/select,', str(path)] if action == 'folder' else (
            [terminal, '-d', parent] if terminal else ['powershell.exe', '-NoLogo', '-NoExit'])
    else:
        if action == 'terminal':
            candidates = [('gnome-terminal', ['--working-directory', parent]), ('konsole', ['--workdir', parent]), ('x-terminal-emulator', []), ('xterm', [])]
            selected = next(((binary, args) for name, args in candidates if (binary := shutil.which(name))), None)
            if not selected:
                raise ServiceUnavailable(tr('python.desktop_files.8bce13671727'), code='file/terminal-unavailable')
            argv = [selected[0], *selected[1]]
        else:
            binary = shutil.which('xdg-open')
            if not binary:
                raise ServiceUnavailable(tr('python.desktop_files.874155482171'), code='file/desktop-unavailable')
            argv = [binary, parent if action == 'folder' else str(path)]
    try:
        options = {'creationflags': subprocess.CREATE_NEW_CONSOLE} if sys.platform == 'win32' and argv[0] == 'powershell.exe' else {}
        process = subprocess.Popen(argv, cwd=parent, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, **options,
                                   **({'start_new_session': True} if sys.platform != 'win32' else {}))
        try:
            code = process.wait(timeout=.3)
        except subprocess.TimeoutExpired:
            # 部分桌面启动器会与应用一起持续运行，稍后回收。
            threading.Thread(target=process.wait, daemon=True).start()
            return
        if code:
            raise ServiceUnavailable(tr('python.desktop_files.77c9f8194844'), code='file/open-failed')
    except OSError as exc:
        raise ServiceUnavailable(tr('python.desktop_files.9730edbf16ae'), code='file/open-failed') from exc
