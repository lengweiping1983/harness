"""同时启动 Web 热更新服务器与自动重载的 Python 后端。"""

from __future__ import annotations

import argparse
import errno
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time

from harness.i18n import tr

ROOT = Path(__file__).resolve().parent.parent


def snapshot(root: Path) -> dict[str, tuple[int, int]]:
    """只记录后端运行源码和资源，测试文件及缓存不触发重启。"""
    result = {}
    source_root = root / "harness"
    for path in source_root.rglob("*"):
        relative = path.relative_to(source_root)
        name = path.name
        if (any(part in {"__pycache__", "tests", "test", "__tests__", ".pytest_cache"} for part in relative.parts[:-1])
                or name.startswith("test_") or name.endswith("_test.py")
                or ".test." in name or ".spec." in name
                or path.suffix not in {".py", ".json", ".md"}):
            continue
        try:
            stat = path.stat()
            if path.is_file():
                result[str(path)] = (stat.st_mtime_ns, stat.st_size)
        except FileNotFoundError:
            pass
    return result


def stop(child: subprocess.Popen | None) -> None:
    """停止本入口启动的进程，并在超时后强制回收。"""
    if child is None or child.poll() is not None:
        return
    child.send_signal(signal.SIGTERM if os.name == "nt" else signal.SIGINT)
    try:
        child.wait(timeout=10)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait()


def listener_pids(port: int) -> set[int]:
    """通过系统工具查询指定 TCP 端口的监听进程，不匹配客户端连接。"""
    if sys.platform == "win32":
        command = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                   "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new(); "
                   "$ErrorActionPreference='Stop'; Get-NetTCPConnection -State Listen | "
                   f"Where-Object {{$_.LocalPort -eq {port}}} | "
                   "Select-Object -ExpandProperty OwningProcess -Unique"]
    else:
        command = [shutil.which("lsof") or "/usr/sbin/lsof", "-nP", "-t",
                   f"-iTCP:{port}", "-sTCP:LISTEN"]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=10)
    if result.returncode and not (sys.platform != "win32" and result.returncode == 1
                                  and not result.stdout.strip() and not result.stderr.strip()):
        raise OSError(result.stderr.strip() or result.stdout.strip() or str(result.returncode))
    return {int(line) for line in result.stdout.split()}


def bind_port(port: int) -> None:
    """按服务器绑定语义探测端口，避免把已关闭连接的等待状态当成监听者。"""
    with socket.socket() as listener:
        # Windows 的地址复用可能与活跃监听者重叠，保留默认绑定行为。
        if os.name != "nt":
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", port))
        listener.listen()


def wait_port(port: int, timeout: float) -> bool:
    """有界等待实际可监听，不因一次终止信号成功就报告端口释放。"""
    deadline = time.monotonic() + timeout
    while True:
        try:
            bind_port(port)
            return True
        except OSError as error:
            if error.errno != errno.EADDRINUSE:
                raise
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)


def release_port(port: int) -> None:
    """先正常终止，再核对仍监听的原进程并强制终止，最后确认端口释放。"""
    pids = listener_pids(port)
    if not pids:
        # 查询期间监听者可能已经退出，连接等待状态不需要终止任何进程。
        if wait_port(port, 5):
            return
        raise OSError(tr("runtime.dev.portOwnerUnknown", port=port))
    if any(pid <= 1 or pid in {os.getpid(), os.getppid()} for pid in pids):
        raise OSError(tr("runtime.dev.portOwnerUnknown", port=port))
    for pid in sorted(pids):
        print(tr("runtime.dev.stoppingPort", port=port, pid=pid), flush=True)
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    if wait_port(port, 5):
        return
    if os.name != "nt":
        # 不使用进程组或端口批量 kill，避免误停后来接管端口的新进程。
        remaining = listener_pids(port) & pids
        for pid in sorted(remaining):
            print(tr("runtime.dev.forceStoppingPort", port=port, pid=pid), flush=True)
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    if not wait_port(port, 5):
        raise OSError(tr("runtime.dev.portReleaseFailed", port=port))


def check_ports(*ports: int) -> None:
    """校验全部参数后，释放冲突端口并重新检查。"""
    if len(set(ports)) != len(ports):
        raise ValueError(tr("runtime.dev.samePorts"))
    if any(not 1 <= port <= 65535 for port in ports):
        raise ValueError(tr("runtime.dev.portRange"))
    for port in ports:
        try:
            bind_port(port)
        except OSError as error:
            if error.errno != errno.EADDRINUSE:
                raise
            release_port(port)
    for port in ports:
        bind_port(port)


def main() -> int:
    """按显式参数、用户设置或环境解析语言，并恢复调用方上下文。"""
    from harness.core.language import current_language, resolve_language, saved_language
    preliminary = argparse.ArgumentParser(add_help=False)
    preliminary.add_argument('--language', choices=('auto', 'en', 'zh-CN'))
    known, _ = preliminary.parse_known_args()
    preference = (known.language or saved_language(Path(os.environ.get('HARNESS_HOME', '~/.harness')).expanduser())
                  or os.environ.get('HARNESS_LANGUAGE', 'auto'))
    language = resolve_language(preference).effective_language
    token = current_language.set(language)
    try:
        return _main(language)
    finally:
        current_language.reset(token)


def _main(language: str) -> int:
    """管理两个开发进程，保存后端文件后重新启动后端。"""
    parser = argparse.ArgumentParser(description=tr('runtime.dev.description'))
    parser.add_argument('--language', choices=('auto', 'en', 'zh-CN'), help=tr('runtime.dev.languageHelp'))
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--web-port", type=int, default=5273)
    parser.add_argument("--share", metavar="HTTP_LAN_ORIGIN",
                        help=tr("runtime.dev.shareHelp"))
    args = parser.parse_args()
    node = shutil.which("node")
    vite = ROOT / "web/node_modules/vite/bin/vite.js"
    if not node or not vite.is_file():
        parser.error(tr("runtime.dev.dependencies"))
    try:
        check_ports(args.port, args.web_port)
    except (OSError, ValueError, OverflowError, UnicodeError, subprocess.SubprocessError) as error:
        parser.error(tr("runtime.dev.portUnavailable", error=str(error)))

    backend = frontend = gateway = None
    gateway_home = Path(os.environ.get('HARNESS_CODEX_GATEWAY_HOME', str(Path.home() / '.harness-codex-gateway')))
    gateway_state = gateway_home / 'codex-gateway.json'
    gateway_enabled = gateway_state.is_file() and '"enabled": true' in gateway_state.read_text(encoding='utf-8', errors='replace')

    def start_backend() -> subprocess.Popen:
        command = [sys.executable, "-X", "utf8", "-u", "-m", "harness.cli", "--ui", "--port", str(args.port), "--language", language]
        if args.share:
            command += ["--share", args.share]
        return subprocess.Popen(command, cwd=ROOT)

    def start_gateway() -> subprocess.Popen | None:
        if not gateway_enabled:
            return None
        return subprocess.Popen([sys.executable, '-m', 'harness.codex_gateway', '--home', str(gateway_home), 'serve', '--port', '18765'], cwd=ROOT)

    def terminate(_signum: int, _frame: object) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, terminate)
    try:
        previous = snapshot(ROOT)
        backend = start_backend()
        gateway = start_gateway()
        frontend = subprocess.Popen(
            [node, str(vite), "--host", "127.0.0.1",
             "--port", str(args.web_port), "--strictPort"],
            cwd=ROOT / "web",
            env={**os.environ, "HARNESS_BACKEND_URL": f"http://127.0.0.1:{args.port}"},
        )
        print(tr("runtime.dev.addresses", web=f"http://127.0.0.1:{args.web_port}", backend=f"http://127.0.0.1:{args.port}"), flush=True)
        if args.share:
            print(tr("runtime.dev.sharedAddress", url=f"{args.share}/shared"), flush=True)
        print(tr("runtime.dev.reloadNotice"), flush=True)
        reported_exit = False
        while frontend.poll() is None:
            time.sleep(0.5)
            current = snapshot(ROOT)
            if current != previous:
                # 等待连续保存稳定，避免一次编辑触发多次重启。
                time.sleep(0.3)
                previous = snapshot(ROOT)
                print(tr("runtime.dev.restarting"), flush=True)
                stop(backend)
                backend = start_backend()
                stop(gateway)
                gateway = start_gateway()
                reported_exit = False
            if backend.poll() is not None and not reported_exit:
                print(tr("runtime.dev.backendExited", code=backend.returncode), flush=True)
                reported_exit = True
        return frontend.returncode or 0
    except KeyboardInterrupt:
        return 0
    finally:
        stop(frontend)
        stop(backend)
        stop(gateway)


if __name__ == "__main__":
    from python_bootstrap import configure_utf8_process
    configure_utf8_process()
    raise SystemExit(main())
