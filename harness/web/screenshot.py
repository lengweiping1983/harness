"""显式的本地 macOS 区域捕获，窗口恢复等待有上限。"""
from __future__ import annotations

from harness.i18n import tr

from pathlib import Path
import ctypes
import re
import subprocess
import sys
import tempfile
import threading
import time

from .attachments import MAX_ATTACHMENT_BYTES
from .errors import BadRequest, Conflict, PayloadTooLarge, ServiceUnavailable

_capture_lock = threading.Lock()
_TOKEN = re.compile(r'^harness-capture-[0-9a-f]{32}$')

# 将随机标识作为参数传入，不将页面控制的文本插入 AppleScript。
_FIND_WINDOW = '''on run argv
    tell application "System Events"
        set owner to first application process whose frontmost is true
        tell owner
            if not (exists window 1) then error "No browser window"
            if name of window 1 does not contain item 1 of argv then error "Window changed"
        end tell
        return unix id of owner
    end tell
end run'''

_SET_MINIMIZED = '''on run argv
    tell application "System Events"
        set owner to first application process whose unix id is (item 1 of argv as integer)
        tell owner
            set targetWindow to first window whose name contains (item 2 of argv)
            set value of attribute "AXMinimized" of targetWindow to (item 3 of argv is "true")
            if (value of attribute "AXMinimized" of targetWindow) is not (item 3 of argv is "true") then error "Window did not change minimized state"
            if item 3 of argv is "false" then set frontmost to true
        end tell
    end tell
end run'''


def native_available() -> bool:
    return sys.platform == 'darwin' and Path('/usr/sbin/screencapture').is_file()


def _script(source: str, *args: str) -> str:
    result = subprocess.run(['/usr/bin/osascript', '-e', source, *args],
                            capture_output=True, text=True, timeout=8, check=True, encoding='utf-8')
    return result.stdout.strip()


def _open_permission_settings(pane: str, code: str) -> None:
    """仅从显式的本机功能入口打开固定系统设置，不重放捕获。"""
    try:
        subprocess.run(['/usr/bin/open', f'x-apple.systempreferences:com.apple.preference.security?Privacy_{pane}'],
                       capture_output=True, timeout=8, check=True)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ServiceUnavailable(tr('ui.collaboration.error.screenshot/permission-settings-failed'),
                                 code='screenshot/permission-settings-failed') from exc
    raise ServiceUnavailable(tr('ui.collaboration.error.' + code), status=403, code=code)


def _ensure_capture_permissions(hide_window: bool) -> None:
    """本机网页捕获同样先申请权限，系统拒绝后直接打开对应设置。"""
    graphics = ctypes.CDLL('/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics')
    preflight = graphics.CGPreflightScreenCaptureAccess
    request = graphics.CGRequestScreenCaptureAccess
    for function in (preflight, request):
        function.argtypes = []
        function.restype = ctypes.c_bool
    if not preflight() and not request():
        _open_permission_settings('ScreenCapture', 'screenshot/permission-required')
    if hide_window:
        services = ctypes.CDLL('/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices')
        trusted = services.AXIsProcessTrusted
        trusted.argtypes = []
        trusted.restype = ctypes.c_bool
        if not trusted():
            _open_permission_settings('Accessibility', 'screenshot/accessibility-required')


def capture_region(*, hide_window: bool = False, window_token: str = '') -> bytes | None:
    """返回 PNG，按 Escape 时返回 None，不保持屏幕共享流。"""
    if not native_available():
        raise ServiceUnavailable(tr('python.screenshot.1925b6fe860f'),
                                 code='screenshot/unsupported')
    if hide_window and not _TOKEN.fullmatch(window_token):
        raise BadRequest(tr('python.screenshot.a24c09f00d67'), code='screenshot/invalid-window')
    if not _capture_lock.acquire(blocking=False):
        raise Conflict(tr('python.screenshot.e60984f813cf'), code='screenshot/busy')
    owner = None
    try:
        _ensure_capture_permissions(hide_window)
        with tempfile.TemporaryDirectory(prefix='harness-screenshot-') as directory:
            target = Path(directory) / 'capture.png'
            primary = None
            try:
                if hide_window:
                    try:
                        owner = _script(_FIND_WINDOW, window_token)
                        if not owner.isdecimal():
                            raise ValueError(tr('runtime.validation.c9971ceecac8'))
                        _script(_SET_MINIMIZED, owner, window_token, 'true')
                        # 开始选择前，等待 Dock 动画结束。
                        time.sleep(.35)
                    except (OSError, ValueError, subprocess.SubprocessError) as exc:
                        raise ServiceUnavailable(
                            tr('python.screenshot.e06eede818ac'),
                            code='screenshot/hide-failed') from exc
                try:
                    result = subprocess.run(['/usr/sbin/screencapture', '-i', '-s', '-x', '-t', 'png', str(target)],
                                            capture_output=True, timeout=120)
                except subprocess.TimeoutExpired as exc:
                    raise ServiceUnavailable(tr('python.screenshot.ecf9ecb00d45'), code='screenshot/timeout') from exc
                except OSError as exc:
                    raise ServiceUnavailable(tr('python.screenshot.768ab8087462'), code='screenshot/start-failed') from exc
                if not target.is_file():
                    if result.stderr.strip():
                        raise ServiceUnavailable(tr('python.screenshot.1a03fef98c10'),
                                                 code='screenshot/capture-failed')
                    return None
                if result.returncode:
                    raise ServiceUnavailable(tr('python.screenshot.8c45070b0e9d'), code='screenshot/capture-failed')
                if target.stat().st_size > MAX_ATTACHMENT_BYTES:
                    raise PayloadTooLarge(tr('python.screenshot.3eb092762a5b'), code='screenshot/too-large')
                data = target.read_bytes()
                if not data.startswith(b'\x89PNG\r\n\x1a\n'):
                    raise ServiceUnavailable(tr('python.screenshot.5f751b68c7f6'), code='screenshot/invalid-image')
                return data
            except BaseException as exc:
                primary = exc
                raise
            finally:
                if owner and owner.isdecimal():
                    try:
                        _script(_SET_MINIMIZED, owner, window_token, 'false')
                    except (OSError, subprocess.SubprocessError) as exc:
                        # 即使捕获本身被取消，也要使窗口可见。
                        error = ServiceUnavailable(tr('python.screenshot.14fa97f4ae81'),
                                                   code='screenshot/restore-failed')
                        if primary is not None:
                            raise error from BaseExceptionGroup(tr('cleanup.failed'), [primary, exc])
                        raise error from exc
    finally:
        _capture_lock.release()
