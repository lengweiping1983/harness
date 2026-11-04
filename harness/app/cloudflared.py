"""查找隧道程序，并为 Windows 和 macOS 启动准备经过校验的官方版本。"""
from __future__ import annotations

from .mcp_errors import McpServiceError
from ..tools.process_env import execution_env

import base64
import hashlib
import io
import os
from pathlib import Path
import platform
import shutil
import sys
import subprocess
import tempfile
import tarfile
import time
from urllib.request import Request, urlopen


VERSION = '2026.9.1'
DARWIN_SHA256 = {
    'arm64': 'c27ab8fd0aa489449e3d201eb02f957ef460a13b613662928b1b23394bf1bcfe',
    'amd64': 'ff0d3b51d5ff70eceef89d6b32145fee985018a2174596a5dbe405e2766e2ac4',
}
WINDOWS_SHA256 = {
    'amd64': '2837888cc0f5d58f15b6dc478376de90b4d3ba5241c7947455d1e0a0df429712',
    '386': '11b6e4b2d306950bd87e7caa4deee8e80a32d71ffee555a96237a76651eeae4c',
}
RELEASE_ASSETS = {
    'cloudflared-windows-amd64.exe': 557330859,
    'cloudflared-windows-386.exe': 557330742,
    'cloudflared-darwin-amd64.tgz': 557330132,
    'cloudflared-darwin-arm64.tgz': 557329375,
}
MAX_BYTES = 100 * 1024 * 1024


def _check_cancel(cancel):
    """取消后不发布缓存文件，也不启动隧道。"""
    if cancel.is_set():
        raise McpServiceError('ui.mcpService.error.cancelled')


def _download(url, cancel):
    """限制下载大小和总时长；读取间隙响应停止操作。"""
    chunks = []
    size = 0
    deadline = time.monotonic() + 180
    request = Request(url, headers={'User-Agent': 'Harness-MCP', 'Accept': 'application/octet-stream'})
    with urlopen(request, timeout=10) as response:
        while True:
            _check_cancel(cancel)
            if time.monotonic() > deadline:
                raise McpServiceError('ui.mcpService.error.downloadTimeout')
            chunk = response.read(256 * 1024)
            if not chunk:
                return b''.join(chunks)
            size += len(chunk)
            if size > MAX_BYTES:
                raise McpServiceError('ui.mcpService.error.downloadTooLarge')
            chunks.append(chunk)


def _download_transport(url, cancel):
    """网络读取有限重试后使用系统传输，始终交由调用方校验摘要。"""
    failures = []
    for attempt in range(2):
        _check_cancel(cancel)
        try:
            return _download(url, cancel)
        except OSError as exc:
            failures.append(exc)
        if attempt == 0:
            cancel.wait(0.5)
    _check_cancel(cancel)
    curl = shutil.which('curl')
    if curl:
        # 文件接收避免管道阻塞；停止时等待本次传输退出后再释放临时文件。
        with tempfile.TemporaryFile() as output:
            try:
                process = subprocess.Popen(
                    [curl, '--fail', '--location', '--silent', '--show-error',
                     '--proto', '=https', '--proto-redir', '=https',
                     '--connect-timeout', '10', '--max-time', '180',
                     '--max-filesize', str(MAX_BYTES), url],
                    stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.DEVNULL,
                    env=execution_env(),
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            except OSError as exc:
                failures.append(exc)
            else:
                try:
                    deadline = time.monotonic() + 185
                    while process.poll() is None:
                        _check_cancel(cancel)
                        if time.monotonic() > deadline:
                            raise McpServiceError('ui.mcpService.error.downloadTimeout')
                        cancel.wait(0.1)
                    _check_cancel(cancel)
                    if process.returncode == 0:
                        output.seek(0)
                        payload = output.read(MAX_BYTES + 1)
                        if len(payload) > MAX_BYTES:
                            raise McpServiceError('ui.mcpService.error.downloadTooLarge')
                        return payload
                    failures.append(OSError(f'curl exited with status {process.returncode}'))
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.wait()
    if sys.platform == 'win32':
        _check_cancel(cancel)
        try:
            return _download_windows(url, cancel)
        except OSError as exc:
            failures.append(exc)
    raise ExceptionGroup('cloudflared download attempts failed', failures)


def _download_windows(url, cancel):
    """使用 Windows 系统证书与代理下载；流式限制大小，取消后回收进程。"""
    shell = shutil.which('powershell.exe')
    if not shell:
        raise OSError('Windows PowerShell is unavailable')
    with tempfile.TemporaryDirectory(prefix='harness-tunnel-download-') as directory:
        output = Path(directory) / 'payload'
        # URL 与输出路径通过 Base64 传递，避免 PowerShell 字符串注入。
        url64 = base64.b64encode(url.encode('utf-8')).decode('ascii')
        path64 = base64.b64encode(str(output).encode('utf-8')).decode('ascii')
        script = f"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Net.Http
$url = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{url64}'))
$path = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{path64}'))
$handler = New-Object Net.Http.HttpClientHandler
$handler.DefaultProxyCredentials = [Net.CredentialCache]::DefaultCredentials
$client = New-Object Net.Http.HttpClient($handler)
$client.Timeout = [TimeSpan]::FromSeconds(180)
$client.DefaultRequestHeaders.UserAgent.ParseAdd('Harness-MCP')
$client.DefaultRequestHeaders.Accept.ParseAdd('application/octet-stream')
try {{
    $response = $client.GetAsync($url, [Net.Http.HttpCompletionOption]::ResponseHeadersRead).GetAwaiter().GetResult()
    $response.EnsureSuccessStatusCode() | Out-Null
    if ($response.Content.Headers.ContentLength -gt {MAX_BYTES}) {{ throw 'Download exceeds size limit' }}
    $inputStream = $response.Content.ReadAsStreamAsync().GetAwaiter().GetResult()
    $file = [IO.File]::Create($path)
    try {{
        $buffer = New-Object byte[] 262144
        $total = 0L
        while (($count = $inputStream.Read($buffer, 0, $buffer.Length)) -gt 0) {{
            $total += $count
            if ($total -gt {MAX_BYTES}) {{ throw 'Download exceeds size limit' }}
            $file.Write($buffer, 0, $count)
        }}
    }} finally {{ $file.Dispose(); $inputStream.Dispose(); $response.Dispose() }}
}} finally {{ $client.Dispose(); $handler.Dispose() }}
"""
        encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
        process = subprocess.Popen([shell, '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=execution_env(),
            creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            deadline = time.monotonic() + 185
            while process.poll() is None:
                _check_cancel(cancel)
                if time.monotonic() > deadline:
                    raise OSError('Windows download timed out')
                cancel.wait(0.1)
            _check_cancel(cancel)
            if process.returncode != 0:
                raise OSError(f'Windows download exited with status {process.returncode}')
            if output.stat().st_size > MAX_BYTES:
                raise McpServiceError('ui.mcpService.error.downloadTooLarge')
            return output.read_bytes()
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()


def _download_official(url, cancel):
    """镜像优先、官方直链与资产 API 依次回退，每个来源都先校验摘要。"""
    urls = [url]
    prefix = f'https://github.com/cloudflare/cloudflared/releases/download/{VERSION}/'
    asset = RELEASE_ASSETS.get(url.removeprefix(prefix)) if url.startswith(prefix) else None
    expected = None
    if asset:
        name = url.removeprefix(prefix)
        if name != 'cloudflared-windows-386.exe':
            urls.insert(0, f'https://download.nexusdock.co/components/cloudflared/{VERSION}/{name}')
        hashes = {f'cloudflared-windows-{arch}.exe': digest for arch, digest in WINDOWS_SHA256.items()}
        hashes.update({f'cloudflared-darwin-{arch}.tgz': digest for arch, digest in DARWIN_SHA256.items()})
        expected = hashes[name]
        urls.append(f'https://api.github.com/repos/cloudflare/cloudflared/releases/assets/{asset}')
    failures = []
    for candidate in urls:
        _check_cancel(cancel)
        try:
            payload = _download_transport(candidate, cancel)
            _check_cancel(cancel)
            if expected and hashlib.sha256(payload).hexdigest() != expected:
                failures.append(McpServiceError('ui.mcpService.error.verificationFailed'))
                continue
            return payload
        except (OSError, ExceptionGroup) as exc:
            failures.append(exc)
    def diagnostics(error):
        if isinstance(error, ExceptionGroup):
            return '; '.join(diagnostics(part) for part in error.exceptions)
        return str(error)
    detail = '; '.join(diagnostics(error) for error in failures)[:1200]
    raise McpServiceError('ui.mcpService.error.downloadFailedDetails', detail=detail) from ExceptionGroup(
        'cloudflared sources failed', failures)


def _install_darwin(home, cancel, machine):
    """仅取归档里的普通可执行文件，不解包路径、链接或其他文件。"""
    arch = {'arm64': 'arm64', 'aarch64': 'arm64', 'x86_64': 'amd64', 'amd64': 'amd64'}.get(machine.lower())
    if not arch:
        raise McpServiceError('ui.mcpService.error.architecture', machine=machine)
    cache = Path(home) / 'tools' / f'cloudflared-{VERSION}-darwin-{arch}'
    cache.mkdir(parents=True, exist_ok=True)
    cache.chmod(0o700)
    archive = cache / 'release.tgz'
    binary = cache / 'cloudflared'
    temporary = cache / 'cloudflared.partial'
    _check_cancel(cancel)
    payload = archive.read_bytes() if archive.is_file() and archive.stat().st_size <= MAX_BYTES else b''
    if hashlib.sha256(payload).hexdigest() != DARWIN_SHA256[arch]:
        url = f'https://github.com/cloudflare/cloudflared/releases/download/{VERSION}/cloudflared-darwin-{arch}.tgz'
        payload = _download_official(url, cancel)
        if hashlib.sha256(payload).hexdigest() != DARWIN_SHA256[arch]:
            raise McpServiceError('ui.mcpService.error.verificationFailed')
    _check_cancel(cancel)
    with tarfile.open(fileobj=io.BytesIO(payload), mode='r:gz') as package:
        entries = [entry for entry in package.getmembers() if entry.name in {'cloudflared', './cloudflared'}]
        if len(entries) != 1 or not entries[0].isfile() or not 0 < entries[0].size <= MAX_BYTES:
            raise McpServiceError('ui.mcpService.error.archiveInvalid')
        with package.extractfile(entries[0]) as stream:
            executable = stream.read(MAX_BYTES + 1)
    if len(executable) != entries[0].size:
        raise McpServiceError('ui.mcpService.error.archiveIncomplete')
    _check_cancel(cancel)
    try:
        temporary.write_bytes(executable)
        temporary.chmod(0o700)
        _check_cancel(cancel)
        temporary.replace(binary)
        archive.write_bytes(payload)
    finally:
        temporary.unlink(missing_ok=True)
    return str(binary)


def _install_windows(home, cancel, machine):
    """下载固定版本的官方程序，校验缓存及下载内容后原子发布。"""
    arch = {'amd64': 'amd64', 'x86_64': 'amd64', 'x86': '386', 'i386': '386',
            'i686': '386', 'arm64': 'amd64', 'aarch64': 'amd64'}.get(machine.lower())
    if not arch:
        raise McpServiceError('ui.mcpService.error.architecture', machine=machine)
    _check_cancel(cancel)
    cache = Path(home) / 'tools' / f'cloudflared-{VERSION}-windows-{arch}'
    binary = cache / 'cloudflared.exe'
    if binary.is_file() and binary.stat().st_size <= MAX_BYTES:
        if hashlib.sha256(binary.read_bytes()).hexdigest() == WINDOWS_SHA256[arch]:
            _check_cancel(cancel)
            return str(binary)
    url = f'https://github.com/cloudflare/cloudflared/releases/download/{VERSION}/cloudflared-windows-{arch}.exe'
    payload = _download_official(url, cancel)
    if hashlib.sha256(payload).hexdigest() != WINDOWS_SHA256[arch]:
        raise McpServiceError('ui.mcpService.error.verificationFailed')
    _check_cancel(cancel)
    cache.mkdir(parents=True, exist_ok=True)
    descriptor, filename = tempfile.mkstemp(prefix='cloudflared-', suffix='.partial', dir=cache)
    temporary = Path(filename)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        _check_cancel(cancel)
        temporary.replace(binary)
    finally:
        temporary.unlink(missing_ok=True)
    return str(binary)


def resolve_cloudflared(home, cancel):
    """优先复用已安装程序；Windows 和 macOS 缺少程序时自动准备私有副本。"""
    _check_cancel(cancel)
    found = shutil.which('cloudflared')
    if found:
        return found
    if sys.platform == 'win32':
        return _install_windows(home, cancel, platform.machine())
    if sys.platform == 'darwin':
        for candidate in (Path('/opt/homebrew/bin/cloudflared'), Path('/usr/local/bin/cloudflared'),
                          Path('/opt/local/bin/cloudflared'), Path.home() / '.local/bin/cloudflared'):
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
        return _install_darwin(home, cancel, platform.machine())
    raise McpServiceError('ui.mcpService.error.missingProgram')
