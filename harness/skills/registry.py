"""文件系统技能目录：用户与项目共享 .agents 和系统 .harness。

注册表按作用域与来源优先级解析技能，内容统一来自 SKILL.md。
"""

from __future__ import annotations

from harness.i18n import tr

import dataclasses
import logging
from pathlib import Path
from typing import Callable, Iterable

from ..core.errors import SkillError, SkillNotFoundError, SkillParseError
from .parser import SkillMetadata, is_valid_skill_name, parse_skill_file
from .paths import SYSTEM_SKILL_ROOT, user_skill_root

logger = logging.getLogger(__name__)

#: 内置/打包技能的 rank
SYSTEM_SKILL_RANK = 600
#: 用户级技能
USER_SKILL_RANK = 500
#: 项目级技能（最高优先级）
PROJECT_SKILL_RANK = 100


def _registered_name_valid(skill: SkillMetadata) -> bool:
    """独立技能保持标准名称，只有已声明所有者的插件可以导出限定名。"""
    if skill.plugin_id is None:
        return is_valid_skill_name(skill.name)
    owner, separator, name = skill.name.partition(':')
    return bool(separator and owner == skill.plugin_id and is_valid_skill_name(owner) and is_valid_skill_name(name))


class SkillProvider:
    """技能提供者基类。

    子类实现 :meth:`discover`，返回该来源的全部技能。
    """

    name: str = "provider"

    def discover(self) -> list[SkillMetadata]:
        """发现该来源的全部技能。"""
        raise NotImplementedError


class FilesystemSkillProvider(SkillProvider):
    """从文件系统目录发现技能。

    每个技能是一个子目录，里面有 ``SKILL.md``。

    :param roots:       ``(目录, rank, source 标签)`` 三元组列表。
    :param provider_id: provider 标识（出现在 ``SkillMetadata.provider``）。
    """

    def __init__(
        self,
        roots: Iterable[tuple[Path, int, str]],
        *,
        provider_id: str = "filesystem",
    ) -> None:
        self.roots = [(Path(root), rank, label) for root, rank, label in roots]
        self.provider_id = provider_id

    def discover(self) -> list[SkillMetadata]:
        """扫描所有 root，收集 ``*/SKILL.md``。

        单个技能解析失败只记录警告并跳过，不影响其它技能——
        一个坏技能不应该让整个 agent 起不来。
        """
        found: list[SkillMetadata] = []
        for root, rank, label in self.roots:
            if not root.exists() or not root.is_dir():
                continue
            for entry in sorted(root.iterdir()):
                skill_file = entry / "SKILL.md"
                if not entry.is_dir() or not skill_file.exists():
                    continue
                try:
                    skill = parse_skill_file(skill_file, source=label, provider=self.provider_id)
                except SkillParseError as exc:
                    logger.warning(tr('log.291f0ea00b95'), skill_file, exc)
                    continue
                # rank 决定同名冲突时谁胜出
                found.append(dataclasses.replace(skill, rank=rank))
        return found


class SkillRegistry:
    """技能注册表：合并 provider、按名解析。

    用法::

        registry = SkillRegistry()
        registry.add_provider(FilesystemSkillProvider([...]))
        registry.refresh()             # 扫描
        catalog = registry.catalog()   # 给模型看的目录
        skill = registry.get("pdf")    # 加载正文
    """

    def __init__(self) -> None:
        self._providers: list[SkillProvider] = []
        #: name -> (rank, 技能)
        self._catalog: dict[str, tuple[int, SkillMetadata]] = {}
        self._registered: dict[str, SkillMetadata] = {}
        #: 可选的启用过滤器（ESC 生命周期管理用）：
        #: 返回 False 的技能不出现在目录里、也不允许被加载。
        self._enabled_filter: Callable[[SkillMetadata], bool] | None = None

    def set_enabled_filter(self, predicate: Callable[[SkillMetadata], bool] | None) -> None:
        """设置启用过滤器（``None`` 清除）。

        过滤器只影响 :meth:`list` / :meth:`catalog` / :meth:`get` 的可见性，
        不改动注册表内部结构——停用是"看不见"，不是"删掉"。
        """
        self._enabled_filter = predicate

    # -- provider 管理 --------------------------------------------------
    def add_provider(self, provider: SkillProvider) -> None:
        """注册一个 provider 并立即刷新。"""
        self._providers.append(provider)
        self.refresh()

    def refresh(self) -> None:
        """重新扫描所有 provider，重建目录。"""
        catalog: dict[str, tuple[int, SkillMetadata]] = {}
        for provider in self._providers:
            for skill in provider.discover():
                if not _registered_name_valid(skill):
                    continue
                rank = skill.rank
                existing = catalog.get(skill.name)
                # rank 小的胜出；同 rank 时先注册的 provider 保持不动
                if existing is None or rank < existing[0]:
                    catalog[skill.name] = (rank, skill)
        for skill in self._registered.values():
            existing = catalog.get(skill.name)
            if existing is None or skill.rank < existing[0]:
                catalog[skill.name] = (skill.rank, skill)
        self._catalog = catalog

    # -- 动态注册 --------------------------------------------------------
    def register(self, skill: SkillMetadata) -> None:
        """直接注册一个已在内存中的技能（不走文件系统扫描）。

        典型用途：技能来自数据库、配置或代码常量。已存在同名技能时按
        :attr:`SkillMetadata.rank` 决定谁胜出（rank 小者胜）。
        """
        if not _registered_name_valid(skill):
            raise SkillError(tr('python.registry.08af7e15508a', p0=f'{skill.name!r}'), skill=skill.name)
        registered = self._registered.get(skill.name)
        if registered is None or skill.rank < registered.rank:
            self._registered[skill.name] = skill
        existing = self._catalog.get(skill.name)
        if existing is None or skill.rank < existing[0]:
            self._catalog[skill.name] = (skill.rank, skill)

    def for_project(self, cwd: str | Path, *, fallback_root: str | Path | None = None) -> SkillRegistry:
        """生成独立项目视图，保留被其他项目覆盖的用户与内置技能。"""
        scoped = SkillRegistry()
        scoped._enabled_filter = self._enabled_filter
        project_sources = {"project-harness", "project-agents"}
        for provider in self._providers:
            if isinstance(provider, FilesystemSkillProvider):
                scoped._providers.append(FilesystemSkillProvider(
                    [(root, rank, label) for root, rank, label in provider.roots if label not in project_sources],
                    provider_id=provider.provider_id,
                ))
            else:
                scoped._providers.append(provider)
        scoped._registered = {name: skill for name, skill in self._registered.items() if skill.source not in project_sources}
        from .scope import project_directories
        # 同级用户安装优先于系统技能；较近目录优先于祖先。
        scoped._providers.append(FilesystemSkillProvider([
            (base / kind / "skills", PROJECT_SKILL_RANK + depth * 10 + offset, label)
            for depth, base in enumerate(project_directories(cwd, fallback_root))
            for offset, (kind, label) in enumerate(((".agents", "project-agents"), (".harness", "project-harness")))
        ]))
        scoped.refresh()
        return scoped

    # -- 查询 ------------------------------------------------------------
    def with_enabled_filter(self, predicate: Callable[[SkillMetadata], bool]) -> SkillRegistry:
        """共享只读目录快照，独立设置本次使用者的过滤器，不改写全局启停。"""
        scoped = SkillRegistry()
        scoped._catalog = dict(self._catalog)
        scoped._enabled_filter = predicate
        return scoped

    def list_all(self) -> list[SkillMetadata]:
        """列出全部已注册技能（**忽略启用过滤器**，管理界面用）。"""
        skills = [skill for _, skill in self._catalog.values()]
        return sorted(skills, key=lambda s: s.name)

    def list(self, *, model_invocable_only: bool = False) -> list[SkillMetadata]:
        """列出全部技能（按名称排序，启用过滤器生效）。"""
        skills = [skill for _, skill in self._catalog.values()]
        if model_invocable_only:
            skills = [s for s in skills if s.model_invocable and not s.configuration_errors
                      and s.policy.get('allow_implicit_invocation', True) is True]
        if self._enabled_filter is not None:
            skills = [s for s in skills if self._enabled_filter(s)]
        return sorted(skills, key=lambda s: s.name)

    def has(self, name: str) -> bool:
        """检查原始目录的注册；启用可见性由 list/get 判断。"""
        return name in self._catalog

    def get(self, name: str) -> SkillMetadata:
        """按名字取技能（含正文；停用的技能视同不存在）。

        :raises SkillNotFoundError: 技能不存在或已停用。
        """
        entry = self._catalog.get(name)
        blocked = False
        if entry is not None and self._enabled_filter is not None:
            blocked = not self._enabled_filter(entry[1])
        if entry is None or blocked:
            available = ", ".join(skill.name for skill in self.list()) or tr('python.registry.f4b39d9b4542')
            reason = tr('python.registry.a8c3698b5b8c') if blocked else tr('python.registry.4e51b67dd32c')
            raise SkillNotFoundError(
                tr('python.registry.43e3300e2843', p0=f'{name!r}', p1=f'{reason}', p2=f'{available}'), skill=name
            )
        return entry[1]

    def catalog(self, *, max_description: int = 500) -> str:
        """渲染给模型看的 ``<available_skills>`` 目录文本。"""
        skills = self.list(model_invocable_only=True)
        if not skills:
            return tr('python.registry.6d3f6aa69e39')
        lines = [skill.summary_line(max_description=max_description) for skill in skills]
        return "\n".join(lines)

    def entries(self) -> list[dict[str, str]]:
        """目录的机器可读形式（供落进会话事件）。"""
        return [{"name": s.name, "description": s.description} for s in self.list()]

    def __len__(self) -> int:
        return len(self._catalog)

    def __repr__(self) -> str:
        return f"<SkillRegistry skills={len(self._catalog)} providers={len(self._providers)}>"


def build_default_registry(
    *,
    cwd: str | None = None,
    home: str | None = None,
    extra_roots: Iterable[tuple[Path, int, str]] = (),
    include_system: bool = True,
) -> SkillRegistry:
    """按优先级组合项目、用户、运行根及随包技能来源。

    项目同时发现 .agents/skills 与 .harness/skills；安装目标由 paths 定义。
    目录发现不创建、搬移或删除来源文件。
    """
    roots: list[tuple[Path, int, str]] = list(extra_roots)
    if cwd:
        from .scope import project_directories
        for depth, base in enumerate(project_directories(cwd)):
            for offset, (kind, label) in enumerate(((".agents", "project-agents"), (".harness", "project-harness"))):
                roots.append((base / kind / "skills", PROJECT_SKILL_RANK + depth * 10 + offset, label))
    user_root = Path(home).expanduser() if home else Path.home() / ".harness"
    roots.append((user_skill_root(home), USER_SKILL_RANK, "user-agents"))
    roots.append((user_root / "skills", SYSTEM_SKILL_RANK, "system-harness"))
    if include_system and SYSTEM_SKILL_ROOT.is_dir():
        roots.append((SYSTEM_SKILL_ROOT, SYSTEM_SKILL_RANK + 200, "system-harness"))

    registry = SkillRegistry()
    registry.add_provider(FilesystemSkillProvider(roots))
    if home is not None:
        from ..plugins.packages import PackageStore
        from ..plugins.provider import PackageSkillProvider
        packages = PackageStore(home)
        registry.add_provider(PackageSkillProvider(packages))
        registry.set_enabled_filter(lambda skill: skill.plugin_id is None or packages.is_enabled(skill.plugin_id))
    return registry


__all__ = [
    "SkillRegistry",
    "SkillProvider",
    "FilesystemSkillProvider",
    "build_default_registry",
    "SYSTEM_SKILL_RANK",
    "USER_SKILL_RANK",
    "PROJECT_SKILL_RANK",
]
