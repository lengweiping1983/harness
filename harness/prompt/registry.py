"""系统提示词注册表。

核心机制：

* **有序片段（section）**：每个贡献者注册一段带 ``order`` 的文本，
  装配时按 ``order`` 升序拼接（相同 order 按名称字典序，保证确定性）；
* **上下文（context）**：运行时动态信息（沙箱策略、技能目录、todo 提示…），
  渲染成 system 段但语义上是"当前环境说明"；
* **变量插值**：``{{name}}`` 占位符在渲染时替换；
* **complete 片段**：某个片段可声明自己是"完整系统提示"，装配时其它片段被丢弃
  ——业务 Agent 用它一键替换整套人格。

这一层是"业务 Agent 可扩展性"的关键：接入方只需注册自己的 section，
不必修改 harness 任何代码。
"""

from __future__ import annotations

from harness.i18n import tr

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from ..core.errors import ConfigError

#: 预置的章节顺序常量
ORDER_IDENTITY = -1000        # harness 身份
ORDER_PERSONA_PREFIX = 0      # 部署人格前缀
ORDER_PLAN_POLICY = 500       # 计划模式策略
ORDER_ENVIRONMENT = 100       # 运行环境
ORDER_TOOL_READ = 1100        # read 工具策略
ORDER_TOOL_WRITE = 1200       # write 工具策略
ORDER_TOOL_EDIT = 1300        # edit 工具策略
ORDER_TOOL_GLOB = 1400        # glob 工具策略
ORDER_TOOL_GREP = 1500        # grep 工具策略
ORDER_TOOL_JOBS = 1600        # 后台作业策略
ORDER_TOOL_LSP = 2200         # lsp 工具策略
ORDER_TOOL_SESSION_QUERY = 2300  # 会话检索策略
ORDER_TOOL_GOAL = 2400        # goal 工具策略
ORDER_POLICY = 2000           # 通用行为策略
ORDER_SKILL_CATALOG = 3200    # 技能目录
ORDER_TOOLS = 5000            # 工具说明
ORDER_CONTACT = 10000         # 本地路径等
ORDER_PERSONA_SUFFIX = 10200  # 部署人格后缀

#: 一个完整的 {{...}} 引用
VARIABLE_REF_RE = re.compile(r"\{\{\s*([a-z][a-z0-9_]*)\s*\}\}")


@dataclass(frozen=True, slots=True)
class PromptSection:
    """一个系统提示词片段。

    :param name:        唯一名字，重复注册会报错。
    :param order:       排序权重，越小越靠前。
    :param text:        静态文本，或一个 ``(variables) -> str`` 的动态提供函数。
    :param interpolate: 是否对 ``{{var}}`` 做插值，默认 True。
    :param complete:    声明本片段是完整系统提示（会屏蔽其它片段）。
    """

    name: str
    order: int
    text: str | Callable[[dict[str, str]], str]
    interpolate: bool = True
    complete: bool = False
    #: 运行状态片段：请求时放在历史之后，保护稳定前缀。
    volatile: bool = False

    def resolve(self, variables: dict[str, str]) -> str:
        """解析出最终文本。"""
        if callable(self.text):
            return self.text(variables)
        return self.text


@dataclass
class PromptAssembly:
    """一次装配的结果。"""

    sections: list[tuple[str, str]] = field(default_factory=list)
    contexts: list[tuple[str, str]] = field(default_factory=list)
    variables: dict[str, str] = field(default_factory=dict)
    tool_names: list[str] = field(default_factory=list)
    volatile_names: set[str] = field(default_factory=set)

    def render(self) -> str:
        """渲染成最终的 system 提示文本。

        空片段自动跳过，各段之间用空行分隔。
        """
        parts: list[str] = []
        for _, text in self.sections:
            if text.strip():
                parts.append(text.strip())
        for _, text in self.contexts:
            if text.strip():
                parts.append(text.strip())
        return "\n\n".join(parts)

    def cache_parts(self, *, final_runtime_names: Iterable[str] = ()) -> tuple[str, str]:
        """稳定指令放在前面，当前运行状态作为请求后缀。"""
        final_names = frozenset(final_runtime_names)
        stable = [text.strip() for name, text in self.sections if name not in self.volatile_names and text.strip()]
        dynamic = [text.strip() for name, text in self.sections
                   if name in self.volatile_names and name not in final_names and text.strip()]
        dynamic.extend(text.strip() for _, text in self.contexts if text.strip())
        dynamic.extend(text.strip() for name, text in self.sections
                       if name in self.volatile_names and name in final_names and text.strip())
        return '\n\n'.join(stable), '\n\n'.join(dynamic)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sections": [{"name": n, "chars": len(t)} for n, t in self.sections],
            "contexts": [{"name": n, "chars": len(t)} for n, t in self.contexts],
            "variables": dict(self.variables),
            "tools": list(self.tool_names),
        }


class SystemPromptRegistry:
    """系统提示词注册表。"""

    def __init__(self) -> None:
        self._sections: dict[str, PromptSection] = {}
        self._contexts: dict[str, tuple[int, str | Callable[[dict[str, str]], str]]] = {}

    # -- 注册 ------------------------------------------------------------
    def section(self, section: PromptSection) -> PromptSection:
        """注册一个提示词片段。"""
        if section.name in self._sections:
            raise ConfigError(tr('python.registry.f41fa90bb3ac', p0=f'{section.name!r}'))
        self._sections[section.name] = section
        return section

    def add_section(
        self,
        name: str,
        text: str | Callable[[dict[str, str]], str],
        *,
        order: int = ORDER_POLICY,
        interpolate: bool = True,
        complete: bool = False,
        volatile: bool = False,
    ) -> PromptSection:
        """便捷注册：直接传字符串或动态提供函数。"""
        return self.section(
            PromptSection(name=name, order=order, text=text, interpolate=interpolate, complete=complete, volatile=volatile)
        )

    def context(
        self,
        name: str,
        text: str | Callable[[dict[str, str]], str],
        *,
        order: int = 100,
    ) -> None:
        """注册一段运行时上下文（环境说明、策略提示等）。"""
        if name in self._contexts:
            raise ConfigError(tr('python.registry.3aeafa2504e7', p0=f'{name!r}'))
        self._contexts[name] = (order, text)

    def replace_section(
        self,
        name: str,
        text: str | Callable[[dict[str, str]], str],
        *,
        order: int | None = None,
        interpolate: bool | None = None,
        complete: bool | None = None,
        volatile: bool | None = None,
    ) -> PromptSection:
        """替换已注册片段的内容（保留 name，order 等未显式给出的字段）。"""
        current = self._sections.get(name)
        if current is None:
            raise ConfigError(tr('python.registry.85f6c574177b', p0=f'{name!r}'))
        updated = PromptSection(
            name=name,
            order=current.order if order is None else order,
            text=text,
            interpolate=current.interpolate if interpolate is None else interpolate,
            complete=current.complete if complete is None else complete,
            volatile=current.volatile if volatile is None else volatile,
        )
        self._sections[name] = updated
        return updated

    def unregister(self, name: str) -> None:
        """移除一个片段。"""
        self._sections.pop(name, None)
        self._contexts.pop(name, None)

    def clear(self) -> None:
        """清空所有注册。"""
        self._sections.clear()
        self._contexts.clear()

    # -- 装配 ------------------------------------------------------------
    def assemble(
        self,
        *,
        variables: dict[str, str] | None = None,
        extra_sections: Iterable[PromptSection] = (),
    ) -> PromptAssembly:
        """在本次装配语言上下文解析提供函数，退出后恢复调用方语言。"""
        from ..core.language import current_language
        language = (variables or {}).get('language')
        if language is None:
            return self._assemble(variables=variables, extra_sections=extra_sections)
        with current_language.set(language):
            return self._assemble(variables=variables, extra_sections=extra_sections)

    def _assemble(
        self,
        *,
        variables: dict[str, str] | None = None,
        extra_sections: Iterable[PromptSection] = (),
    ) -> PromptAssembly:
        """装配出一次完整的系统提示。

        :param variables:      本次渲染可用的变量。
        :param extra_sections: 临时追加的片段（例如按会话定制的人格）。
        :raises ConfigError: 注册了多于一个 ``complete`` 片段。
        """
        variables = dict(variables or {})
        sections = list(self._sections.values()) + list(extra_sections)

        # 1) 找出 complete 片段（多于一个直接报错——语义冲突）
        completes = [s for s in sections if s.complete]
        if len(completes) > 1:
            raise ConfigError(tr('python.registry.b926cefb62fa') + ", ".join(s.name for s in completes))

        # 2) 排序：order 升序，同 order 按 name 字典序（跨机器确定）
        ordered = sorted(sections, key=lambda s: (s.order, s.name))
        if completes:
            ordered = completes

        assembly = PromptAssembly(variables=variables)
        for section in ordered:
            text = section.resolve(variables)
            if section.interpolate:
                text = interpolate_variables(text, variables)
            assembly.sections.append((section.name, text))
            if section.volatile:
                assembly.volatile_names.add(section.name)

        for name, (order, source) in sorted(self._contexts.items(), key=lambda kv: (kv[1][0], kv[0])):
            text = source(variables) if callable(source) else source
            assembly.contexts.append((name, interpolate_variables(text, variables)))

        return assembly

    def render(self, **variables: Any) -> str:
        """便捷方法：一次性装配并渲染。"""
        return self.assemble(variables={k: str(v) for k, v in variables.items()}).render()

    def __len__(self) -> int:
        return len(self._sections)


def interpolate_variables(text: str, variables: dict[str, str]) -> str:
    """把 ``{{name}}`` 替换成变量值。

    未定义的变量保留原样（便于模型看到"这里本该有个值"的信号），
    而不是替换成空串导致语义丢失。
    """

    def replace(match: re.Match[str]) -> str:
        return variables.get(match.group(1), match.group(0))

    return VARIABLE_REF_RE.sub(replace, text)


def _build_base_prompt(
    registry: SystemPromptRegistry,
    *,
    agent_name: str = "Harness Agent",
    description: str = "",
    working_dir: str | None = None,
    platform: str | None = None,
    is_win: bool | None = None,
    today: str | None = None,
    language: str | None = None,
) -> SystemPromptRegistry:
    """注册 harness 的基础提示词片段。

    这是"最小可用人格"：身份 + 环境 + 行为准则。业务方在此之上
    通过 :meth:`SystemPromptRegistry.add_section` 追加自己的内容，
    或注册一个 ``complete=True`` 的片段整体替换。

    :param platform: 目标操作系统名；`None` 表示自动探测当前平台。
    :param is_win: 是否按 Windows 语义生成 shell 提示；`None` 表示跟随 `platform`。
    """
    if language is None:
        language = tr('language.display_name')

    import os
    import platform as _platform
    from datetime import date

    # 不能硬编码平台：写死 linux 会让模型在 Windows 上发出 pwd && ls -la
    # 这类错误 shell 语法，多花一次工具调用才能自我纠正。
    if platform is None:
        platform = _platform.system() or "unknown"
    if is_win is None:
        is_win = platform.lower().startswith("win")

    registry.add_section(
        "harness:identity",
        tr('python.registry.2ba085089a2d', p0=f'{agent_name}', p1=f'{description}'),
        order=ORDER_IDENTITY,
    )

    if is_win:
        # Windows shell 入口显式使用 COMSPEC，即 cmd.exe，
        # 因此这里必须写 cmd 语法；PowerShell 需要显式包一层。
        shell_label = "Windows: cmd.exe (COMSPEC)"
        shell_rules = [
            tr('python.registry.7e43a4be1fcb'),
            tr('python.registry.140e4b5b63ba'),
            tr('python.registry.23d2708c13c1'),
            tr('python.registry.487f0d02ab4c'),
            tr('python.registry.48a125039c85'),
            tr('python.registry.d2dc5be4424c'),
            tr('python.registry.51e494949fb4'),
        ]
    else:
        shell_label = "POSIX shell (sh/bash/zsh)"
        shell_rules = [
            tr('python.registry.b3e0c0da8473'),
            tr('python.registry.e7ff3807e486'),
        ]
    env_lines = [
        tr('python.registry.d61b366b4f64', p0=f'{working_dir or os.getcwd()}'),
        tr('python.registry.734cd1435c52', p0=f'{platform}'),
        tr('python.registry.c3846fb65ef0', p0=f'{shell_label}'),
        tr('python.registry.0704d65ea7c6', p0=f'{today or date.today().isoformat()}'),
        tr('python.registry.9469ddea2a2b', p0=f'{language}'),
    ]
    if shell_rules:
        env_lines.append("")
        env_lines.append("  <shell_rules>")
        env_lines.extend(f"  - {rule}" for rule in shell_rules)
        env_lines.append("  </shell_rules>")

    def environment(variables: dict[str, str]) -> str:
        # 工具在会话工作目录运行，该目录可能与服务端启动目录不同。
        # 每次请求单独解析，不修改共享状态。
        lines = list(env_lines)
        lines[0] = tr('python.registry.d61b366b4f64', p0=f"{variables.get('cwd') or working_dir or os.getcwd()}")
        lines.insert(1, tr('python.registry.d65adac8b2b2'))
        lines.insert(2, tr('prompt.artifact_output'))
        return "<environment>\n" + "\n".join(lines) + "\n</environment>"

    registry.add_section(
        "harness:environment",
        environment,
        order=ORDER_ENVIRONMENT,
        volatile=True,
        interpolate=False,  # 目录名中的 {{...}} 按字面量处理。
    )

    registry.add_section(
        "harness:behavior",
        tr('python.registry.cfc49e334456', p0=f'{language}'),
        order=ORDER_POLICY,
    )
    return registry


def build_base_prompt(registry: SystemPromptRegistry, **kwargs) -> SystemPromptRegistry:
    """按回合解析基础模板，不冻结语言或修改共享注册表。"""
    from ..core.language import current_language, resolve_language
    initial = SystemPromptRegistry()
    _build_base_prompt(initial, **kwargs)
    for section in initial._sections.values():
        def provider(variables, name=section.name):
            actual = variables.get('language') or resolve_language(kwargs.get('language', 'zh-CN')).effective_language
            token = current_language.set(actual)
            try:
                temporary = SystemPromptRegistry()
                options = {**kwargs, 'language': tr('language.display_name')}
                _build_base_prompt(temporary, **options)
                return temporary._sections[name].resolve(variables)
            finally:
                current_language.reset(token)
        registry.add_section(section.name, provider, order=section.order,
                             interpolate=section.interpolate, volatile=section.volatile)
    from ..i18n import resources
    registry.add_section('harness:language', lambda variables: resources(variables.get('language') or resolve_language(kwargs.get('language', 'zh-CN')).effective_language)['language.output_policy'], order=ORDER_POLICY + 1, volatile=True)
    registry.add_section('harness:program_verification', lambda variables: resources(variables.get('language') or resolve_language(kwargs.get('language', 'zh-CN')).effective_language)['prompt.program_verification'], order=ORDER_POLICY + 2)
    return registry


__all__ = [
    "PromptSection",
    "PromptAssembly",
    "SystemPromptRegistry",
    "interpolate_variables",
    "build_base_prompt",
    "ORDER_IDENTITY",
    "ORDER_PERSONA_PREFIX",
    "ORDER_ENVIRONMENT",
    "ORDER_TOOLS",
    "ORDER_SKILL_CATALOG",
    "ORDER_POLICY",
    "ORDER_CONTACT",
    "ORDER_PERSONA_SUFFIX",
]
