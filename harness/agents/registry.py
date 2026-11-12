"""Agent注册表与文件系统 provider。

与 :mod:`harness.skills.registry` 完全同构（这是刻意的设计对称：
读懂技能注册表就等于读懂Agent注册表）：

* **Service Definition**：:class:`AgentRegistry` 合并多个 provider 的目录、
  按名字解析出胜出Agent、暴露摘要与正文；
* **Service Provider**：:class:`FilesystemAgentProvider` 决定Agent来自哪些
  目录（用户级 ``~/.harness/agents`` 与项目级 ``<cwd>/.harness/agents``）。

同名冲突按 ``rank`` 升序解决：rank 小的胜出（项目Agent覆盖用户Agent）。
"""

from __future__ import annotations

from harness.i18n import tr

import dataclasses
import logging
from pathlib import Path
from typing import Callable, Iterable

from ..core.errors import AgentError, AgentNotFoundError, AgentParseError
from .parser import AgentDefinition, is_valid_agent_name, parse_agent_file

logger = logging.getLogger(__name__)

#: 项目级Agent（最高优先级）
PROJECT_AGENT_RANK = 100
#: 用户级Agent
USER_AGENT_RANK = 500


class AgentProvider:
    """Agent提供者基类。

    子类实现 :meth:`discover`，返回该来源的全部Agent。
    """

    name: str = "provider"

    def discover(self) -> list[AgentDefinition]:
        """发现该来源的全部Agent。"""
        raise NotImplementedError


class FilesystemAgentProvider(AgentProvider):
    """从文件系统目录发现Agent。

    每个Agent是一个子目录，里面有 ``AGENTS.md``。

    :param roots:       ``(目录, rank, source 标签)`` 三元组列表。
    :param provider_id: provider 标识（出现在 ``AgentDefinition.provider``）。
    """

    def __init__(
        self,
        roots: Iterable[tuple[Path, int, str]],
        *,
        provider_id: str = "filesystem",
    ) -> None:
        self.roots = [(Path(root), rank, label) for root, rank, label in roots]
        self.provider_id = provider_id

    def discover(self) -> list[AgentDefinition]:
        """扫描所有 root，收集 ``*/AGENTS.md``。

        单个Agent解析失败只记录警告并跳过，不影响其它Agent——
        一个坏Agent不应该让整个 agent 起不来（与技能同容错纪律）。
        """
        found: list[AgentDefinition] = []
        for root, rank, label in self.roots:
            if not root.exists() or not root.is_dir():
                continue
            for entry in sorted(root.iterdir()):
                agent_file = entry / "AGENTS.md"
                if not entry.is_dir() or not agent_file.exists():
                    continue
                try:
                    agent = parse_agent_file(
                        agent_file, source=label, provider=self.provider_id
                    )
                except AgentParseError as exc:
                    logger.warning(tr('log.75a56482de7a'), agent_file, exc)
                    continue
                # rank 决定同名冲突时谁胜出
                found.append(dataclasses.replace(agent, rank=rank))
        return found


class AgentRegistry:
    """Agent注册表：合并 provider、按名解析。

    用法::

        registry = AgentRegistry()
        registry.add_provider(FilesystemAgentProvider([...]))
        registry.refresh()             # 扫描
        catalog = registry.catalog()   # 给模型看的目录
        agent = registry.get("reviewer")  # 取完整定义
    """

    def __init__(self) -> None:
        self._providers: list[AgentProvider] = []
        #: name -> (rank, Agent)
        self._catalog: dict[str, tuple[int, AgentDefinition]] = {}
        self._registered: dict[str, AgentDefinition] = {}
        #: 可选的启用过滤器（ESC 生命周期管理用）。
        self._enabled_filter: Callable[[AgentDefinition], bool] | None = None

    def set_enabled_filter(self, predicate: Callable[[AgentDefinition], bool] | None) -> None:
        """设置启用过滤器（``None`` 清除）。

        过滤器只影响可见性（目录 / 解析），不改动注册表内部结构。
        """
        self._enabled_filter = predicate

    # -- provider 管理 --------------------------------------------------
    def add_provider(self, provider: AgentProvider) -> None:
        """注册一个 provider 并立即刷新。"""
        self._providers.append(provider)
        self.refresh()

    def refresh(self) -> None:
        """重新扫描所有 provider，重建目录。"""
        catalog: dict[str, tuple[int, AgentDefinition]] = {}
        for provider in self._providers:
            for agent in provider.discover():
                if not is_valid_agent_name(agent.name):
                    continue
                rank = agent.rank
                existing = catalog.get(agent.name)
                if existing is not None and rank == existing[0]:
                    logger.warning(tr('log.643b6c61a377'), agent.name, existing[1].path, agent.path)
                # rank 小的胜出；同 rank 时先注册的 provider 保持不动
                if existing is None or rank < existing[0]:
                    catalog[agent.name] = (rank, agent)
        for agent in self._registered.values():
            existing = catalog.get(agent.name)
            if existing is None or agent.rank < existing[0]:
                catalog[agent.name] = (agent.rank, agent)
        self._catalog = catalog

    # -- 动态注册 --------------------------------------------------------
    def register(self, agent: AgentDefinition) -> None:
        """直接注册一个已在内存中的Agent（不走文件系统扫描）。

        典型用途：Agent来自数据库、配置或代码常量（安装后立即生效）。
        已存在同名Agent时按 ``rank`` 决定谁胜出（rank 小者胜）。
        """
        if not is_valid_agent_name(agent.name):
            raise AgentError(
                tr('python.registry.9d8b97e64cb4', p0=f'{agent.name!r}'), agent=agent.name
            )
        registered = self._registered.get(agent.name)
        if registered is None or agent.rank < registered.rank:
            self._registered[agent.name] = agent
        existing = self._catalog.get(agent.name)
        if existing is None or agent.rank < existing[0]:
            self._catalog[agent.name] = (agent.rank, agent)

    def for_project(self, cwd: str | Path) -> AgentRegistry:
        """生成独立项目视图，保留被其他项目覆盖的用户Agent。"""
        scoped = AgentRegistry()
        scoped._enabled_filter = self._enabled_filter
        project_sources = {"project-harness", "project-agents"}
        for provider in self._providers:
            if isinstance(provider, FilesystemAgentProvider):
                scoped._providers.append(FilesystemAgentProvider(
                    [(root, rank, label) for root, rank, label in provider.roots if label not in project_sources],
                    provider_id=provider.provider_id,
                ))
            else:
                scoped._providers.append(provider)
        scoped._registered = {name: agent for name, agent in self._registered.items() if agent.source not in project_sources}
        base = Path(cwd)
        scoped._providers.append(FilesystemAgentProvider([
            (base / ".harness" / "agents", PROJECT_AGENT_RANK, "project-harness"),
            (base / ".agents" / "agents", PROJECT_AGENT_RANK + 50, "project-agents"),
        ]))
        scoped.refresh()
        return scoped

    # -- 查询 ------------------------------------------------------------
    def list_all(self) -> list[AgentDefinition]:
        """列出全部已注册Agent（**忽略启用过滤器**，管理界面用）。"""
        return sorted(
            (agent for _, agent in self._catalog.values()), key=lambda e: e.name
        )

    def list(self) -> list[AgentDefinition]:
        """列出全部Agent（按名称排序，启用过滤器生效）。"""
        agents = [agent for _, agent in self._catalog.values()]
        if self._enabled_filter is not None:
            agents = [e for e in agents if self._enabled_filter(e)]
        return sorted(agents, key=lambda e: e.name)

    def has(self, name: str) -> bool:
        """检查原始目录的注册；启用可见性由 list/get 判断。"""
        return name in self._catalog

    def get(self, name: str) -> AgentDefinition:
        """按名字取Agent（含正文；停用的Agent视同不存在）。

        :raises AgentNotFoundError: Agent不存在或已停用。
        """
        entry = self._catalog.get(name)
        blocked = False
        if entry is not None and self._enabled_filter is not None:
            blocked = not self._enabled_filter(entry[1])
        if entry is None or blocked:
            available = ", ".join(agent.name for agent in self.list()) or tr('python.registry.f4b39d9b4542')
            reason = tr('python.registry.a8c3698b5b8c') if blocked else tr('python.registry.4e51b67dd32c')
            raise AgentNotFoundError(
                tr('python.registry.cb8d3978ce07', p0=f'{name!r}', p1=f'{reason}', p2=f'{available}'), agent=name
            )
        return entry[1]

    def catalog(self, *, max_description: int = 500) -> str:
        """渲染给模型看的 ``<available_agents>`` 目录文本。"""
        agents = self.list()
        if not agents:
            return tr('python.registry.e349a655cb9a')
        return "\n".join(
            agent.summary_line(max_description=max_description) for agent in agents
        )

    def entries(self) -> list[dict[str, str]]:
        """目录的机器可读形式。"""
        return [
            {"name": e.name, "description": e.description, "agent_type": e.agent_type}
            for e in self.list()
        ]

    def __len__(self) -> int:
        return len(self._catalog)

    def __repr__(self) -> str:
        return f"<AgentRegistry agents={len(self._catalog)} providers={len(self._providers)}>"


def build_default_agent_registry(
    *,
    cwd: str | None = None,
    home: str | None = None,
    extra_roots: Iterable[tuple[Path, int, str]] = (),
) -> AgentRegistry:
    """构造默认的Agent注册表。

    按 rank 从小到大搜索（rank 小的优先级高）：

    1. ``extra_roots``         —— 调用方自定义（优先级最高）
    2. ``<cwd>/.harness/agents`` —— 项目自定义
    3. ``<cwd>/.agents/agents``   —— 项目共享声明目录
    4. ``~/.harness/agents``     —— 用户级
    """
    roots: list[tuple[Path, int, str]] = list(extra_roots)
    if cwd:
        base = Path(cwd)
        roots.append((base / ".harness" / "agents", PROJECT_AGENT_RANK, "project-harness"))
        roots.append((base / ".agents" / "agents", PROJECT_AGENT_RANK + 50, "project-agents"))
    user_root = Path(home).expanduser() if home else Path.home() / ".harness"
    roots.append((user_root / "agents", USER_AGENT_RANK, "user-harness"))

    registry = AgentRegistry()
    registry.add_provider(FilesystemAgentProvider(roots))
    return registry


__all__ = [
    "AgentRegistry",
    "AgentProvider",
    "FilesystemAgentProvider",
    "build_default_agent_registry",
    "PROJECT_AGENT_RANK",
    "USER_AGENT_RANK",
]
