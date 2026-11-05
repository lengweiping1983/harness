"""hex 语音转文字（STT）内置工具。

hex 是随 harness 一起分发的本地语音听写程序：源码在 ``vendor/hex``，
由 ``scripts/build-hex.py`` 编译成无头服务二进制，放在
``harness/resources/hex/<平台>-<架构>/hex`` 随包分发。

本模块负责三件事：

1. 定位并**按需启动** hex 服务（``hex service --embedded`` 命令，只在本机监听，
   通过 owner-only 本地 API 提供转录能力，全部离线计算）；
2. 通过本地 API 准备模型（首次使用会自动从 HuggingFace 下载，
   之后模型常驻本地，转录完全离线）；
3. 把 ``transcribe_audio`` 注册成 harness 内置工具，
   让 agent 直接对本地 WAV 文件做语音转文字。

生命周期与 harness 保持一致：第一次调用转录工具时懒启动服务，
harness 关闭时由 ``Harness.close`` 调用 ``HexServiceManager.stop`` 优雅停机。
"""

from __future__ import annotations

import asyncio
import ctypes
import json
import os
import platform
import re
import threading
from urllib.parse import quote, urlencode, urlsplit
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from harness.i18n import tr
from ...core.cleanup import join_cleanup, raise_cleanup_errors

from ...llm.types import TextBlock
from ..registry import ToolDefinition, ToolRunContext, ToolResult

#: 默认多语言转录模型（首次使用自动下载）。
DEFAULT_MODEL = "whisper_large_v3_turbo"

#: 默认自动识别语音语言，独立于界面语言。
DEFAULT_LANGUAGE = "auto"

#: hex 本地 API 的单次上传上限（与 hex 服务端 MAX_AUDIO_BYTES 保持一致）。
MAX_AUDIO_BYTES = 64 * 1024 * 1024

#: 等待服务就绪（discovery 文件出现）的最长时间。
SERVICE_START_TIMEOUT = 15.0

#: 模型下载/加载的准备超时（首次下载几百 MB 模型可能较慢）。
MODEL_PREPARE_TIMEOUT = 600.0


def _platform_tag() -> str:
    """返回与构建脚本一致的平台标签，例如 ``macos-arm64``。"""
    system = {"darwin": "macos", "linux": "linux", "win32": "windows"}.get(sys.platform, sys.platform)
    machine = platform.machine().lower()
    if machine in {"aarch64", "arm64"}:
        arch = "arm64"
    elif machine in {"x86_64", "amd64"}:
        arch = "x86_64"
    else:
        arch = machine
    return f"{system}-{arch}"


def hex_cpu_supported() -> bool:
    """Windows 引擎要求系统可用的 AVX2；只查询系统能力，不执行引擎。"""
    if sys.platform != 'win32':
        return True
    try:
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        feature = kernel32.IsProcessorFeaturePresent
        feature.argtypes = [ctypes.c_uint32]
        feature.restype = ctypes.c_int
        # PF_AVX2_INSTRUCTIONS_AVAILABLE 同时反映操作系统提供的指令集能力。
        return bool(feature(40))
    except (OSError, AttributeError) as error:
        copy = {'key': 'ui.voice.global.cpuCheckFailed'}
        raise HexToolError(tr(copy['key']), copy=copy) from error


def require_hex_cpu() -> None:
    """在读取服务状态或启动进程前拒绝不满足指令集要求的调用。"""
    if not hex_cpu_supported():
        copy = {'key': 'ui.voice.global.cpuUnsupported'}
        raise HexToolError(tr(copy['key']), copy=copy)


def hex_binary_path() -> Path:
    """优先随包引擎；macOS 与 Windows 可复用用户显式安装的原生 Hex。"""
    name = "hex.exe" if sys.platform == "win32" else "hex"
    resources = Path(__file__).resolve().parents[2] / "resources" / "hex"
    bundled = resources / _platform_tag() / name
    # 桌面包自带引擎拥有独立版本，不能被外部 Hex 的安装或损坏指针覆盖。
    if bundled.is_file():
        return bundled
    # 源码及 SDK 未携带引擎时，仍严格核对用户显式安装的第三方版本。
    if sys.platform == 'win32':
        appdata = os.environ.get('APPDATA')
        root = (Path(appdata) if appdata else Path.home() / 'AppData' / 'Roaming') / 'hex'
        pointer = root / 'current-version'
        try:
            with pointer.open('rb') as stream:
                data = stream.read(257)
        except FileNotFoundError:
            return bundled
        except OSError as error:
            raise _installation_error(pointer) from error
        try:
            version = data.decode('utf-8').strip()
            if len(data) > 256 or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.+_-]{0,127}', version):
                raise ValueError('current-version')
            installed = root / 'versions' / version / 'hex.exe'
            if not installed.is_file() or not installed.resolve().is_relative_to((root / 'versions').resolve()):
                raise ValueError('installed executable')
            return installed
        except (OSError, ValueError, RuntimeError) as error:
            raise _installation_error(pointer) from error
    if sys.platform == 'darwin':
        # 保留完整签名应用包，复用其权限、辅助程序与资源。
        for app in (Path.home() / 'Applications' / 'Hex.app', Path('/Applications/Hex.app')):
            installed = app / 'Contents' / 'MacOS' / 'hex'
            if installed.is_file():
                return installed
    return bundled


def _installation_error(path: Path) -> HexToolError:
    """损坏安装指针保留原件，不猜测其他版本或修复用户安装。"""
    copy = {'key': 'ui.voice.installationInvalid', 'params': {'path': str(path)}}
    return HexToolError(tr(copy['key'], **copy['params']), copy=copy)



def hex_discovery_path() -> Path:
    """返回 hex 本地 API 的 discovery 文件路径（与 hex 的 app_paths 对齐）。

    hex 支持用 ``HEX_APPLICATION_SUPPORT_DIR`` 覆盖数据目录，这里保持一致，
    便于测试时把整个服务隔离到临时目录。
    """
    override = os.environ.get("HEX_APPLICATION_SUPPORT_DIR")
    if override:
        return Path(override) / "local-api.json"
    # Windows 测试与精简启动环境可能没有 APPDATA，也可能没有任何
    # Python 可识别的 home 环境变量。此时仍返回稳定的用户数据候选路径，
    # 不能让 Path.home() 的 RuntimeError 阻断 Harness 的基础启动。
    def home() -> Path:
        try:
            return Path.home()
        except RuntimeError:
            return Path.cwd()

    if sys.platform == "darwin":
        base = home() / "Library" / "Application Support" / "hex"
    elif sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) / "hex" if appdata else home() / "AppData" / "Roaming" / "hex"
    else:
        xdg = os.environ.get("XDG_DATA_HOME")
        base = Path(xdg) / "hex" if xdg else home() / ".local" / "share" / "hex"
    return base / "local-api.json"


@dataclass
class DiscoveryDocument:
    """hex 服务写入的本地 API 接入信息。"""

    port: int
    token: str
    api_version: str
    pid: int

    @classmethod
    def from_file(cls, path: Path) -> "DiscoveryDocument | None":
        """读取 discovery 文件；文件不存在或格式错误时返回 ``None``。"""
        try:
            with path.open("rb") as stream:
                data = stream.read(8193)
            if len(data) > 8192:
                return None
            raw = json.loads(data, object_pairs_hook=_unique_fields)
            return cls.from_value(raw)
        except (OSError, ValueError, RecursionError):
            return None

    @classmethod
    def from_value(cls, raw: Any) -> "DiscoveryDocument | None":
        """文件与嵌入握手共用字段准入，不强转身份。"""
        if (not isinstance(raw, dict) or type(raw.get("port")) is not int
                or not 1 <= raw["port"] <= 65535
                or type(raw.get("pid")) is not int or not 0 < raw["pid"] < 2**31
                or not isinstance(raw.get("token"), str) or not raw["token"]
                or len(raw["token"]) > 1024 or not raw["token"].isascii()
                or any(ord(char) < 33 or ord(char) > 126 for char in raw["token"])
                or raw.get("apiVersion") != "2"):
            return None
        return cls(raw["port"], raw["token"], raw["apiVersion"], raw["pid"])


def _unique_fields(pairs):
    """拒绝重复 JSON 字段，不覆盖服务身份。"""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(key)
        result[key] = value
    return result


def validate_selection(model: object, language: object) -> None:
    """直接调用与模型工具共用参数准入。"""
    if any(not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value)
           for value in (model, language)):
        raise HexToolError(tr('ui.voice.invalid'))



def _windows_process_api():
    """只声明查询接口；Windows 的 os.kill(pid, 0) 不用于探活。"""
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.GetLastError.restype = wintypes.DWORD
    return kernel


def _windows_pid_alive(pid: int) -> bool:
    """查询退出状态并归还查询句柄，绝不终止或向原生应用发送信号。"""
    from ctypes import wintypes
    kernel = _windows_process_api()
    handle = kernel.OpenProcess(0x1000, False, pid)

    def failure(operation: str) -> HexToolError:
        copy = {'key': 'ui.voice.pidProbeFailed',
                'params': {'operation': operation, 'code': kernel.GetLastError()}}
        return HexToolError(tr(copy['key'], **copy['params']), copy=copy)

    if not handle:
        code = kernel.GetLastError()
        if code == 5:
            return True
        if code == 87:
            return False
        raise failure('OpenProcess')
    errors: list[BaseException] = []
    exit_code = wintypes.DWORD()
    try:
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            errors.append(failure('GetExitCodeProcess'))
    except BaseException as error:
        errors.append(error)
    finally:
        try:
            if not kernel.CloseHandle(handle):
                errors.append(failure('CloseHandle'))
        except BaseException as error:
            errors.append(error)
    raise_cleanup_errors(errors)
    return exit_code.value == 259


def _pid_alive(pid: int) -> bool:
    """判断进程号是否存活（不要求是 hex 进程，discovery 还会做 API 探活）。"""
    if pid <= 0:
        return False
    if sys.platform == 'win32':
        return _windows_pid_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class HexServiceManager:
    """管理 hex 无头服务的生命周期与本地 API 调用。

    - ``ensure_running``：优先复用已在运行的 hex（用户自己启动的也可用），
      否则启动随包二进制；
    - ``prepare_model``：下载并加载转录模型（幂等，已加载时秒回）；
    - ``transcribe``：把 WAV 字节交给本地 API 转成文字；
    - ``stop``：harness 关闭时优雅停止自启的服务，不打扰用户自己开的 hex。
    """

    def __init__(
        self,
        *,
        binary: Path | None = None,
        discovery: Path | None = None,
        start_timeout: float = SERVICE_START_TIMEOUT,
    ) -> None:
        self._configured_binary = binary
        self.discovery_path = discovery or hex_discovery_path()
        self.start_timeout = start_timeout
        self._process: subprocess.Popen | None = None
        self._base_url: str | None = None
        self._token: str | None = None
        self._lock = threading.RLock()
        self._closed = False
        self._owned_document: DiscoveryDocument | None = None
        self._reader: threading.Thread | None = None
        self._reader_error: BaseException | None = None
        self._prepared: tuple[str, str, str, str] | None = None
        # 本机 API 只使用 HTTP，不初始化代理、系统证书库或文件等其他协议。
        self._opener = urllib.request.OpenerDirector()
        for handler in (urllib.request.HTTPHandler(), urllib.request.HTTPDefaultErrorHandler(),
                        urllib.request.HTTPErrorProcessor(), urllib.request.UnknownHandler(), _NoRedirect()):
            self._opener.add_handler(handler)

    @property
    def binary(self) -> Path:
        """语音引擎定位按需进行；外部安装损坏不阻止 Harness 启动。"""
        return self._configured_binary or hex_binary_path()

    # ------------------------------------------------------------------
    # 进程生命周期
    # ------------------------------------------------------------------
    def _discover(self) -> DiscoveryDocument | None:
        """读取 discovery；若 hex 进程已死则视为不可用。"""
        document = DiscoveryDocument.from_file(self.discovery_path)
        if document is None or not _pid_alive(document.pid):
            return None
        return document if self._probe(document) else None

    def _probe(self, document: DiscoveryDocument) -> bool:
        """认证健康读取不经过代理或重定向。"""
        request = urllib.request.Request(f"http://127.0.0.1:{document.port}/health",
                                         headers={"Authorization": f"Bearer {document.token}"})
        try:
            with self._opener.open(request, timeout=1) as response:
                body = response.read(8193)
            health = json.loads(body, object_pairs_hook=_unique_fields)
            if len(body) > 8192 or not isinstance(health, dict) or health.get("apiVersion") != "2":
                return False
        except urllib.error.HTTPError as error:
            error.close()
            return False
        except (OSError, ValueError, RecursionError):
            return False
        return True

    def ensure_running(self) -> tuple[str, str]:
        """确保本地 API 可用，返回 ``(base_url, token)``。

        顺序：读 discovery → 探活 → 未就绪则启动随包二进制并等待。
        二进制缺失时抛出带构建指引的双语错误。
        """
        with self._lock:
            return self._ensure_running()

    def _ensure_running(self) -> tuple[str, str]:
        """锁内只启动一次；每次准入重新核对存活端点。"""
        if self._closed:
            raise HexToolError(tr('ui.voice.closed'))
        require_hex_cpu()
        if self._owned_document is not None:
            document = self._owned_document
            if self._process.poll() is None and self._probe(document):
                return f"http://127.0.0.1:{document.port}", document.token
            self._stop_process()
        document = self._discover()
        if document is not None:
            self._base_url = f"http://127.0.0.1:{document.port}"
            self._token = document.token
            return self._base_url, self._token

        binary = self.binary
        if not binary.is_file():
            raise HexToolError(
                tr('python.hex_tools.binary_missing', p0=str(binary)),
                copy={'key': 'ui.voice.binaryMissing', 'params': {'path': str(binary)}},
            )
        self._start_service(binary)
        document = self._owned_document
        return f"http://127.0.0.1:{document.port}", document.token

    def _start_service(self, binary: Path) -> None:
        """启动私有嵌入服务；握手不改写用户 discovery，不占用原生应用锁。"""
        log_dir = self.discovery_path.parent / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "harness-hex-service.log"
        try:
            with log_file.open("ab") as output:
                from ..process_env import execution_env
                env = execution_env({"HEX_APPLICATION_SUPPORT_DIR": str(self.discovery_path.parent)})
                self._process = subprocess.Popen(
                    [str(binary), "service", "--embedded"], stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=output,
                    start_new_session=sys.platform != "win32", env=env,
                )
        except OSError as error:
            raise HexToolError(tr('python.hex_tools.service_spawn_failed', p0=str(error))) from error
        process = self._process
        ready = threading.Event()
        result: list[bytes | BaseException] = []

        def read_ready() -> None:
            # 有界读取握手，随后排空 stdout，避免第三方诊断填满管道。
            try:
                result.append(process.stdout.readline(8193))
            except BaseException as error:
                result.append(error)
                return
            finally:
                ready.set()
            try:
                while process.stdout.read(65536):
                    pass
            except BaseException as error:
                self._reader_error = error

        self._reader = threading.Thread(target=read_ready, name='harness-hex-ready')
        try:
            self._reader.start()
            if not ready.wait(self.start_timeout):
                raise HexToolError(tr('python.hex_tools.service_start_timeout'))
            if isinstance(result[0], BaseException):
                raise result[0]
            line = result[0]
            if len(line) > 8192 or not line.endswith(b'\n'):
                raise ValueError('handshake')
            raw = json.loads(line, object_pairs_hook=_unique_fields)
            if not isinstance(raw, dict) or raw.get('type') != 'ready' or not isinstance(raw.get('url'), str):
                raise ValueError('handshake')
            url = urlsplit(raw['url'])
            if (url.scheme != 'http' or url.hostname != '127.0.0.1' or url.username or url.password
                    or url.path or url.query or url.fragment):
                raise ValueError('endpoint')
            document = DiscoveryDocument.from_value({**raw, 'port': url.port})
            if document is None or document.pid != process.pid or not self._probe(document):
                raise ValueError('identity')
            self._owned_document = document
        except BaseException as error:
            primary = HexToolError(tr('python.hex_tools.bad_response')) if isinstance(error, (ValueError, RecursionError)) else error
            try:
                self._stop_process()
            except BaseException as cleanup:
                raise_cleanup_errors([primary, cleanup])
            if primary is error:
                raise
            raise primary from error

    def stop(self) -> None:
        """先关闭新准入，再等待原操作与所属进程；失败保留句柄。"""
        self._closed = True
        with self._lock:
            self._stop_process()

    def _stop_process(self) -> None:
        """关闭 stdin 租约，必要时升级终止，收回握手线程后才清空句柄。"""
        self._prepared = None
        process = self._process
        errors: list[BaseException] = []
        if process is not None:
            if process.stdin is not None and not process.stdin.closed:
                try:
                    process.stdin.close()
                except BaseException as error:
                    errors.append(error)
            try:
                try:
                    process.wait(timeout=5.5)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
            except BaseException as error:
                errors.append(error)
        if self._reader is not None and self._reader.ident is not None:
            self._reader.join(timeout=5)
            if self._reader.is_alive():
                errors.append(HexToolError(tr('ui.voice.cleanupFailed')))
        if self._reader_error is not None:
            errors.append(self._reader_error)
            self._reader_error = None
        if not errors and process is not None and process.stdout is not None:
            try:
                process.stdout.close()
            except BaseException as error:
                errors.append(error)
        raise_cleanup_errors(errors)
        self._process = None
        self._reader = None
        self._owned_document = None
        self._base_url = self._token = None

    # ------------------------------------------------------------------
    # 本地 API 调用
    # ------------------------------------------------------------------
    def _request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
    ) -> bytes:
        """向 hex 本地 API 发请求；非 2xx 或网络错误转成当前调用语言的工具错误。"""
        base_url, token = self.ensure_running()
        request = urllib.request.Request(
            f"{base_url}{path}",
            data=body,
            method=method,
            headers={
                "Authorization": f"Bearer {token}",
                **(headers or {}),
            },
        )
        try:
            with self._opener.open(request, timeout=timeout) as response:
                payload = response.read(2 * 1024 * 1024 + 1)
                if len(payload) > 2 * 1024 * 1024:
                    raise HexToolError(tr('python.hex_tools.bad_response'))
                return payload
        except urllib.error.HTTPError as error:
            try:
                detail = (error.read(400) or b"").decode("utf-8").strip()
            except UnicodeDecodeError:
                detail = tr('python.hex_tools.non_utf8_detail')
            finally:
                error.close()
            raise HexToolError(
                tr('python.hex_tools.http_error', p0=f'{error.code}', p1=detail[:200])
            ) from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise HexToolError(
                tr('python.hex_tools.request_failed', p0=str(error))
            ) from error

    def prepare_model(self, model: str, timeout: float = MODEL_PREPARE_TIMEOUT,
                      *, language: str = DEFAULT_LANGUAGE) -> None:
        """显式准备转录模型：必要时下载、加载，并执行引擎预热。

        prepare 端点返回 SSE 事件流（进度/完成/错误），这里逐行读到
        ``"type":"ok"`` 为止；调用方（异步工具）应通过
        ``asyncio.to_thread`` 执行，避免阻塞事件循环。
        """
        validate_selection(model, language)
        self._prepared = None
        payload = self._request("POST", f"/models/{quote(model, safe='')}/prepare?" +
                                urlencode({'language': language}), timeout=timeout)
        try:
            events_text = payload.decode("utf-8")
        except UnicodeDecodeError as error:
            # 服务端响应必须可严格解码；出现非法字节时保留原始错误，不静默替换。
            raise HexToolError(
                tr('python.hex_tools.prepare_failed', p0=str(error))
            ) from error
        for line in events_text.splitlines():
            if not line.startswith("data:"):
                continue
            try:
                event = json.loads(line[5:].strip(), object_pairs_hook=_unique_fields)
            except (ValueError, RecursionError) as error:
                raise HexToolError(tr('python.hex_tools.bad_response')) from error
            if not isinstance(event, dict):
                raise HexToolError(tr('python.hex_tools.bad_response'))
            if event.get("type") == "ok":
                return
            if event.get("type") in {"error", "failed"}:
                raise HexToolError(
                    tr('python.hex_tools.prepare_failed', p0=str(event.get("error") or event))
                )
        raise HexToolError(tr('python.hex_tools.prepare_failed', p0=tr("ui.voice.incomplete")))

    def transcribe(
        self,
        audio: bytes,
        *,
        model: str = DEFAULT_MODEL,
        language: str = DEFAULT_LANGUAGE,
        timeout: float = 120.0,
    ) -> dict[str, Any]:
        """把 WAV 字节交给本地 API 转录，返回响应 JSON。"""
        validate_selection(model, language)
        if not audio or len(audio) > MAX_AUDIO_BYTES:
            raise HexToolError(tr('ui.voice.invalid'))
        payload = self._request(
            "POST",
            "/transcriptions?" + urlencode({"model": model, "language": language}),
            body=audio,
            headers={"Content-Type": "audio/wav"},
            timeout=timeout,
        )
        try:
            result = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_fields)
        except (ValueError, RecursionError) as error:
            raise HexToolError(tr('python.hex_tools.bad_response')) from error
        if not isinstance(result, dict) or not isinstance(result.get("transcript"), str):
            raise HexToolError(tr('python.hex_tools.bad_response'))
        return result

    def prepare_transcription(self, *, model: str = DEFAULT_MODEL,
                              language: str = DEFAULT_LANGUAGE) -> None:
        """录音期间提前准备，同一认证端点和选择只复用已确认的成功。"""
        validate_selection(model, language)
        with self._lock:
            endpoint = self.ensure_running()
            selection = (*endpoint, model, language)
            if self._prepared == selection:
                return
            self._prepared = None
            self.prepare_model(model, language=language)
            self._prepared = selection

    def transcribe_prepared(self, audio: bytes, *, model: str = DEFAULT_MODEL,
                            language: str = DEFAULT_LANGUAGE,
                            timings: dict[str, float] | None = None) -> dict[str, Any]:
        """串行执行完整模型准备和转录；关闭等待本次操作真正结束。"""
        validate_selection(model, language)
        if not audio or len(audio) > MAX_AUDIO_BYTES:
            raise HexToolError(tr('ui.voice.invalid'))
        started = time.perf_counter()
        with self._lock:
            admitted = time.perf_counter()
            self.prepare_transcription(model=model, language=language)
            prepared = time.perf_counter()
            try:
                return self.transcribe(audio, model=model, language=language)
            except BaseException:
                # 失败可能表示模型已被替换或端点已变化；本次不重放音频。
                self._prepared = None
                raise
            finally:
                if timings is not None:
                    timings.update(queue=(admitted - started) * 1000,
                                   prepare=(prepared - admitted) * 1000,
                                   inference=(time.perf_counter() - prepared) * 1000)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """拒绝本地端点重定向，避免泄露认证信息和音频。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


async def run_hex_owned(function, *args, **kwargs):
    """取消等待仍等待已开始的线程，避免关闭与转录交叉失去所有权。"""
    pending = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(pending)
    except asyncio.CancelledError:
        await join_cleanup(pending)
        raise


class HexToolError(Exception):
    """保留发生时诊断；公开错误可另带浏览器资源身份。"""

    def __init__(self, message: str, *, copy: dict[str, Any] | None = None):
        super().__init__(message)
        self.copy = copy


def _read_audio(path: Path) -> bytes:
    """读取待转录的 WAV 文件，校验存在性与 hex 的 64MB 上传上限。"""
    if not path.is_file():
        raise HexToolError(tr('python.hex_tools.audio_missing', p0=str(path)))
    with path.open("rb") as stream:
        audio = stream.read(MAX_AUDIO_BYTES + 1)
    if len(audio) > MAX_AUDIO_BYTES:
        raise HexToolError(
            tr('python.hex_tools.audio_too_large', p0=f'{len(audio)}', p1=f'{MAX_AUDIO_BYTES}')
        )
    return audio


def build_hex_tools(service: HexServiceManager) -> list[ToolDefinition]:
    """构造 ``transcribe_audio`` 工具，绑定给定的服务管理器。"""

    async def transcribe_audio(args: Any, ctx: ToolRunContext) -> ToolResult:
        if not isinstance(args, dict):
            raise HexToolError(tr("ui.voice.invalid"))
        audio_path = args.get("audio_path")
        if not isinstance(audio_path, str) or not audio_path.strip():
            raise HexToolError(tr('python.hex_tools.audio_path_required'))
        model = args.get("model", DEFAULT_MODEL)
        language = args.get("language", DEFAULT_LANGUAGE)

        validate_selection(model, language)
        path = Path(audio_path)
        if not path.is_absolute():
            if ctx is None or not ctx.cwd:
                raise HexToolError(tr('python.hex_tools.audio_path_required'))
            path = Path(ctx.cwd) / path
        audio = await run_hex_owned(_read_audio, path)
        started = time.monotonic()
        result = await run_hex_owned(service.transcribe_prepared, audio, model=model, language=language)
        elapsed = round(time.monotonic() - started, 2)
        transcript = str(result.get("transcript") or "").strip()
        data = {
            "transcript": transcript,
            "model": model,
            "language": language,
            "audio_path": audio_path,
            "elapsed_seconds": elapsed,
        }
        return ToolResult(
            content=[TextBlock(text=tr('python.hex_tools.result_format',
                                        p0=transcript, p1=f'{elapsed:g}'))],
            display=data,
            data=data,
        )

    return [
        ToolDefinition(
            schema={
                "description_key": 'python.hex_tools.tool_description',
                "parameter_keys": {
                    ('properties', 'audio_path', 'description'): 'python.hex_tools.param_audio_path',
                    ('properties', 'model', 'description'): 'python.hex_tools.param_model',
                    ('properties', 'language', 'description'): 'python.hex_tools.param_language',
                },
                "name": "transcribe_audio",
                "description": tr('python.hex_tools.tool_description'),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "audio_path": {
                            "type": "string",
                            "description": tr('python.hex_tools.param_audio_path'),
                        },
                        "model": {
                            "type": "string",
                            "description": tr('python.hex_tools.param_model'),
                        },
                        "language": {
                            "type": "string",
                            "description": tr('python.hex_tools.param_language'),
                        },
                    },
                    "required": ["audio_path"],
                    "additionalProperties": False,
                },
            },
            execute=transcribe_audio,
            # 转录在本地 GPU/CPU 上运行，不写工作区文件；
            # 服务单实例，模型加载互斥，不允许并发。
            effect="execute",
            concurrency_safe=False,
            timeout=MODEL_PREPARE_TIMEOUT + 150.0,
        )
    ]


__all__ = [
    "DEFAULT_MODEL",
    "DEFAULT_LANGUAGE",
    "HexServiceManager",
    "HexToolError",
    "build_hex_tools",
    "hex_binary_path",
    "hex_discovery_path",
]
