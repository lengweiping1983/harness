"""宿主装配的执行环境边界；进程回收不代表文件或网络隔离。"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import sys
from typing import Protocol

from ..tools.owned_process import spawn_owned


@dataclass(frozen=True)
class ExecutionCapabilities:
    """能力来自实现声明；实际平台验收需要独立证据。"""

    provider: str
    platform: str
    process_cleanup: str
    filesystem_isolation: str = 'host'
    network_isolation: str = 'host'
    credentials: str = 'caller_supplied'
    evidence: str = 'implementation'

    def to_dict(self):
        """返回独立快照，不向调用方暴露可变内部对象。"""
        return asdict(self)


class ExecutionEnvironment(Protocol):
    """环境只能由可信宿主选择，工具参数不能切换提供器。"""

    def capabilities(self) -> ExecutionCapabilities: ...

    async def spawn(self, argv: list[str], *, cwd: str, env: dict[str, str], shell: bool = False): ...


class LocalExecutionEnvironment:
    """继续使用现有所属进程实现，完整保留取消及清理异常。"""

    def capabilities(self) -> ExecutionCapabilities:
        mechanism = {'win32': 'windows-job', 'linux': 'linux-owned-group', 'darwin': 'darwin-owned-group'}.get(sys.platform, 'unavailable')
        return ExecutionCapabilities('local-owned', sys.platform, mechanism)

    async def spawn(self, argv: list[str], *, cwd: str, env: dict[str, str], shell: bool = False):
        return await spawn_owned(argv, cwd=cwd, env=env, shell=shell)
