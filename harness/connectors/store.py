"""连接器声明存储（``~/.harness/connectors.json``）。

当前声明文件格式::

    {
      "version": 1,
      "connectors": {
        "my-server": {
          "transport": "stdio",              # 或 "streamable-http"
          "command": "npx",
          "args": ["-y", "@some/mcp-server"],
          "env": {"TOKEN": "${MY_SERVER_TOKEN}"},
          "url": "",                          # streamable-http 时使用
          "headers": {},
          "enabled": true,
          "bound": false,
          "metadata": {"description": "…"}
        }
      }
    }

写纪律同 :class:`harness.plugins.state.EscStateStore`：原子替换 + RLock。
"""

from __future__ import annotations

from harness.i18n import tr
from ..core.fsatomic import atomic_write

import json
from contextlib import contextmanager
import logging
import os
import re
import threading
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from ..core.errors import ConnectorError
from ..tools.builtin.mcp_client import McpServerConfig


#: 状态文件名（位于 ``HARNESS_HOME``）
logger = logging.getLogger(__name__)
CONNECTORS_FILENAME = "connectors.json"

#: 连接器名合法性（与 MCP server_name 同规则）
NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")

#: env / headers 值里的变量引用语法
VAR_REF_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

#: 允许的传输类型
TRANSPORTS = ("stdio", "streamable-http")


def interpolate(value: str) -> str:
    """把 ``${VAR}`` 替换成环境变量值。

    未定义的变量替换成空串并记警告——连接器声明里的密钥引用
    必须只来自进程环境，不能落盘明文。
    """
    def substitute(match: re.Match[str]) -> str:
        name = match.group(1)
        resolved = os.environ.get(name)
        if resolved is None:
            logger.warning(tr('log.20234da3b8e8'), name)
            return ""
        return resolved

    return VAR_REF_RE.sub(substitute, value)


@dataclass
class ConnectorRecord:
    """一台连接器的完整声明。"""

    name: str
    transport: str = "stdio"
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    bound: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not NAME_RE.fullmatch(self.name):
            raise ConnectorError(
                tr('python.store.d10190d26c4a', p0=f'{self.name!r}')
            )
        if self.transport not in TRANSPORTS:
            raise ConnectorError(
                tr('python.store.720fc4ccd4dc', p0=f'{self.name!r}', p1=f"{'/'.join(TRANSPORTS)}", p2=f'{self.transport!r}')
            )

    def to_mcp_config(self) -> McpServerConfig:
        """转换成 MCP 服务器配置（env / headers 做变量插值）。"""
        fields: dict[str, Any] = {
            "server_name": self.name,
            "transport": self.transport,
        }
        if self.transport == "stdio":
            fields["command"] = self.command or ""
            fields["args"] = list(self.args)
            fields["cwd"] = self.cwd
            fields["env"] = {k: interpolate(v) for k, v in self.env.items()}
        else:
            fields["url"] = self.url or ""
            fields["headers"] = {k: interpolate(v) for k, v in self.headers.items()}
        options = self.metadata.get('mcp_options', {})
        if 'tool_timeout_sec' in options:
            fields['tool_call_timeout_ms'] = int(options['tool_timeout_sec'] * 1000)
        if 'startup_timeout_sec' in options:
            fields['startup_timeout_ms'] = int(options['startup_timeout_sec'] * 1000)
        for key in ('enabled_tools', 'disabled_tools'):
            if key in options:
                fields[key] = list(options[key])
        return McpServerConfig(**fields)

    def to_dict(self) -> dict[str, Any]:
        """可 JSON 化的声明（不含插值后的凭据）。"""
        return {
            "name": self.name,
            "transport": self.transport,
            "command": self.command,
            "args": list(self.args),
            "env": dict(self.env),
            "cwd": self.cwd,
            "url": self.url,
            "headers": dict(self.headers),
            "enabled": self.enabled,
            "bound": self.bound,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ConnectorRecord":
        """只接受当前字段类型，不将损坏数据强制转换为有效声明。"""
        if not isinstance(data, dict):
            raise ConnectorError(tr('connectors.record.invalid'))
        for key in ('name', 'transport'):
            if key in data and not isinstance(data[key], str):
                raise ConnectorError(tr('connectors.record.invalid'))
        for key in ('command', 'cwd', 'url'):
            if key in data and data[key] is not None and not isinstance(data[key], str):
                raise ConnectorError(tr('connectors.record.invalid'))
        for key in ('enabled', 'bound'):
            if key in data and type(data[key]) is not bool:
                raise ConnectorError(tr('connectors.record.invalid'))
        args = data.get('args', [])
        if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
            raise ConnectorError(tr('connectors.record.invalid'))
        for key in ('env', 'headers'):
            value = data.get(key, {})
            if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
                raise ConnectorError(tr('connectors.record.invalid'))
        if not isinstance(data.get('metadata', {}), dict):
            raise ConnectorError(tr('connectors.record.invalid'))
        return cls(
            name=data.get('name', ''), transport=data.get('transport', 'stdio'),
            command=data.get('command'), args=list(args), env=dict(data.get('env', {})),
            cwd=data.get('cwd'), url=data.get('url'), headers=dict(data.get('headers', {})),
            enabled=data.get('enabled', True), bound=data.get('bound', False),
            metadata=dict(data.get('metadata', {})),
        )


class ConnectorStore:
    """连接器声明的持久化容器。

    :param home: ``HARNESS_HOME``；``None`` 表示纯内存（测试用）。
    """

    def __init__(self, home: str | os.PathLike[str] | None = None) -> None:
        self._path = Path(home).expanduser() / CONNECTORS_FILENAME if home is not None else None
        self._lock = threading.RLock()
        self._records: dict[str, ConnectorRecord] = {}
        self._loaded = False
        #: 插件能力是动态投影，不混入独立用户声明。
        self.plugin_provider: Any = None

    @contextmanager
    def locked(self):
        """在插件安装提交期间保持声明空间不被并发覆盖。"""
        with self._lock:
            self._load()
            yield

    # -- 持久化 ----------------------------------------------------------
    def _load(self) -> None:
        if self._loaded:
            return
        if self._path is None:
            self._loaded = True
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self._loaded = True
            return
        except (OSError, ValueError) as exc:
            raise ConnectorError(tr('connectors.state.invalid')) from exc
        if not isinstance(raw, dict) or type(raw.get('version')) is not int or raw['version'] != 1 or not isinstance(raw.get('connectors'), dict):
            raise ConnectorError(tr('connectors.state.invalid'))
        records = {}
        for name, item in raw['connectors'].items():
            if not isinstance(item, dict):
                raise ConnectorError(tr('connectors.state.invalid'))
            record = ConnectorRecord.from_dict({**item, 'name': str(name)})
            records[record.name] = record
        self._records = records
        self._loaded = True

    def _persist(self, records: dict[str, ConnectorRecord]) -> None:
        if self._path is None:
            return
        payload = {
            "version": 1,
            "connectors": {name: record.to_dict() for name, record in records.items()},
        }
        # 固定 UTF-8/LF 字节，避免共享发布器的文本模式在 Windows 转换换行。
        atomic_write(self._path, json.dumps(payload, ensure_ascii=False, indent=2).encode('utf-8'))

    # -- 创建、读取、更新和删除 -------------------------------------------
    def list(self) -> list[ConnectorRecord]:
        """列出全部连接器（按名称排序）。"""
        with self._lock:
            self._load()
            external = self.plugin_provider.list() if self.plugin_provider is not None else []
            if set(self._records) & {item.name for item in external}:
                raise ConnectorError(tr('config.validation.message_20'))
            return sorted([*self._records.values(), *external], key=lambda r: r.name)

    def is_plugin(self, name: str) -> bool:
        """判断实际能力来源，不信任用户 metadata 伪装所有者。"""
        with self._lock:
            self._load()
            return name not in self._records and self.plugin_provider is not None and any(item.name == name for item in self.plugin_provider.list())

    def get(self, name: str) -> ConnectorRecord:
        """按名取连接器。

        :raises ConnectorError: 连接器不存在。
        """
        with self._lock:
            self._load()
            record = self._records.get(name)
            if record is None and self.plugin_provider is not None:
                record = next((item for item in self.plugin_provider.list() if item.name == name), None)
            if record is None:
                available = ", ".join(sorted(self._records)) or tr('python.store.f4b39d9b4542')
                raise ConnectorError(
                    tr('python.store.9718df19a752', p0=f'{name!r}', p1=f'{available}')
                )
            return record

    def upsert(self, record: ConnectorRecord) -> ConnectorRecord:
        """保存完整声明；未重新绑定时保留已有绑定状态。"""
        with self._lock:
            record = replace(record)
            self._load()
            if self.plugin_provider is not None and any(item.name == record.name for item in self.plugin_provider.list()):
                raise ConnectorError(tr('config.validation.message_21'))
            existing = self._records.get(record.name)
            if existing is not None and not record.bound:
                record = replace(record, bound=existing.bound)
            candidate = {**self._records, record.name: record}
            self._persist(candidate)
            self._records = candidate
            return record

    def remove(self, name: str) -> None:
        """移除一台连接器声明。

        :raises ConnectorError: 连接器不存在。
        """
        with self._lock:
            self._load()
            if name not in self._records:
                raise ConnectorError(tr('python.store.42f4f2031a8f', p0=f'{name!r}'))
            candidate = dict(self._records)
            del candidate[name]
            self._persist(candidate)
            self._records = candidate

    def set_enabled(self, name: str, enabled: bool) -> ConnectorRecord:
        with self._lock:
            self._load()
            record = self.get(name)
            if name not in self._records and self.plugin_provider is not None:
                return self.plugin_provider.update(name, enabled=enabled)
            updated = replace(record, enabled=enabled)
            candidate = {**self._records, name: updated}
            self._persist(candidate)
            self._records = candidate
            return updated

    def set_bound(self, name: str, bound: bool) -> ConnectorRecord:
        with self._lock:
            self._load()
            record = self.get(name)
            if name not in self._records and self.plugin_provider is not None:
                return self.plugin_provider.update(name, bound=bound)
            updated = replace(record, bound=bound)
            candidate = {**self._records, name: updated}
            self._persist(candidate)
            self._records = candidate
            return updated


__all__ = [
    "ConnectorRecord",
    "ConnectorStore",
    "interpolate",
    "CONNECTORS_FILENAME",
]
