"""OAuth 凭据的系统级安全存储。

存储策略（Windows 凭据管理器优先，受限权限文件兜底）：

* Windows —— 凭据管理器（``advapi32`` 的 CredWrite/CredRead，
  使用 ctypes 调用）；目标名 ``harness:connector:<name>``；
* 其它平台或钥匙串失败 —— 回退到 ``HARNESS_HOME/oauth-credentials.json``
  （权限 0600，原子替换写入，与 connectors.json 同纪律）。

凭据内容（JSON）：``access_token`` / ``refresh_token`` / ``expires_at``
/ ``client_id`` / ``scope`` / ``issuer``。access_token 永不写入
connectors.json——那里只放 ``${VAR}`` 引用。
"""

from __future__ import annotations

from harness.i18n import tr
from ..core.errors import ConnectorError

import json
import logging
import os
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: 回退文件名（位于 ``HARNESS_HOME``）
OAUTH_FILENAME = "oauth-credentials.json"


# ---------------------------------------------------------------------------
# Windows 凭据管理器后端
# ---------------------------------------------------------------------------
class _WinCredBackend:
    """通过 ctypes 调 Windows 凭据管理器（CRED_TYPE_GENERIC）。"""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        self._advapi = ctypes.WinDLL("advapi32", use_last_error=True)

        class CREDENTIALW(ctypes.Structure):
            _fields_ = [
                ("Flags", wintypes.DWORD),
                ("Type", wintypes.DWORD),
                ("TargetName", wintypes.LPWSTR),
                ("Comment", wintypes.LPWSTR),
                ("LastWritten", wintypes.FILETIME),
                ("CredentialBlobSize", wintypes.DWORD),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)),
                ("Persist", wintypes.DWORD),
                ("AttributeCount", wintypes.DWORD),
                ("Attributes", ctypes.c_void_p),
                ("TargetAlias", wintypes.LPWSTR),
                ("UserName", wintypes.LPWSTR),
            ]

        self._CREDENTIALW = CREDENTIALW
        self._PCREDENTIALW = ctypes.POINTER(CREDENTIALW)

    @staticmethod
    def _target(name: str) -> str:
        return f"harness:connector:{name}"

    def get(self, name: str) -> str | None:
        ctypes = self._ctypes
        pcred = self._PCREDENTIALW()
        ok = self._advapi.CredReadW(
            self._target(name), 1, 0, ctypes.byref(pcred)  # 通用凭据类型：CRED_TYPE_GENERIC=1
        )
        if not ok:
            return None
        try:
            size = pcred.contents.CredentialBlobSize
            raw = ctypes.string_at(pcred.contents.CredentialBlob, size)
            return raw.decode("utf-16-le").rstrip("\x00")
        finally:
            self._advapi.CredFree(pcred)

    def set(self, name: str, secret: str) -> None:
        ctypes = self._ctypes
        blob = secret.encode("utf-16-le") + b"\x00\x00"
        buf = (ctypes.c_byte * len(blob)).from_buffer_copy(blob)
        cred = self._CREDENTIALW()
        cred.Type = 1  # 通用凭据类型：CRED_TYPE_GENERIC
        cred.TargetName = self._target(name)
        cred.CredentialBlobSize = len(blob)
        cred.CredentialBlob = ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte))
        cred.Persist = 2  # 本机持久化：CRED_PERSIST_LOCAL_MACHINE
        cred.UserName = "harness"
        if not self._advapi.CredWriteW(ctypes.byref(cred), 0):
            raise OSError(tr('python.credentials.8cacb42e53b1', p0=f'{ctypes.get_last_error()}'))

    def delete(self, name: str) -> None:
        self._advapi.CredDeleteW(self._target(name), 1, 0)


# ---------------------------------------------------------------------------
# 文件后端（0600 权限，原子替换）
# ---------------------------------------------------------------------------
class _FileBackend:
    def __init__(self, home: str | os.PathLike[str]) -> None:
        self._path = Path(home).expanduser() / OAUTH_FILENAME
        self._lock = threading.RLock()

    def _read_all(self) -> dict[str, str]:
        with self._lock:
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return {}
            except (OSError, ValueError) as exc:
                raise ConnectorError(tr('connectors.credentials.invalid')) from exc
            if not isinstance(raw, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in raw.items()):
                raise ConnectorError(tr('connectors.credentials.invalid'))
            return raw

    def _write_all(self, data: dict[str, str]) -> None:
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            handle = tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", newline="\n",
                dir=str(self._path.parent), prefix=".oauth-", suffix=".tmp",
                delete=False,
            )
            try:
                with handle:
                    json.dump(data, handle, ensure_ascii=False, indent=2)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(handle.name, 0o600)
                os.replace(handle.name, self._path)
                os.chmod(self._path, 0o600)
            finally:
                if os.path.exists(handle.name):
                    os.unlink(handle.name)

    def get(self, name: str) -> str | None:
        return self._read_all().get(name)

    def set(self, name: str, secret: str) -> None:
        with self._lock:
            data = self._read_all()
            data[name] = secret
            self._write_all(data)

    def delete(self, name: str) -> None:
        with self._lock:
            data = self._read_all()
            if name in data:
                del data[name]
                self._write_all(data)


# ---------------------------------------------------------------------------
# 门面
# ---------------------------------------------------------------------------
class CredentialStore:
    """OAuth 凭据存储门面：Windows 钥匙串优先，0600 文件兜底。

    ``backend`` 可选 ``auto``（默认，按平台探测）/ ``wincred`` /
    ``file``（0600 JSON 文件，跨平台可移植）/ ``memory``（仅进程内，
    用于测试）。显式指定可绕开平台探测，例如无头服务器用 ``file``。
    """

    def __init__(
        self,
        home: str | os.PathLike[str] | None = None,
        *,
        backend: str | None = None,
    ) -> None:
        self._backend: Any = None
        self.backend_name = "memory"
        self._memory: dict[str, str] = {}
        if backend is None:
            backend = "auto"
        if backend not in ("auto", "wincred", "file", "memory"):
            raise ConnectorError(tr('connectors.credentials.backend_invalid'))
        if backend in ("auto", "wincred") and sys.platform == "win32":
            try:
                self._backend = _WinCredBackend()
                self.backend_name = "wincred"
            except Exception as exc:  # ctypes 装载失败等
                logger.warning(tr('log.08b0d0fb924a'), exc)
        if self._backend is None and backend in ("auto", "file") and home is not None:
            self._backend = _FileBackend(home)
            self.backend_name = "file"

    def get(self, name: str) -> dict[str, Any] | None:
        """读取一个连接器的 OAuth 凭据（不存在返回 ``None``）。"""
        raw = self._read(name)
        if raw is None:
            return None
        try:
            payload = json.loads(raw)
        except ValueError as exc:
            raise ConnectorError(tr('connectors.credentials.invalid')) from exc
        if not isinstance(payload, dict):
            raise ConnectorError(tr('connectors.credentials.invalid'))
        return payload

    def set(self, name: str, payload: dict[str, Any]) -> None:
        """写入凭据（完整替换）。"""
        if not isinstance(payload, dict):
            raise ConnectorError(tr('connectors.credentials.invalid'))
        self._write(name, json.dumps(payload, ensure_ascii=False))

    def delete(self, name: str) -> None:
        """删除凭据（幂等）。"""
        if self._backend is not None:
            self._backend.delete(name)
        else:
            self._memory.pop(name, None)

    # -- 内部 ------------------------------------------------------------
    def _read(self, name: str) -> str | None:
        if self._backend is not None:
            return self._backend.get(name)
        return self._memory.get(name)

    def _write(self, name: str, secret: str) -> None:
        if self._backend is not None:
            self._backend.set(name, secret)
        else:
            self._memory[name] = secret


__all__ = ["CredentialStore", "OAUTH_FILENAME"]
