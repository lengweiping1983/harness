"""Harness 配置。

配置来源优先级（后者覆盖前者）：

1. 内置默认值；
2. 环境变量（``HARNESS_*``）；
3. 已保存的非敏感运行设置；
4. 显式传入的覆盖项（可选字段的 None 表示清空）。

单模型凭据从环境变量或显式参数读取。全局模型路由配置单独保存在
本地 ``connection-secrets.json``，普通设置与 UI 快照不包含原始密钥。
"""

from __future__ import annotations

from harness.llm.context_policy import DEFAULT_WORKING_BUDGET

from harness.i18n import tr

import math
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from ..llm.adapter import DEFAULT_BASE_URL, DEFAULT_MODEL
from ..llm.capabilities import positive
from ..llm.key_pool import parse_key_list
from ..tools.builtin.mcp_client import McpError, McpServerConfig

#: 默认数据目录
DEFAULT_HOME = Path.home() / ".harness"
#: 默认派生深度上限；根会话直接派生的成员深度为 1。
#: 有界默认值让深层递归派生失败可见，而不是无界膨胀后才在资源上崩溃。
DEFAULT_MAX_SUBAGENT_DEPTH = 8


def _default_owned_processes() -> bool:
    """支持固定组长身份的平台默认启用所属执行器。"""
    return os.name == 'nt' or sys.platform.startswith('linux') or sys.platform == 'darwin'


class _DefaultLanguage(str):
    """区分默认 auto 和调用方显式指定的 auto。"""


@dataclass
class HarnessConfig:
    """Harness 的全局配置。"""

    #: OpenRouter API 密钥
    api_key: str = ""
    #: OpenRouter API 密钥轮换池（有序，1 号在最前）。
    #: 非空时启用多 key 故障转移：报错标记当日不可用并自动切下一把。
    api_keys: list[str] = field(default_factory=list)
    #: 按序排列的模型和凭据配置；公开序列化结果不包含密钥。
    model_routes: list[dict] = field(default_factory=list, repr=False)
    #: OpenRouter 端点
    base_url: str = DEFAULT_BASE_URL
    #: 模型（默认走 OpenRouter 上的免费模型，可用 HARNESS_MODEL 覆盖）
    model: str = DEFAULT_MODEL
    #: 数据目录（会话持久化、设置等）
    home: Path = field(default_factory=lambda: DEFAULT_HOME)
    #: 默认工作目录
    cwd: Path | None = None
    #: 思考强度：low / medium / high（映射到 OpenRouter 的 reasoning.effort）
    reasoning_effort: str | None = None
    #: 温度
    temperature: float | None = None
    summary_max_tokens: int | None = 8192
    context_auto_manage: bool = True
    context_working_budget: int = DEFAULT_WORKING_BUDGET
    #: 工具结果首次进入模型的总字符预算；原始结果留档，None 显式关闭。
    tool_result_max_chars: int | None = 12000
    tool_schema_mode: str = 'auto'
    model_context_window: int | None = None
    model_max_output_tokens: int | None = None
    network_timeout: float = 180.0
    #: 持久会话分批保存模型流检查点，意外退出后恢复已提交片段。
    enable_stream_checkpoints: bool = True
    #: auto 启用已知端点缓存；off 不自动注入缓存参数。
    prompt_cache_mode: str = "auto"
    #: 瞬时错误重试次数
    max_retries: int = 3
    #: 是否启用文件工具
    enable_fs: bool = True
    #: 是否启用 shell 工具
    enable_shell: bool = True
    #: 所属进程执行器；Windows 原子 Job 关联，Linux/macOS 固定组长身份。
    enable_owned_processes: bool = field(default_factory=_default_owned_processes, kw_only=True)
    #: 是否启用 todo 工具
    enable_todo: bool = True
    #: 是否启用 goal 目标管理子系统
    enable_goal: bool = True
    #: 是否启用 session-query 会话检索五件套
    enable_session_query: bool = True
    #: 是否启用 web_fetch / web_search
    enable_web: bool = True
    #: 后续网页工具统一使用 DeepSeek，与对话模型无关。
    deepseek_web_enabled: bool = False
    #: 仅用于联网服务鉴权，不进入公开配置或对象展示。
    deepseek_web_api_key: str = field(default="", repr=False)
    #: 可与其他搜索服务同时启用，失败时继续下一个。
    brave_web_enabled: bool = False
    brave_web_api_key: str = field(default="", repr=False)
    #: 是否启用 run_code
    enable_code: bool = True
    #: 是否启用 read_image
    enable_image: bool = True
    #: 是否启用 lsp
    enable_lsp: bool = True
    #: 是否启用子代理系统
    enable_subagent: bool = True
    #: 是否启用技能系统
    enable_skills: bool = True
    #: 是否启用Agent系统（Agent注册表、召唤工具、Agent 团队编排）
    enable_agents: bool = True
    #: 是否启用连接器管理层（connectors.json 声明 + 运行时启停）
    enable_connectors: bool = True
    #: 是否启用 ask_user_question（与 UI 应答通道联动）
    enable_interaction: bool = True
    #: 是否启用 present（交付物声明卡片）
    enable_present: bool = True
    #: 是否启用 load_workspace_dependencies
    enable_workspace_deps: bool = True
    #: 是否启用 ralph（自治循环子代理）
    enable_ralph: bool = True
    enable_workflow: bool = True
    #: 是否启用 MCP（外部 Model Context Protocol 服务器）桥接
    enable_mcp: bool = True
    enable_plan_mode: bool = True
    #: 文件系统观察策略：edit 必须先 read，write 对已观察文件做版本 CAS。
    enable_fs_observation_policy: bool = True
    #: 重复工具调用提醒：连续 N 次"同工具 + 同参数"时向模型注入提醒。
    #: 默认在 3/5/8 次连续重复时触发。
    enable_repeat_tool_reminder: bool = True
    #: 触发提醒的连续重复次数（升序、>= 2、去重）。非法值在启动时快速失败。
    repeat_tool_reminder_thresholds: list[int] = field(
        default_factory=lambda: [3, 5, 8]
    )
    #: 详细提醒里引用重复参数的长度上限（>= 1）。
    repeat_tool_reminder_preview_chars: int = 500
    #: 对重复链透明的工具（既不计数也不重置）。``*`` 通配。
    repeat_tool_reminder_exclude: list[str] = field(default_factory=list)
    #: 压缩前裁剪过重工具结果（默认阈值 8192/4096/1024）。
    #: 该裁剪不发起模型调用，可能直接省掉一次摘要请求。
    enable_tool_result_pruner: bool = True
    #: 超过该字符数的工具结果会被裁剪。
    tool_result_prune_threshold_chars: int = 8192
    #: 裁剪后保留的头部字符数。
    tool_result_prune_head_chars: int = 4096
    #: 裁剪后保留的尾部字符数。
    tool_result_prune_tail_chars: int = 1024
    #: 外部 MCP 服务器配置；每台服务器的工具会以
    #: ``mcp__<serverName>__<tool>`` 的名字注册进工具表。
    mcp_servers: list[McpServerConfig] = field(default_factory=list)
    #: Agent 名称（写进系统提示）
    agent_name: str = "Harness Agent"
    #: Agent 描述（写进系统提示）
    agent_description: str = ""
    #: 允许模型调用的工具白名单；``None`` 表示全部
    tool_allow: list[str] | None = None
    #: 默认回复语言
    language: str = field(default_factory=lambda: _DefaultLanguage("auto"))
    #: 显式语言覆盖仅用于启动恢复优先级，不写入公开配置。
    _language_explicit: bool = field(default=False, init=False, repr=False, compare=False)
    _language_initialized: bool = field(default=False, init=False, repr=False, compare=False)
    context_window: int | None = None
    output_reserve: int = 16_000
    #: None 关闭每回合执行上限，有限预算仍需显式启用。
    max_steps: int | None = None
    token_budget: int | None = None
    #: 整棵代理树的并发上限；None 表示不限制名额。后代同样占名额，
    #: 阻塞观察期间让渡，因此父代理等待子代理不会与闸门互锁。
    max_concurrent_subagents: int | None = None
    #: 派生深度上限；根会话直接派生的成员深度为 1。超限在派生时立即失败。
    max_subagent_depth: int | None = DEFAULT_MAX_SUBAGENT_DEPTH
    #: 单个受管子代理的墙钟时限（秒）；None 表示不设默认时限，
    #: 团队等调用方仍可指定更严格的每成员时限。
    subagent_job_timeout: float | None = None
    permission_policy: str = "full_access"
    enable_setup: bool = False
    enable_browser: bool = False
    enable_automations: bool = True
    enable_worktrees: bool = True
    #: 项目长期记忆与记忆工具；只读时不创建存储。
    enable_project_memory: bool = True
    #: 显式本地语义模型缓存；None保持关闭，不在运行时设置接口中暴露本地路径。
    memory_model_cache: Path | None = None

    def __post_init__(self) -> None:
        from ..core.language import normalize_language
        if not self._language_initialized:
            self._language_explicit = not isinstance(self.language, _DefaultLanguage)
            self._language_initialized = True
        self.language = normalize_language(self.language)
        self.home = Path(self.home).expanduser()
        if self.memory_model_cache is not None:
            if not isinstance(self.memory_model_cache, (str, Path)) or not str(self.memory_model_cache).strip():
                raise ValueError(tr('runtime.validation.ac7799bcc744'))
            self.memory_model_cache = Path(self.memory_model_cache).expanduser().absolute()
        # key 池归一化：去空白、去重保序；池非空时 api_key 对齐到 1 号。
        self.api_keys = list(dict.fromkeys(key.strip() for key in self.api_keys if key and key.strip()))
        if self.api_keys:
            self.api_key = self.api_keys[0]
        if self.model_routes:
            self.model_routes = [{**m, **{name: positive(m.get(name)) for name in ('context_window', 'max_output_tokens')}} for m in self.model_routes]
            from ..llm.routes import ModelRoutes
            self.model_routes = ModelRoutes(self.model_routes).models
            first = next(m for m in self.model_routes if m['enabled'])
            self.api_keys = [k['value'] for k in first['keys']]
            self.api_key = self.api_keys[0]
            self.model, self.base_url = first['model'], first['base_url']
        if self.cwd is not None:
            self.cwd = Path(self.cwd).expanduser()
        import logging
        for name in ('context_window', 'summary_max_tokens', 'model_context_window', 'model_max_output_tokens'):
            value = getattr(self, name)
            if value is not None and positive(value) is None:
                logging.getLogger(__name__).warning(tr('log.1e037c2b9969'), name)
                setattr(self, name, None)
        if type(self.context_auto_manage) is not bool:
            raise ValueError(tr('context.settings.boolean'))
        if self.tool_schema_mode not in ('auto', 'all'):
            raise ValueError(tr('runtime.validation.f99d35499b74'))
        if self.tool_result_max_chars is not None and (type(self.tool_result_max_chars) is not int or self.tool_result_max_chars < 512):
            raise ValueError(tr('tool.result.ef15eeb94ce3'))
        if type(self.context_working_budget) is not int or self.context_working_budget < 1:
            raise ValueError(tr('context.settings.budget'))
        if type(self.output_reserve) is not int or self.output_reserve < 0:
            self.output_reserve = 16_000
        for name in ("max_steps", "token_budget", "max_concurrent_subagents", "max_subagent_depth"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value < 1):
                logging.getLogger(__name__).warning(tr('log.1e037c2b9969'), name)
                setattr(self, name, None)
        if self.subagent_job_timeout is not None:
            try:
                valid_timeout = (
                    type(self.subagent_job_timeout) in {int, float}
                    and self.subagent_job_timeout > 0
                    and math.isfinite(float(self.subagent_job_timeout))
                )
            except OverflowError:
                valid_timeout = False
            if not valid_timeout:
                logging.getLogger(__name__).warning(tr('log.1e037c2b9969'), "subagent_job_timeout")
                self.subagent_job_timeout = None
        if self.permission_policy not in {"read_only", "request_approval", "auto_review", "full_access"}:
            raise ValueError(tr('python.config.d0aaa8d658c0'))
        if type(self.network_timeout) not in {int, float} or self.network_timeout <= 0 or (type(self.network_timeout) is float and not math.isfinite(self.network_timeout)):
            self.network_timeout = 180.0
        if self.temperature is not None and (type(self.temperature) not in {int, float} or not 0 <= self.temperature <= 2 or not math.isfinite(self.temperature)):
            self.temperature = None
        if self.prompt_cache_mode not in {"auto", "off"}:
            raise ValueError(tr('python.config.2939d167a30b'))
        if type(self.enable_stream_checkpoints) is not bool:
            raise ValueError(tr('python.config.5ce92d3c0eda'))

    @classmethod
    def from_env(cls, **overrides) -> "HarnessConfig":
        """从环境变量构造配置。

        支持的变量::

            OPENROUTER_API_KEY / HARNESS_API_KEY
            HARNESS_API_KEYS / OPENROUTER_API_KEYS（逗号分隔的有序 key 池）
            HARNESS_API_KEYS_FILE（key 列表文件路径；未设置时自动读取
                ``~/.harness/api-keys.txt``（若存在）。文件每行一把 key，
                ``#`` 开头为注释，也兼容 ``name = "sk-..."`` 赋值行）
            HARNESS_BASE_URL
            HARNESS_MODEL
            HARNESS_HOME
            HARNESS_CWD
            HARNESS_REASONING_EFFORT
            HARNESS_TEMPERATURE
            HARNESS_OWNED_PROCESSES（1 启用所属执行器，0 使用平台基础执行器）
            HARNESS_MCP_SERVERS（JSON 数组或 ``@path`` 配置文件）
            HARNESS_MEMORY_MODEL_CACHE（显式本地模型缓存目录；未设置则关闭语义候选）
            HARNESS_MAX_CONCURRENT_SUBAGENTS（整棵代理树的并发名额上限）
            HARNESS_MAX_SUBAGENT_DEPTH（派生深度上限，默认 8）
            HARNESS_SUBAGENT_JOB_TIMEOUT（单个受管子代理墙钟时限，秒；未设置则不限）
        """
        env = os.environ
        mcp_servers = _load_mcp_servers(env.get("HARNESS_MCP_SERVERS"))
        home = Path(env.get("HARNESS_HOME", str(DEFAULT_HOME))).expanduser()
        # key 池优先级：显式列表环境变量 > 指定文件 > 默认 ~/.harness/api-keys.txt；
        # 都没有时退回单 key（OPENROUTER_API_KEY）。
        pool_keys = _split_keys(env.get("HARNESS_API_KEYS") or env.get("OPENROUTER_API_KEYS"))
        if not pool_keys:
            keys_file = Path(env["HARNESS_API_KEYS_FILE"]).expanduser() if env.get("HARNESS_API_KEYS_FILE") else home / "api-keys.txt"
            if keys_file.is_file():
                try:
                    pool_keys = parse_key_list(keys_file.read_text(encoding="utf-8"))
                except OSError:
                    pool_keys = []
        config = cls(
            api_key=env.get("OPENROUTER_API_KEY") or env.get("HARNESS_API_KEY", ""),
            api_keys=pool_keys,
            base_url=env.get("HARNESS_BASE_URL", DEFAULT_BASE_URL),
            model=env.get("HARNESS_MODEL", DEFAULT_MODEL),
            language=env.get("HARNESS_LANGUAGE", "auto"),
            home=home,
            cwd=Path(env["HARNESS_CWD"]).expanduser() if env.get("HARNESS_CWD") else None,
            memory_model_cache=env.get('HARNESS_MEMORY_MODEL_CACHE') or None,
            reasoning_effort=env.get("HARNESS_REASONING_EFFORT") or None,
            temperature=_float_or_none(env.get("HARNESS_TEMPERATURE")),
            summary_max_tokens=_int_or_none(env.get('HARNESS_SUMMARY_MAX_TOKENS', '8192')),
            context_auto_manage=env.get('HARNESS_CONTEXT_AUTO_MANAGE', '1') == '1',
            tool_schema_mode=env.get('HARNESS_TOOL_SCHEMA_MODE', 'auto'),
            context_working_budget=int(env.get('HARNESS_CONTEXT_WORKING_BUDGET', str(DEFAULT_WORKING_BUDGET))),
            model_context_window=_int_or_none(env.get('HARNESS_MODEL_CONTEXT_WINDOW')),
            model_max_output_tokens=_int_or_none(env.get('HARNESS_MODEL_MAX_OUTPUT_TOKENS')),
            context_window=_int_or_none(env.get('HARNESS_CONTEXT_WINDOW')),
            max_steps=_int_or_none(env.get('HARNESS_MAX_STEPS')),
            token_budget=_int_or_none(env.get('HARNESS_TOKEN_BUDGET')),
            max_concurrent_subagents=_int_or_none(env.get('HARNESS_MAX_CONCURRENT_SUBAGENTS')),
            max_subagent_depth=_int_or_none(env.get('HARNESS_MAX_SUBAGENT_DEPTH', str(DEFAULT_MAX_SUBAGENT_DEPTH))),
            subagent_job_timeout=_float_or_none(env.get('HARNESS_SUBAGENT_JOB_TIMEOUT')),
            network_timeout=_float_or_none(env.get('HARNESS_NETWORK_TIMEOUT')) or 180.0,
            mcp_servers=mcp_servers,
            permission_policy=env.get("HARNESS_PERMISSION_POLICY", "request_approval"),
            enable_automations=env.get("HARNESS_AUTOMATIONS", "1") == "1",
            enable_owned_processes=env.get("HARNESS_OWNED_PROCESSES", "1" if _default_owned_processes() else "0") == "1",
            enable_stream_checkpoints=env.get("HARNESS_STREAM_CHECKPOINTS", "1") == "1",
            prompt_cache_mode=env.get("HARNESS_PROMPT_CACHE_MODE", "auto"),
        )
        # 本地控制台保存的非敏感设置；显式 SDK 参数仍然优先。
        import json
        settings_path = Path(overrides.get("home") or config.home) / "runtime-settings.json"
        if settings_path.exists():
            try:
                saved = json.loads(settings_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise ValueError(tr('python.config.b044cebb12fd')) from exc
            if not isinstance(saved, dict) or type(saved.get("version")) is not int or saved["version"] != 1 or not isinstance(saved.get("settings"), dict):
                raise ValueError(tr('python.config.b044cebb12fd'))
            for key, value in saved["settings"].items():
                if key in {"tool_schema_mode", "tool_result_max_chars", "context_auto_manage", "context_working_budget", "permission_policy", "context_window", "output_reserve", "max_steps", "token_budget", "model", "base_url", "reasoning_effort", "summary_max_tokens", "model_context_window", "model_max_output_tokens", "network_timeout"}:
                    setattr(config, key, value)
        from ..core.language import saved_language
        preference = saved_language(Path(overrides.get("home") or config.home))
        if preference is not None:
            config.language = preference
        # 显式覆盖优先
        for key, value in overrides.items():
            if hasattr(config, key) and (value is not None or key in {
                "tool_result_max_chars", "max_steps", "token_budget", "context_window", "summary_max_tokens", "model_context_window", "model_max_output_tokens", "network_timeout", "temperature", "reasoning_effort", "memory_model_cache"
            }):
                setattr(config, key, value)
        config.__post_init__()
        config._language_explicit = "language" in overrides
        if 'model_routes' not in overrides:
            from ..llm.routes import load_models
            config.model_routes = load_models(config.home, config.model, config.base_url,
                                              config.api_keys or ([config.api_key] if config.api_key else []))
        if config.model_routes:
            config.__post_init__()
        if not (config.home / 'connection-secrets.json').exists():
            for route in config.model_routes:
                route['context_window'] = route.get('context_window') or config.model_context_window
                route['max_output_tokens'] = route.get('max_output_tokens') or config.model_max_output_tokens
        return config

    def to_dict(self, *, include_secrets: bool = False) -> dict:
        """转成字典（UI 展示用）。"""
        data = {
            "language": self.language,
            "permission_policy": self.permission_policy,
            "context_window": self.context_window,
            "output_reserve": self.output_reserve,
            "max_steps": self.max_steps,
            "token_budget": self.token_budget,
            "max_concurrent_subagents": self.max_concurrent_subagents,
            "max_subagent_depth": self.max_subagent_depth,
            "subagent_job_timeout": self.subagent_job_timeout,
            "enable_browser": self.enable_browser,
            "enable_automations": self.enable_automations,
            "enable_worktrees": self.enable_worktrees,
            "enable_project_memory": self.enable_project_memory,
            "enable_owned_processes": self.enable_owned_processes,
            "base_url": self.base_url,
            "model": self.model,
            "home": str(self.home),
            "cwd": str(self.cwd) if self.cwd else None,
            "reasoning_effort": self.reasoning_effort,
            "temperature": self.temperature,
            "summary_max_tokens": self.summary_max_tokens,
            "context_auto_manage": self.context_auto_manage,
            "context_working_budget": self.context_working_budget,
            "tool_result_max_chars": self.tool_result_max_chars,
            "tool_schema_mode": self.tool_schema_mode,
            "model_context_window": self.model_context_window,
            "model_max_output_tokens": self.model_max_output_tokens,
            "network_timeout": self.network_timeout,
            "enable_stream_checkpoints": self.enable_stream_checkpoints,
            "prompt_cache_mode": self.prompt_cache_mode,
            "tools": {
                "fs": self.enable_fs,
                "shell": self.enable_shell,
                "todo": self.enable_todo,
                "goal": self.enable_goal,
                "session_query": self.enable_session_query,
                "web": self.enable_web,
                "code": self.enable_code,
                "image": self.enable_image,
                "lsp": self.enable_lsp,
                "subagent": self.enable_subagent,
                "skills": self.enable_skills,
                "agents": self.enable_agents,
                "connectors": self.enable_connectors,
                "interaction": self.enable_interaction,
                "present": self.enable_present,
                "workspace_deps": self.enable_workspace_deps,
                "ralph": self.enable_ralph,
                "workflow": self.enable_workflow,
                "plan_mode": self.enable_plan_mode,
                "fs_observation_policy": self.enable_fs_observation_policy,
                "repeat_tool_reminder": self.enable_repeat_tool_reminder,
                "repeat_tool_reminder_thresholds": list(
                    self.repeat_tool_reminder_thresholds
                ),
                "tool_result_pruner": self.enable_tool_result_pruner,
                "mcp": self.enable_mcp,
            },
            "mcp_servers": [
                {
                    "server_name": server.server_name,
                    "transport": server.transport,
                    "connected": False,
                }
                for server in self.mcp_servers
            ],
            "agent_name": self.agent_name,
            "has_api_key": bool(self.api_key),
            "api_key_pool_size": len(self.api_keys),
            "model_routing_enabled": bool(self.model_routes),
        }
        if include_secrets:
            data["api_key"] = self.api_key
        return data


def _split_keys(raw: str | None) -> list[str]:
    """解析逗号/换行分隔的 key 列表环境变量。"""
    if not raw:
        return []
    parts = re.split(r"[,\n]", raw)
    return [part.strip() for part in parts if part.strip()]


def _float_or_none(value: str | None) -> float | None:
    try:
        return float(value) if value else None
    except (TypeError, ValueError):
        return None


def _int_or_none(value: str | None) -> int | None:
    try:
        return int(value) if value else None
    except (TypeError, ValueError):
        return None


def _load_mcp_servers(raw: str | None) -> list[McpServerConfig]:
    """读取当前数组声明；损坏原件保留，任何条目非法都拒绝整份配置。"""
    if raw is None or raw == "":
        return []
    if not isinstance(raw, str):
        raise ValueError(tr('config.mcp.invalid_array'))
    import json

    text = raw.strip()
    if text.startswith("@"):
        try:
            path = Path(text[1:].strip()).expanduser()
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            raise ValueError(tr('config.mcp.read_failed')) from None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        raise ValueError(tr('config.mcp.invalid_array')) from None
    if not isinstance(payload, list):
        raise ValueError(tr('config.mcp.invalid_array'))
    servers: list[McpServerConfig] = []
    names: set[str] = set()
    for index, item in enumerate(payload, 1):
        try:
            if not isinstance(item, dict):
                raise TypeError()
            server = McpServerConfig(**item)
            if server.server_name in names:
                raise ValueError()
        except (McpError, TypeError, ValueError):
            raise ValueError(tr('config.mcp.invalid_server', index=index)) from None
        names.add(server.server_name)
        servers.append(server)
    return servers


__all__ = [
    "HarnessConfig",
    "DEFAULT_HOME",
    "DEFAULT_MODEL",
    "DEFAULT_BASE_URL",
]
