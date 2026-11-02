"""显式启动 Hex 原生听写应用，为任意前台应用提供全局快捷键。"""
from __future__ import annotations

import os
import sys

from .i18n import tr
from .tools.builtin.hex_tools import HexToolError, hex_binary_path


def main() -> int:
    """无参数进入原生设置；其他参数原样交给 Hex 公共命令树。"""
    try:
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
