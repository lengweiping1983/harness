"""Harness：把各层组装成一个可用的 Agent 运行时。

这是**业务方唯一需要直接接触的类**。它把
LLM 适配器、会话存储、工具注册表、技能注册表、提示词注册表和 Agent 循环
组装成一个整体，并提供同步/异步两种调用方式。

典型集成（把 harness 变成一个业务 Agent）::

    from harness import Harness, HarnessConfig

    harness = Harness(HarnessConfig.from_env(cwd="/srv/myapp"))
    session = harness.create_session()

    # 方式一：阻塞式（脚本、批处理）
    result = harness.run(session, "统计 /data 下的 CSV 行数")

    # 方式二：异步流式（Web 服务、UI）
    async for event in harness.stream(session, "生成一份周报"):
        print(event["type"], event.get("text", ""))

    # 方式三：接业务工具
    @harness.tool("query_orders", "查询订单", {...})
    async def query_orders(args, ctx):
        return await my_db.query(args["user_id"])
"""

from __future__ import annotations

from harness.i18n import tr

import asyncio
import inspect
import os
import uuid
import contextlib
import logging
from collections import deque
from contextvars import ContextVar
from dataclasses import replace
from pathlib import Path
from typing import Any, AsyncIterator, Callable

from ..core.agent import AgentLoop
from ..core.turn import TurnResult
from ..core.cleanup import join_cleanup, raise_cleanup_errors
from ..core.errors import ConfigError, ConnectorError, HarnessError, PersistenceError, SessionNotFoundError
from ..core.events import Context, EventBus
from ..llm.adapter import OpenRouterAdapter
from ..llm.context_windows import load_context_windows, save_context_windows
from ..llm.key_pool import ApiKeyPool
from ..llm.transport import Transport
from ..llm.types import LlmCallConfig
from ..interaction.questions import QuestionBroker
from ..prompt.tool_policies import build_tool_policies
from ..prompt.registry import (
    ORDER_POLICY,
    ORDER_PERSONA_PREFIX,
    ORDER_SKILL_CATALOG,
    ORDER_TOOLS,
    SystemPromptRegistry,
    build_base_prompt,
)
from ..session.session import Session
from ..session.store import SessionStore
from ..skills.registry import SkillRegistry, build_default_registry
from ..skills.tool import build_skill_tool
from ..agents import (
    AgentMountController,
    AgentRegistry,
    build_default_agent_registry,
    build_agent_tool,
    render_catalog as render_agent_catalog,
)
from ..plugins.state import EscStateStore
from ..connectors.store import ConnectorStore
from ..connectors.credentials import CredentialStore
from ..connectors.manager import ConnectorManager
from ..connectors.presets import get_preset
from ..subagent.manager import SubagentManager
from ..tools.repeat_reminder import RepeatToolReminder
from ..session.tool_result_pruner import ToolResultPruner, resolve_budget
from ..tools.builtin.code_tools import build_code_tools
from ..tools.builtin.fs_tools import build_fs_tools
from ..tools.builtin.image_tools import build_image_tools
from ..tools.builtin.goal_tools import GoalStore, build_goal_tools
from ..tools.builtin.session_query_tools import SESSION_QUERY_PROMPT_KEY, build_session_query_tools
from ..tools.builtin.interaction_tools import build_ask_user_question_tools
from ..tools.builtin.widget_tools import build_widget_tools
from ..tools.builtin.job_tools import JobRegistry, build_job_tools
from ..tools.builtin.lsp_tools import build_lsp_tools
from ..tools.builtin.plan_tools import build_exit_plan_mode_tool
from .plan_mode import plan_policy_section, PlanModeController
from ..tools.builtin.mcp_client import McpRegistry
from ..tools.builtin.mcp_tools import build_mcp_resource_tools, build_remote_tool
from ..tools.builtin.present_tools import build_present_tools
from ..tools.builtin.pwsh_tools import build_pwsh_tools
from ..tools.builtin.ralph_tools import build_ralph_tools
from ..tools.builtin.workflow_tools import build_workflow_tools
from ..tools.builtin.shell_tools import build_shell_tools
from ..tools.builtin.subagent_model_tools import build_list_subagent_models_tool
from ..tools.builtin.subagent_tools import build_subagent_tools
from ..tools.builtin.terminal_tools import TerminalSessionService, build_terminal_tools
from ..tools.builtin.hex_tools import HexServiceManager, build_hex_tools, run_hex_owned
from ..tools.builtin.todo_tools import TodoStore, build_todo_tools
from ..tools.builtin.project_tools import build_project_tools
from ..tools.builtin.web_tools import build_web_tools
from ..tools.builtin.workspace_tools import build_workspace_dependency_tools
from ..tools.registry import ToolDefinition, ToolRegistry
from .config import HarnessConfig
from .runtime import IdentityContext

logger = logging.getLogger(__name__)

# 标记随此 SDK 调用传递到运行时和工具任务；等待会话锁的流
# 不能阻碍其他调用方正在执行的回合。
_stream_owner: ContextVar[object | None] = ContextVar('harness_stream_owner', default=None)
_STREAM_BUFFER_EVENTS = 64


class _StreamEventBuffer:
    """缓冲区有界，中断前不丢失数据；关闭时唤醒受阻的生产者。"""

    def __init__(self) -> None:
        self.items: deque[tuple[str, dict[str, Any]] | None] = deque()
        self.ready = asyncio.Event()
        self.space = asyncio.Event()
        self.closed = False
        self.interrupted = False

    async def put(self, item: tuple[str, dict[str, Any]] | None) -> None:
        while len(self.items) >= _STREAM_BUFFER_EVENTS and not self.closed and not self.interrupted:
            self.space.clear()
            await self.space.wait()
        if self.closed:
            return
        if len(self.items) >= _STREAM_BUFFER_EVENTS:
            # 即使没有流读取者，外部取消也必须完成回合收尾。
            # 日志和助手部分输出保存权威结果；
            # 保留近期终止事件，替换过时的增量事件。
            self.items.popleft()
        self.items.append(item)
        self.ready.set()

    async def get(self) -> tuple[str, dict[str, Any]] | None:
        while not self.items:
            if self.closed:
                return None
            self.ready.clear()
            await self.ready.wait()
        item = self.items.popleft()
        self.space.set()
        return item

    def interrupt(self) -> None:
        self.interrupted = True
        self.space.set()

    def close(self) -> None:
        self.closed = True
        self.items.clear()
        self.ready.set()
        self.space.set()


async def _settle_plan_mode(
    agent: AgentLoop, session: Session, turn: int, step: int
) -> None:
    """step 边界钩子：结算排队中的计划模式切换。"""
    controller = getattr(agent, "plan_controller", None)
    if controller is not None:
        await controller.settle(session)


_SKILL_SELECTION_SNAPSHOT: ContextVar[tuple[object, str, tuple[str, ...] | None] | None] = ContextVar('agent_skill_selection', default=None)


class Harness:
    """Agent 运行时。

    :param config: 配置；``None`` 时从环境变量读取。
    :param transport: 传输实现（测试时注入假实现以离线运行）。
    :param memory_candidates: 可选的本地候选服务；未提供时可由 memory_model_cache 配置装配。
    """

    def __init__(
        self,
        config: HarnessConfig | None = None,
        *,
        transport: Transport | None = None,
        identity: IdentityContext | None = None,
        memory_candidates=None,
        codex_gateway_home: Path | None = None,
    ) -> None:
        self.identity = identity or IdentityContext()
        self.config = config or HarnessConfig.from_env()
        from ..llm.chatgpt_auth import ChatGPTAccount
        from ..llm.providers import ProviderConnections
        self.provider_connections = ProviderConnections(self.config.home)
        self.config.provider_connections = self.provider_connections
        from ..codex_gateway.accounts import AccountPool
        gateway_home = Path(codex_gateway_home) if codex_gateway_home is not None else Path(os.environ.get('HARNESS_CODEX_GATEWAY_HOME', str(self.config.home / 'codex-gateway')))
        self.codex_gateway = AccountPool(gateway_home, ChatGPTAccount(gateway_home))
        self.chatgpt = self.codex_gateway.slots['primary'].account
        self.config.chatgpt_account = self.chatgpt
        self.config.subscription_pool = self.codex_gateway
        available_models = self.provider_connections.catalog()
        if available_models and not any(m['id'] == self.config.model for m in available_models):
            self.config.model = available_models[0]['id']
        from ..codex_gateway.desktop import DesktopIntegration
        self.codex_desktop = DesktopIntegration(gateway_home)
        if not self.has_model_credentials and not self.config.enable_setup:
            raise ConfigError(
                tr('python.harness.12bc005f8680')
            )

        # -- 总线与上下文 --------------------------------------------------
        self.bus = EventBus()
        self.ctx = Context(self.bus)
        self.ctx.set("bus", self.bus)

        # -- 大语言模型 -----------------------------------------------------
        # 多 key 轮换池：报错（429/超时/5xx/401/402/403）自动标记并切下一把；
        # 状态按天持久化在 home 下，跨天自动恢复。
        keys = self.config.api_keys or ([self.config.api_key] if self.config.api_key else [])
        self.key_pool = ApiKeyPool(keys, state_path=self.config.home / "api-key-pool.json") if len(keys) > 1 else None
        # 端点真实上下文窗口的学习缓存（400 报错里自报的值），按 base_url|model 持久化。
        self._learned_windows: dict[str, int] = load_context_windows(
            self.config.home / "context-windows.json")
        from ..llm.capabilities import CapabilityRegistry
        from ..llm.types import ModelInfo
        capabilities = CapabilityRegistry(self.config.home, discover=transport is None)
        for key, window in self._learned_windows.items():
            url, model = key.rsplit('|', 1)
            capabilities.learn(url, model, context_window=window)
        self.adapter = OpenRouterAdapter(
            self.config.api_key or "setup-pending",
            base_url=self.config.base_url,
            model=self.config.model,
            transport=transport,
            max_retries=self.config.max_retries,
            network_timeout=self.config.network_timeout,
            context_window=self.config.context_window,
            capabilities=capabilities,
            model_info=ModelInfo('openrouter', self.config.model, self.config.model_context_window, self.config.model_max_output_tokens),
            key_pool=self.key_pool,
            prompt_cache_mode=self.config.prompt_cache_mode,
        )
        self.adapter.chatgpt = None
        self.adapter.provider_connections = self.provider_connections
        self.adapter.subscription_pool = self.codex_gateway
        if self.config.model_routes:
            from ..llm.routes import ModelRoutes
            self.adapter.route_pool = ModelRoutes(self.config.model_routes, self.config.home)
            self.adapter.route_pool.capabilities = capabilities
            if not (self.config.home / 'connection-secrets.json').exists():
                self.adapter.route_pool.update(self.config.model_routes)
            self.adapter.key_pool = None
            self.key_pool = None

        # -- 会话 ----------------------------------------------------------
        self.sessions = SessionStore(self.config.home, bus=self.bus)
        from .session_titles import SessionTitles
        self.session_titles = SessionTitles(self)

        # -- 工具 ----------------------------------------------------------
        self.tools = ToolRegistry(allow=self.config.tool_allow)
        self.todos = TodoStore(self.bus, session_of=self.get_session)
        # 会话目标（goal）：create/get/update 三件套共用
        self.goals = GoalStore(sessions=self.sessions, bus=self.bus)
        from .goal_runtime import GoalRuntime
        self.goal_runtime = GoalRuntime(self)
        # 后台作业注册表：bash 的 run_in_background 与 job_* 工具共用
        from ..tools.job_archive import JobArchive
        self.jobs = JobRegistry(archive=JobArchive(self.config.home / 'jobs.sqlite3'))
        self.tools.services["jobs"] = self.jobs
        self.tools.add_guard(self.jobs.guard)
        self.tools.add_post_hook(self.jobs.record_progress)
        # terminal_* 工具共用的常驻终端会话表
        self.terminals = TerminalSessionService()
        # hex 语音转文字服务的生命周期管理器（懒启动，close 时优雅停机）
        self.hex_service = HexServiceManager()
        # ask_user_question 的挂起/应答通道（UI 通过 bus 事件与 REST 应答）
        self.questions = QuestionBroker(self.bus)
        #: 外部 MCP 服务器桥接；工具表在 :meth:`ensure_started` 里补齐。
        self._started = False
        self._start_task: asyncio.Task[None] | None = None
        # 计划模式控制器：必须在 self.sessions 之后构造（它要按 id 反查会话）。
        self.plan = PlanModeController(self.sessions)
        self.tools.add_restriction(self.plan.guard)
        self.tools.add_execution_check(self.plan.guard)
        # 重复工具调用提醒：默认阈值 3/5/8 次。
        # 阈值非法时在这里快速失败，不做静默降级。
        self.repeat_reminder: RepeatToolReminder | None = None
        if self.config.enable_repeat_tool_reminder:
            self.repeat_reminder = RepeatToolReminder(
                self.config.repeat_tool_reminder_thresholds,
                exclude=self.config.repeat_tool_reminder_exclude,
                arguments_preview_chars=self.config.repeat_tool_reminder_preview_chars,
            )
        # 过重工具结果裁剪：预算非法时在这里快速失败。
        self.tool_result_pruner: ToolResultPruner | None = None
        if self.config.enable_tool_result_pruner:
            self.tool_result_pruner = ToolResultPruner(
                resolve_budget(
                    self.config.tool_result_prune_threshold_chars,
                    self.config.tool_result_prune_head_chars,
                    self.config.tool_result_prune_tail_chars,
                )
            )
        from ..project.artifact_review import ArtifactReview
        self.artifact_review = ArtifactReview(self)
        self._register_builtin_tools()
        from ..tools.builtin.tool_result_tools import build_tool_result_reader
        self.tools.register(build_tool_result_reader())
        from ..tools.discovery import build_tool_search
        self.tools.register(build_tool_search())

        # -- 插件包：状态与能力声明独立于技能和连接器。 --------------------
        from ..plugins.packages import PackageStore
        self.packages = PackageStore(self.config.home)

        # -- 技能 ----------------------------------------------------------
        self.skills: SkillRegistry | None = None
        if self.config.enable_skills:
            self.skills = build_default_registry(
                cwd=str(self.config.cwd) if self.config.cwd else None,
                # 与 EscStateStore 同一 home：HARNESS_HOME 重定向时技能/Agent
                # 的用户级目录也必须跟着走，否则状态与内容分属两个家目录。
                home=str(self.config.home),
            )
            for tool in build_skill_tool(self.skills, registry_for_session=self.skills_for):
                self.tools.register(tool)

        # -- Agent·技能·连接器（ESC）生命周期状态 --------------------------
        # 安装 ≠ 启用：显式关闭项持久化在 ~/.harness/esc-state.json。
        self.esc_state = EscStateStore(self.config.home)
        if self.config.enable_fs:
            from ..plugins.authoring_tools import build_authoring_tools
            self._register_optional_tools(build_authoring_tools(self), 'builtin:authoring')
        if self.skills is not None:
            self.skills.set_enabled_filter(self._default_skill_enabled)

        # -- 提示词 --------------------------------------------------------
        # 命名 prompts 而非 prompt：Harness 本身没有 prompt() 方法，
        # 但 AgentLoop 有；统一命名避免歧义。
        self.prompts = SystemPromptRegistry()
        self._register_prompt_sections()

        # -- Agent ----------------------------------------------------------
        # 注册表 + 会话级挂载控制器。人格注入是"动态 section 按会话渲染"：
        # _ensure_system_prompt 每回合重装配，agent:active 片段只在挂载了
        # Agent的会话里产出人格文本——与 plan:policy 同一纪律。
        self._active_team_runs: set[tuple[str, str]] = set()
        self.agents: AgentRegistry | None = None
        self.agent_mount: AgentMountController | None = None
        if self.config.enable_agents:
            self.agents = build_default_agent_registry(
                cwd=str(self.config.cwd) if self.config.cwd else None,
                home=str(self.config.home),
            )
            self.agents.set_enabled_filter(
                lambda agent: self.esc_state.is_enabled("agent", agent.name)
            )
            self.agent_mount = AgentMountController(self.agents, sessions=self.sessions, registry_for_session=self.agents_for, session_resolver=self._agent_session)
            self._register_agent_prompt_sections()

        # -- Agent 循环 -----------------------------------------------------
        self.agent = AgentLoop(
            self.adapter,
            self.tools,
            self.prompts,
            skills=self.skills,
            config=LlmCallConfig(
                model=self.config.model,
                temperature=self.config.temperature,
                reasoning_effort=self.config.reasoning_effort,
            ),
            bus=self.bus,
        )

        # 每个会话一个 AgentLoop（状态独立）
        self.agent.repeat_reminder = self.repeat_reminder
        self.agent.skills_for_session = self.skills_for
        self.agent.tool_schema_mode = self.config.tool_schema_mode
        self.agent.tool_schema_policy = lambda: self.config.tool_schema_mode
        self._agents: dict[str, AgentLoop] = {}
        from .runtime import RunController
        from .workspace_preparations import WorkspacePreparations
        self.workspace_preparations = WorkspacePreparations()
        self.runtime = RunController(self)
        self._close_task: asyncio.Task[None] | None = None
        self._superagent_workers: list[Any] = []
        self._superagent_workbench = None
        from .session_access import SessionAccess
        self.session_access = SessionAccess(self.sessions)
        from .session_credentials import SessionCredentials
        self.session_credentials = SessionCredentials(self.session_access)
        from .session_invitations import SessionInvitations
        self.session_invitations = SessionInvitations(self.session_access)
        from .session_decisions import SessionDecisions
        self.session_decisions = SessionDecisions(self.session_access, self.questions)
        from .permissions import PermissionPolicy
        self.permissions = PermissionPolicy(self)
        from ..project.execution import ProjectExecution
        self.project_execution = ProjectExecution(self)
        from ..project.planning import ProjectPlanning
        self.project_planning = ProjectPlanning(self)
        self.tools.add_restriction(self.project_planning.guard)
        self.tools.add_execution_check(self.project_planning.guard)
        from .hooks import ProjectHooks
        self.hooks = ProjectHooks(self)
        self.tools.add_guard(self.hooks.before)
        self.tools.add_result_observer(self.hooks.after)
        self.tools.add_guard(self.permissions.guard)
        self.tools.add_restriction(self.permissions.restrict_external_agent)
        from ..acp.tool import build_external_agent_tools
        for tool in build_external_agent_tools(self):
            self.tools.register(tool)
        from .file_restore import FileRestoreService
        self.file_restores = FileRestoreService(self)
        from .browser import BrowserService
        from .worktrees import WorktreeService
        if os.environ.get("HARNESS_DESKTOP_BRIDGE"):
            from .desktop_browser import DesktopBrowserService
            self.browser = DesktopBrowserService(self.config.home / "browser", bus=self.bus)
            self.config.enable_browser = True
        else:
            self.browser = BrowserService(self.config.home / "browser", bus=self.bus)
            self.config.enable_browser = False
        self.worktrees = WorktreeService(self.config.home / "worktrees") if self.config.enable_worktrees else None
        from .memory import ProjectMemory
        from .layered_memory import LayeredMemory
        from ..tools.builtin.memory_tools import build_layered_memory_tools
        self.memory = ProjectMemory() if self.config.enable_project_memory else None
        self.layered_memory = LayeredMemory(self.config.home, self.memory) if self.memory is not None else None
        self._owned_memory_candidates = None
        if self.memory is not None:
            if memory_candidates is None and self.config.memory_model_cache is not None:
                from .memory_semantic import LocalMemoryCandidates
                memory_candidates = LocalMemoryCandidates(self.config.memory_model_cache)
                self._owned_memory_candidates = memory_candidates
            for tool in build_layered_memory_tools(self.layered_memory, candidates=memory_candidates):
                self.tools.register(tool)
            self.prompts.add_section("project:memory", self._memory_context,
                                     order=ORDER_POLICY + 3, volatile=True, interpolate=False)
        self.automations = None
        if self.config.enable_automations:
            from .automations import AutomationService
            self.automations = AutomationService(self.config.home / "automations.sqlite3", self)
            from ..tools.builtin.automation_tools import build_automation_tools
            self.tools.services["automations"] = self.automations
            for tool in build_automation_tools():
                self.tools.register(tool)
            self.prompts.add_section(
                "tool:automation",
                lambda _variables: tr("policy.automation"),
                order=2380,
                volatile=True,
            )
        from .terminal_setup import TerminalSetupService
        self.terminal_setup = TerminalSetupService()
        if self.config.enable_browser:
            self._register_browser_tool()

        # -- 子代理系统 ------------------------------------------------
        # 必须在 self.agent / self.prompts / self.skills 就绪后构造；
        # 子代理复用同一份 ToolRegistry，因此工具注册放在这一步。
        self.subagents = SubagentManager(
            contexts=SessionStore(self.config.home / 'subagent-contexts', bus=self.bus,
                                  request_traces=self.sessions.request_traces, file_evidence=self.sessions.file_evidence),
            run_storage=self.runtime.storage,
            adapter=self.adapter,
            tools=self.tools,
            prompts=self.prompts,
            skills=self.skills,
            config=self.agent.config,
            bus=self.bus,
            cwd=str(self.config.cwd) if self.config.cwd else None,
            max_concurrent=self.config.max_concurrent_subagents,
            max_depth=self.config.max_subagent_depth,
            job_timeout=self.config.subagent_job_timeout,
        )
        self.runs = self.subagents.runs
        self.runs.controller = self.runtime
        self.subagents.session_resolver = self._agent_session
        self.subagents.request_traces = self.sessions.request_traces
        self.subagents.configure_agent = self._configure_subagent
        self.subagents.parent_config = self._subagent_parent_config
        self.subagents.parent_language = self._language_for_parent
        self.subagents.before_spawn = self._guard_subagent_preparation
        if self.config.enable_shell and self.config.enable_fs:
            async def execute_external_member(state, agent):
                from .external_agent_commands import run_external_agent_command
                return await run_external_agent_command(self, state.session, state.runner, state.text,
                    {'source': 'subagent', 'request_id': getattr(agent, 'request_id', None),
                     'run_id': getattr(agent, 'run_id', None)}, agent=agent)
            self.subagents.external_runner = execute_external_member
        from ..agents.personality import PersonalityController
        self.personalities = PersonalityController(self)
        self.prompts.add_section("agent:personality", self.personalities.context,
                                 order=ORDER_PERSONA_PREFIX + 60, volatile=True, interpolate=False)
        from ..collaboration.acceptance import build_acceptance_evidence_tool
        self.tools.register(build_acceptance_evidence_tool())
        self._prompt_team_runs = {}
        from ..collaboration.history import build_team_history_tool
        self.tools.register(build_team_history_tool(self))
        self.subagents.scope_resolver = lambda sid: self._prompt_team_runs[sid].scope if sid in self._prompt_team_runs else None
        for tool in build_subagent_tools(self.subagents, agent_persona=self._agent_persona_for_subagent, configured_spawn=self._spawn_configured_subagent, configured_followup=self._followup_subagent):
            self.tools.register(tool)
        from ..web.models import model_catalog
        self.tools.register(
            build_list_subagent_models_tool(
                base_url=self.config.base_url,
                model=self.config.model,
                api_key=self.config.api_key,
                model_directory=lambda: model_catalog(self.config)['models'],
            )
        )
        if self.config.enable_ralph:
            self._register_optional_tools(build_ralph_tools(self.subagents), 'builtin:ralph')
        if self.config.enable_workflow:
            workflow_cwd = str(self.config.cwd) if self.config.cwd else None
            for tool in build_workflow_tools(
                self.subagents,
                jobs=self.jobs,
                bus=self.bus,
                cwd=workflow_cwd,
            ):
                self.tools.register(tool)
        self.mcp = McpRegistry(
            list(self.config.mcp_servers) if self.config.enable_mcp else None
        )
        if self.config.enable_mcp:
            # 三个资源工具是静态的，先注册；远端工具要等连接建立后才知道有哪些。
            for tool in build_mcp_resource_tools(self.mcp):
                self.tools.register(tool)

        # -- Agent工具与Agent 团队 ------------------------------------------
        # `agent` 召唤工具：agent 型挂载人格、team 型现场编排。
        # Agent 团队依赖子代理系统；未启用子代理时 team 型召唤会得到明确报错。
        if self.agents is not None and self.agent_mount is not None:
            for tool in build_agent_tool(
                self.agents,
                self.agent_mount,
                bus=self.bus,
                registry_for_session=self.agents_for,
                prompt_activation=self._activate_prompt_team,
            ):
                self.tools.register(tool)

        # -- 连接器管理层 ----------------------------------------------
        # 把 connectors.json 里的声明黏合到 MCP 注册表 / 工具表；
        # enabled∧bound 的连接器在 ensure_started 阶段接入。
        self.connector_store = ConnectorStore(self.config.home)
        from ..plugins.provider import PackageConnectorProvider
        self.connector_store.plugin_provider = PackageConnectorProvider(self.packages)
        self.oauth_credentials = CredentialStore(self.config.home)
        self.connectors: ConnectorManager | None = None
        if self.config.enable_connectors:
            self.connectors = ConnectorManager(
                self.connector_store,
                self.mcp,
                self.tools,
                self.prompts,
                self.bus,
                self.oauth_credentials,
                register_prompt=self._register_mcp_prompt,
            )

        from ..plugins.service import PackageService
        self.plugin_service = PackageService(self)

    # ==================================================================
    # 组装
    # ==================================================================
    def _register_browser_tool(self):
        from .browser import TOOL_ACTIONS
        async def browser_tool(args, ctx):
            return await self.browser.execute(ctx.session_id, args)
        self.tools.register(ToolDefinition({"name": "browser", "description_key": "python.harness.15e31eb3ab97", "description": tr('python.harness.15e31eb3ab97'), "parameters": {"type": "object", "properties": {"action": {"type": "string", "enum": list(TOOL_ACTIONS)}, "url": {"type": "string"}, "full_page": {"type": "boolean"}, "screenshot_id": {"type": "string", "maxLength": 80}, "x": {"type": "number", "minimum": 0}, "y": {"type": "number", "minimum": 0}, "selector": {"type": "string"}, "ref": {"type": "string"}, "text": {"type": "string"}, "key": {"type": "string"}, "duration_ms": {"type": "integer", "minimum": 1, "maximum": 2000}, "tab_id": {"type": "string"}, "query": {"type": "string", "maxLength": 240}, "offset": {"type": "integer", "minimum": 0, "maximum": 1000000}, "values": {"type": "array", "items": {"type": "string", "maxLength": 1000}, "maxItems": 20, "uniqueItems": True}, "checked": {"type": "boolean"}, "dialog_id": {"type": "string", "maxLength": 80}, "accept": {"type": "boolean"}, "prompt_text": {"type": "string", "maxLength": 2000}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}, "delta_x": {"type": "number"}, "delta_y": {"type": "number"}}, "required": ["action"]}}, browser_tool, effect="read", concurrency_safe=False, cancellable=True, progress_key=self.browser.progress_key))

    def _register_optional_tools(self, tools, namespace):
        """明确标记扩展能力的定义加载策略，执行权限和副作用声明保持原值。"""
        for tool in tools:
            tool.defer_loading = True
            tool.discovery_namespace = namespace
            self.tools.register(tool)

    def _register_builtin_tools(self) -> None:
        """注册内置工具。"""
        cwd = str(self.config.cwd) if self.config.cwd else None
        tool_timeout = 18_000.0
        if self.config.enable_fs:
            for tool in build_fs_tools(cwd, observation_policy=self.config.enable_fs_observation_policy):
                self.tools.register(tool)
            self._register_optional_tools(build_project_tools(cwd, enable_git=self.config.enable_shell, verify_artifacts=self.artifact_review.verify), 'builtin:projects')
        if self.config.enable_shell:
            for tool in build_shell_tools(registry=self.jobs, owned_processes=self.config.enable_owned_processes):
                # 默认前台等待使用工具自身的短超时；显式长任务仍可选择更长超时。
                tool.timeout = tool_timeout + 15
                self.tools.register(tool)
            pwsh_tools = build_pwsh_tools(registry=self.jobs, owned_processes=self.config.enable_owned_processes)
            for tool in pwsh_tools:
                tool.timeout = tool_timeout + 15
            self._register_optional_tools(pwsh_tools, 'builtin:powershell')
            for tool in build_job_tools(self.jobs):
                self.tools.register(tool)
            self._register_optional_tools(build_terminal_tools(self.terminals, registry=self.jobs), 'builtin:terminal')
        if self.config.enable_todo:
            for tool in build_todo_tools(self.todos):
                self.tools.register(tool)
        if self.config.enable_goal:
            for tool in build_goal_tools(self.goals):
                self.tools.register(tool)
        if self.config.enable_session_query:
            self._register_optional_tools(build_session_query_tools(self.sessions), 'builtin:history')
        if self.config.enable_web:
            for tool in build_web_tools(timeout=tool_timeout, provider_settings=lambda: {name: (getattr(self.config, f"{name}_web_enabled"), getattr(self.config, f"{name}_web_api_key")) for name in ("deepseek", "brave")}):
                self.tools.register(tool)
        if self.config.enable_code:
            self._register_optional_tools(build_code_tools(cwd, timeout=min(tool_timeout, 120.0),
                registry=self.jobs, owned_processes=self.config.enable_owned_processes), 'builtin:code')
        if self.config.enable_image:
            for tool in build_image_tools(cwd):
                self.tools.register(tool)
        if self.config.enable_lsp:
            self._register_optional_tools(build_lsp_tools(cwd, timeout=30.0), 'builtin:lsp')
        if self.config.enable_interaction:
            for tool in build_ask_user_question_tools(self.questions):
                self.tools.register(tool)
        # 对话内小组件是零副作用的展示能力，无条件注册以保持工具目录稳定。
        for tool in build_widget_tools():
            self.tools.register(tool)
        # hex 语音转文字：随包二进制按需启动，按工具名发现（builtin:hex）。
        self._register_optional_tools(build_hex_tools(self.hex_service), 'builtin:hex')
        if self.config.enable_present:
            self._register_optional_tools(build_present_tools(self.bus, cwd=cwd), 'builtin:present')
        if self.config.enable_workspace_deps:
            self._register_optional_tools(build_workspace_dependency_tools(), 'builtin:dependencies')
        if self.config.enable_plan_mode:
            # 永远注册：模式切换时工具目录必须一字不变（request-cache 稳定性）。
            self.tools.register(
                build_exit_plan_mode_tool(
                    self.plan,
                    self.questions if self.config.enable_interaction else None,
                )
            )

    def _register_prompt_sections(self) -> None:
        """注册系统提示词片段。"""
        # 工具策略段：硬规则文案，按工具可用性条件渲染。
        for section_name, order, provider in build_tool_policies(set(self.tools.names())):
            if section_name == 'tool:browser-testing':
                continue
            if section_name == 'tool:todo':
                self.prompts.add_section(section_name, lambda variables, p=provider: (
                    p() if {'todo_write', 'todo_update'} & set(self.tools.names()) and not (
                        self.config.enable_plan_mode and self.plan.is_active_session_id(variables.get('session_id', ''))
                    ) and not variables.get('task_read_only') and not variables.get('recovery_read_only')
                    and not getattr(self._agents.get(variables.get('session_id', '')), 'recovery_read_only', False)
                    else ''), order=order, volatile=True)
                continue
            self.prompts.add_section(section_name, lambda variables, p=provider: p(), order=order)
        from ..prompt.tool_policies import browser_testing_policy
        self.prompts.add_section('tool:browser-testing',
            lambda variables: browser_testing_policy(set(self.tools.names())), order=1700)
        build_base_prompt(
            self.prompts,
            agent_name=self.config.agent_name,
            description=self.config.agent_description,
            working_dir=str(self.config.cwd) if self.config.cwd else None,
            language=self.config.language,
        )
        # 工具说明段：动态渲染当前可见工具
        self.prompts.add_section(
            "harness:tools",
            lambda variables: self._render_tool_section(),
            order=ORDER_TOOLS,
        )
        # 会话检索引导：模型该在什么时候去翻历史
        self.prompts.add_section(
            "harness:session-query",
            lambda variables: tr(SESSION_QUERY_PROMPT_KEY) if self.config.enable_session_query else "",
            order=ORDER_TOOLS,
        )
        if self.config.enable_goal:
            self.prompts.add_section("goal:policy", lambda variables: self.goals.context(variables.get("session_id", "")), order=ORDER_POLICY + 2, volatile=True)
        if self.config.enable_plan_mode:
            from .plans import approved_context
            self.prompts.add_section(
                "plan:approved",
                lambda variables: approved_context(self.plan.session_of(variables.get("session_id", ""))),
                order=ORDER_POLICY + 1,
                volatile=True,
            )
            # plan:policy 只在计划模式激活时渲染；工具不存在时该段渲染为空。
            self.prompts.add_section(
                "plan:policy",
                lambda variables: (
                    plan_policy_section()
                    if self.plan.is_active_session_id(variables.get("session_id", ""))
                    else ""
                ),
                order=ORDER_POLICY,
                volatile=True,
            )

    def _render_tool_section(self) -> str:
        """提醒模型使用请求中的 schema，不重复整个工具目录。"""
        schemas = self.tools.schemas()
        if not schemas:
            return ""
        return (tr('python.harness.2a3bf062a135'))

    def _memory_context(self, variables: dict[str, str]) -> str:
        """损坏的外部记忆不能阻断普通对话，但需如实提示未召回。"""
        import sqlite3
        if self.layered_memory is None:
            return ""
        try:
            session = self._agent_session(variables.get("session_id", ""))
            latest = session.latest("user/message") if session is not None else None
            query = latest.data.get("text", "") if latest is not None else ""
            return self.layered_memory.context(variables.get("cwd", ""),
                                               session_id=variables.get('session_id', ''),
                                               query=query if isinstance(query, str) else "")
        except (OSError, ValueError, sqlite3.Error, HarnessError) as exc:
            logging.getLogger(__name__).warning(tr('log.3a7c9f1c6202'), exc)
            return tr("python.memory.unavailable")

    def _register_agent_prompt_sections(self) -> None:
        """注册Agent相关的动态提示片段。

        两个片段都按 ``session_id`` 现算（_ensure_system_prompt 每回合重装配）：

        * ``agent:active``   —— 挂载Agent的人格，只在挂载了的会话里渲染；
        * ``agent:catalog``  —— <available_agents> 目录，无Agent时为空。
        """
        if self.agents is None or self.agent_mount is None:
            return
        mount = self.agent_mount

        self.prompts.add_section(
            "agent:active",
            lambda variables: mount.render_section(variables.get("session_id", "")),
            order=ORDER_PERSONA_PREFIX + 50,
            volatile=True,
            interpolate=False,  # Agent正文里的 {{...}} 是字面量
        )
        self.prompts.add_section(
            "agent:catalog",
            lambda variables: render_agent_catalog(self.agents_for(variables.get("session_id")),
                searchable=self.config.tool_schema_mode == 'auto' and 'agent' in self.tools.names()),
            order=ORDER_SKILL_CATALOG + 100,
            volatile=True,
            interpolate=False,
        )

    # ==================================================================
    # 扩展点：注册业务工具 / 技能 / 提示词
    # ==================================================================
    def tool(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        **kwargs: Any,
    ):
        """注册业务工具（装饰器）。见模块文档的用法示例。"""
        return self.tools.tool(name, description, parameters, **kwargs)

    def add_tool(self, tool: ToolDefinition) -> ToolDefinition:
        """直接注册一个 :class:`ToolDefinition`。"""
        return self.tools.register(tool)

    def add_prompt(
        self,
        name: str,
        text: str,
        *,
        order: int = 0,
        complete: bool = False,
    ) -> None:
        """追加一段系统提示词（业务人格、领域知识等）。"""
        self.prompts.add_section(name, text, order=order, complete=complete)

    def set_persona(self, persona: str) -> None:
        """一键替换整套人格（注册 complete 片段）。"""
        self.prompts.add_section("deployment:persona", persona, order=ORDER_PERSONA_PREFIX, complete=True)

    def _default_skill_enabled(self, skill: Any) -> bool:
        """初始与动态目录共用默认启停及插件所有者过滤。"""
        return self.esc_state.is_enabled("skill", skill.name) and (
            skill.plugin_id is None or self.packages.is_enabled(skill.plugin_id))

    def add_skill(self, skill: Any) -> None:
        """动态注册一个技能对象（:class:`SkillMetadata`）。"""
        if self.skills is None:
            from ..skills.registry import SkillRegistry

            self.skills = SkillRegistry()
            self.skills.set_enabled_filter(self._default_skill_enabled)
            for tool in build_skill_tool(self.skills, registry_for_session=self.skills_for):
                self.tools.register(tool)
            self.agent.skills = self.skills
        # 技能已在内存中，直接注册进目录，无需再扫描文件系统
        self.skills.register(skill)

    def refresh_skills(self) -> int:
        """重新扫描技能目录，返回技能总数。"""
        if self.skills is None:
            return 0
        self.skills.refresh()
        return len(self.skills)

    def refresh_agents(self) -> int:
        """重新扫描Agent目录，返回Agent总数。"""
        if self.agents is None:
            return 0
        self.agents.refresh()
        return len(self.agents)

    def set_skill_enabled(self, name: str, enabled: bool) -> None:
        """启停一个技能（落盘 esc-state，下一回合目录自动过滤）。"""
        self.esc_state.set_enabled("skill", name, enabled)

    def set_agent_enabled(self, name: str, enabled: bool) -> None:
        """启停一个Agent（落盘 esc-state，目录与召唤同时失效）。"""
        self.esc_state.set_enabled("agent", name, enabled)

    # ==================================================================
    # 连接器预设
    # ==================================================================
    def install_connector_preset(self, name: str) -> dict[str, Any]:
        """安装一个精选连接器预设（如 ``github``）。

        一键安装行为：

        * 把预设声明 upsert 进 ``connectors.json``（既有安装的
          ``bound`` 标记由 :meth:`ConnectorStore.upsert` 保留）；
        * 预设附带的配套技能写入用户技能目录（已存在则不覆盖，
          尊重用户改动），随后重新扫描技能注册表。

        安装后连接器处于「未绑定」状态，用户仍需设置凭据环境变量
        并执行绑定，与 UI 的 添加 → 绑定 → 启用 链路一致。

        :raises ConnectorError: 预设不存在，或连接器管理未启用。
        """
        if self.connectors is None:
            raise ConnectorError(tr('python.harness.e944e835a38d'), code="connector/disabled")
        preset = get_preset(name)
        record = self.connector_store.upsert(preset.record)
        skill_path: Path | None = None
        if preset.skill_name and preset.skill_markdown and self.config.home:
            from ..skills.paths import user_skill_root
            target = user_skill_root(self.config.home) / preset.skill_name / 'SKILL.md'
            if not target.exists() and not target.is_symlink():
                import os
                import tempfile
                target.parent.mkdir(parents=True, exist_ok=True)
                descriptor, filename = tempfile.mkstemp(dir=target.parent, prefix='.preset-skill-')
                temporary = Path(filename)
                try:
                    with os.fdopen(descriptor, 'w', encoding='utf-8', newline='') as stream:
                        stream.write(preset.skill_markdown)
                        stream.flush()
                        os.fsync(stream.fileno())
                    # 完整正文一次公开；并发创建或已有链接均不能被覆盖。
                    try:
                        os.link(temporary, target)
                    except FileExistsError:
                        pass
                    else:
                        skill_path = target
                finally:
                    temporary.unlink(missing_ok=True)
            self.refresh_skills()
        return {
            "connector": record.to_dict(),
            "skill": preset.skill_name if skill_path is not None else None,
            "skill_path": str(skill_path) if skill_path is not None else None,
        }

    # ==================================================================
    # Agent挂载（会话级）
    # ==================================================================
    def _agent_session(self, session_id: str) -> Session | None:
        """统一解析主 Agent 会话与持久化成员上下文。"""
        try:
            return self.sessions.get(session_id)
        except SessionNotFoundError:
            subagents = getattr(self, "subagents", None)
            if subagents is not None and subagents.contexts is not None:
                try:
                    return subagents.contexts.get(session_id)
                except SessionNotFoundError:
                    pass
        return None

    def _skill_selection_for(self, session: Session) -> tuple[str, ...] | None:
        """读取当前回合快照，未运行时解析当前配置。"""
        snapshot = _SKILL_SELECTION_SNAPSHOT.get()
        if snapshot is not None and snapshot[0] is self and snapshot[1] == session.id:
            return snapshot[2]
        return self._current_skill_selection_for(session)

    @contextlib.contextmanager
    def _freeze_skill_selection(self, session: Session):
        """技能配置与人格一样在下一回合采用，避免运行中保存改变工具可用性。"""
        token = _SKILL_SELECTION_SNAPSHOT.set((self, session.id, self._current_skill_selection_for(session)))
        try:
            yield
        finally:
            _SKILL_SELECTION_SNAPSHOT.reset(token)

    def _current_skill_selection_for(self, session: Session) -> tuple[str, ...] | None:
        """当前Agent与团队成员独立选技能，普通子代理继承派发者。"""
        state = self.subagents.state_for_session(session.id) if hasattr(self, 'subagents') else None
        if state is not None:
            if 'skills' in (state.prompt_input or {}):
                selected = state.prompt_input['skills']
                return tuple(selected) if selected is not None else None
            parent = self._agent_session(state.parent_session_id)
            return self._skill_selection_for(parent) if parent else None
        resolved = self.agent_mount.resolve_active(session.id) if self.agent_mount else None
        if resolved is None:
            return None
        definition = resolved[1]
        if definition.is_team:
            from ..agents.members import lead_member, member_skills
            run = self._prompt_team_runs.get(session.id)
            team = run.team if run else definition
            registry = run.registry if run else self.agents_for(session)
            return member_skills(lead_member(team), team, registry)
        return definition.skills

    def skills_for(self, session: Session | str | None = None, *, configured: bool = True) -> SkillRegistry | None:
        """技能跟随项目与Agent；管理目录可忽略Agent选择，仍展示全部技能。"""
        if self.skills is None or session is None:
            return self.skills
        session_obj = self._agent_session(session) if isinstance(session, str) else session
        if session_obj is None:
            raise SessionNotFoundError(str(session))
        cwd = session_obj.header.cwd or self.config.cwd or Path.cwd()
        registry = self.skills.for_project(cwd, fallback_root=self.agent_project_root(session_obj))
        selected = self._skill_selection_for(session_obj) if configured else None
        if selected is None:
            return registry
        names = frozenset(selected)
        return registry.with_enabled_filter(lambda skill: skill.name in names
            and (skill.plugin_id is None or self.packages.is_enabled(skill.plugin_id)))

    def agents_for(self, session: Session | str | None = None) -> AgentRegistry | None:
        """Agent跟随聊天项目；同仓库工作树共用项目级配置。"""
        if self.agents is None or session is None:
            return self.agents
        session_obj = self._agent_session(session) if isinstance(session, str) else session
        if session_obj is None:
            raise SessionNotFoundError(str(session))
        return self.agents.for_project(self.agent_project_root(session_obj))

    def agent_project_root(self, session: Session) -> Path:
        """返回当前聊天绑定的Agent配置根目录，不接受外部目录覆盖。"""
        from .memory import ProjectMemory
        roots = getattr(self, "_agent_roots", None)
        if roots is None:
            roots = self._agent_roots = ProjectMemory()
        return roots.root(session.header.cwd or self.config.cwd or Path.cwd())

    def agent_mount_state(self, session: Session | str) -> dict[str, Any]:
        """读取某个会话的Agent挂载状态（UI / API 用）。

        返回 ``{"available", "agent", "model", "description", "agent_type", "revision"}``：
        Agent系统未启用时 ``available=False``；未挂载时 ``agent=None``。
        ``revision`` 来自最后一个已落库选择事件，退场后也保留版本。
        持续团队返回工作区选项；失效团队保留标识及不可用状态，允许明确退场。
        """
        session_obj = self.get_session(session) if isinstance(session, str) else session
        latest = session_obj.latest("agent/mode")
        revision = latest.seq + 1 if latest is not None else 0
        if self.agents is None or self.agent_mount is None:
            return {"available": False, "agent": None, "model": None, "revision": revision}
        resolved = self.agent_mount.resolve_active(session_obj.id)
        if resolved is None:
            projection = self.agent_mount.active_projection(session_obj.id)
            if projection and projection.agent_type == 'team':
                return {"available": True, "agent": projection.agent, "display_name": projection.agent,
                        "agent_type": "team", "selection_unavailable": True, "revision": revision,
                        "team_worktrees": projection.team_worktrees, "team_working_copy": projection.team_working_copy}
            return {"available": True, "agent": None, "model": None, "revision": revision}
        projection, agent = resolved
        from ..agents.presentation import agent_presentation, team_member_identity
        roster = []
        if agent.is_team:
            catalog = {item.name: item for item in self.agents_for(session_obj).list_all()}
            for member in agent.members:
                referenced = catalog.get(member.agent)
                roster.append({'name': member.name, 'role': member.role,
                               'identity': team_member_identity(agent.name, member, referenced),
                               'display_name': member.display_name or (agent_presentation(referenced)['display_name'] if referenced else tr('agent.team.lead_display') if member.role == 'lead' else member.name)})
        return {
            **agent_presentation(agent),
            "available": True,
            "agent": projection.agent,
            "model": projection.model,
            "description": agent.description,
            **({"runner": agent.metadata["runner"]} if agent.metadata.get("runner") else {}),
            "agent_type": agent.agent_type,
            **({"team_worktrees": projection.team_worktrees, "team_working_copy": projection.team_working_copy, "members": roster} if agent.is_team else {}),
            "revision": revision,
        }

    async def set_agent_mount(self, session: Session | str, name: str | None) -> dict[str, Any]:
        """设置常驻Agent或退场（``None`` 表示退场）；``/agent`` 是独立的一次性调用。

        * 原挂载入口只接 agent 型人格，team 型在这里仍返回
          ``agent/team-mount``；持续团队选择使用 ``set_agent_selection``，
          不渲染常驻人格，而是在后续任务开始时路由到团队编排；
        * 挂载发生在回合中途不会打断当前回合，下一回合装配时自然生效
          （``agent:active`` 动态提示片段）；
        * 状态以 log-only 的 ``agent/mode`` 事件落库，重启后重放恢复。
        """
        if self.agents is None or self.agent_mount is None:
            raise HarnessError(tr('python.harness.efc9526645eb'), code="agent/disabled")
        session_obj = self.get_session(session) if isinstance(session, str) else session
        if name is None:
            await self.agent_mount.set(session_obj, None)
            await self.bus.emit("agent/loaded", session_obj.id, {"action": "off"})
            return self.agent_mount_state(session_obj)
        # AgentNotFoundError 冒泡给调用方（API 层映射成 404）
        agent = self.agents_for(session_obj).get(str(name).strip())
        if agent.is_team:
            raise HarnessError(
                tr('python.harness.ce891e5e28ec', p0=f'{agent.name!r}', p1=f'{agent.name}'),
                code="agent/team-mount",
            )
        await self.agent_mount.set(session_obj, agent.name)
        await self.bus.emit(
            "agent/loaded",
            session_obj.id,
            {
                "action": "mount",
                "name": agent.name,
                "description": agent.description,
                "agent_type": agent.agent_type,
            },
        )
        return self.agent_mount_state(session_obj)

    # ==================================================================
    # 会话管理
    # ==================================================================
    # ==================================================================
    # 计划模式
    # ==================================================================
    async def set_plan_mode(self, session: Session | str, active: bool) -> dict[str, Any]:
        """切换某个会话的计划模式。

        回合进行中不会立刻生效，而是排队等下一个 step 边界结算。
        """
        if not self.config.enable_plan_mode:
            raise HarnessError(tr('python.harness.8cc13931c52f'), code="plan-mode/disabled")
        session_obj = self.get_session(session) if isinstance(session, str) else session
        outcome = await self.plan.set(session_obj, active)
        return {"outcome": outcome, **self.plan.snapshot(session_obj.id)}

    def plan_mode_state(self, session: Session | str) -> dict[str, Any]:
        """读取计划模式状态（UI / API 用）。"""
        session_obj = self.get_session(session) if isinstance(session, str) else session
        return self.plan.snapshot(session_obj.id)

    async def set_agent_selection(self, session: Session | str, name: str | None, *, team_worktrees: bool = False, team_working_copy: bool = False) -> dict[str, Any]:
        """明确选定后续任务的单Agent或Agent 团队，覆盖同一会话的唯一槽位。

        工作树隔离与交付约束由宿主共享服务负责。
        """
        if not isinstance(team_worktrees, bool) or not isinstance(team_working_copy, bool):
            raise HarnessError(tr('agent.team.selection_options'), code='agent/invalid-options')
        if name is None:
            return await self.set_agent_mount(session, None)
        if self.agents is None or self.agent_mount is None:
            return await self.set_agent_mount(session, name)
        session_obj = self.get_session(session) if isinstance(session, str) else session
        agent = self.agents_for(session_obj).get(str(name).strip())
        if not agent.is_team:
            return await self.set_agent_mount(session_obj, name)
        if not self.config.enable_subagent:
            raise HarnessError(tr('python.harness.49504b0dbdef', p0=agent.name), code='agent/team-unavailable')
        await self.agent_mount.set(session_obj, agent.name, team_worktrees=team_worktrees, team_working_copy=team_working_copy)
        await self.bus.emit('agent/loaded', session_obj.id, {'action': 'select-team', 'name': agent.name, 'agent_type': 'team'})
        return self.agent_mount_state(session_obj)

    async def _apply_slash_command(
        self, session_obj: Session, text: str, *, run_context: dict[str, Any] | None = None
    ) -> tuple[str, str] | TurnResult | None:
        """处理 ``/runner``、``/plan``、``/goal``、``/agent`` 与 ``/skill`` 斜杠命令。

        :returns: ``(处理后的用户输入, 提示文本)``；不是斜杠命令则返回 ``None``。
        """
        stripped = text.strip()
        from .external_agent_commands import external_agent_command
        external = await external_agent_command(self, session_obj, text, run_context)
        if external is not None:
            return external
        if self.config.enable_goal:
            handled = await self.goal_runtime.command(session_obj.id, text)
            if handled is not None:
                return handled
        if not stripped.startswith("/"):
            return None
        parts = stripped.split(maxsplit=2)
        if self.config.enable_plan_mode and parts[0] == "/plan":
            rest = stripped[len("/plan"):].strip()
            head = parts[1] if len(parts) > 1 else ""
            message = parts[2] if len(parts) > 2 else ""
            if head.lower() in {"off", "exit", "end"}:
                outcome = await self.plan.set(session_obj, False)
                return "", tr('python.harness.9740f1819ba3', p0=tr('plan.mode.outcome.' + outcome))
            if head.lower() in {"on", "start"}:
                message = message.strip()
            elif head:
                # `/plan <任意内容>`：带 message 进入计划模式。
                message = rest
            outcome = await self.plan.set(session_obj, True)
            return message, tr('python.harness.859b557d92d4', p0=tr('plan.mode.outcome.' + outcome))
        handled = await self._apply_agent_command(session_obj, stripped, run_context=run_context)
        if handled is not None:
            return handled
        handled = await self._apply_skill_command(session_obj, stripped)
        if handled is not None:
            return handled
        return None

    async def _apply_skill_command(
        self, session_obj: Session, stripped: str
    ) -> tuple[str, str] | None:
        """处理 ``/skill`` 斜杠命令（用户侧技能入口，对齐模型侧 ``skill`` 工具）。

        * ``/skill``                —— 用法提示（提示从目录里挑一个）；
        * ``/skill <name> [任务]``   —— 校验 ``user_invocable`` 后把技能
          正文注入为本回合的有效输入，模型按技能指引处理后续任务。

        注入方式与 ``skill`` 工具的结果一致（``<skill>`` 包裹正文），
        因此模型侧看到的是同一份"已激活的指引"；``skill/loaded`` 事件
        让 UI 在对话流里显示技能已加载的标记。
        """
        parts = stripped.split(maxsplit=2)
        if not parts or parts[0] != "/skill":
            return None
        skills = self.skills_for(session_obj)
        if skills is None:
            return "", tr('python.harness.e71c8badb283')
        name = parts[1] if len(parts) > 1 else ""
        message = parts[2] if len(parts) > 2 else ""

        if not name:
            count = len(skills)
            return "", tr('python.harness.219ed8617ef3', p0=f'{count}')

        try:
            skill = skills.get(name)
        except Exception as exc:
            return "", tr('python.harness.d92ab9ab9cad', p0=f'{exc}')

        if not skill.user_invocable:
            return "", tr('python.harness.e125303c942c', p0=f'{skill.name!r}')

        from ..skills.context import snapshot, record_user_load
        value = snapshot(skill)
        record_user_load(value)
        body = value.text
        effective = (
            tr('python.harness.f2fd1bc87a2a', p0=f'{body}', p1=f'{message}')
            if message
            else tr('python.harness.f45b7dc0a1f8', p0=f'{body}')
        )
        await self.bus.emit(
            "skill/loaded",
            session_obj.id,
            {
                "session_id": session_obj.id,
                "name": skill.name,
                "path": str(skill.path) if skill.path else None,
                "source": "user",
            },
        )
        return effective, tr('python.harness.4b2f0e29e2c5', p0=f'{skill.name}')

    async def _apply_skill_mentions(self, session_obj: Session, text: str, *, mention_text: str | None = None) -> str:
        """将显式的 ``$技能名`` 引用接入现有技能加载流程。"""
        if not self.config.enable_skills:
            return text
        skills = self.skills_for(session_obj)
        if skills is None:
            return text
        from ..skills.mentions import mentioned_skills
        # 先从原始正文提取，避免将技能正文里的示例再次解释为调用。
        names = mentioned_skills(text if mention_text is None else mention_text, skills)
        effective = text
        for name in reversed(names):
            handled = await self._apply_skill_command(session_obj, f"/skill {name} {effective}")
            if handled is not None:
                effective, _ = handled
        return effective

    async def _apply_agent_command(
        self, session_obj: Session, stripped: str, *, run_context: dict[str, Any] | None = None
    ) -> tuple[str, str] | TurnResult | None:
        """处理 ``/agent`` 斜杠命令。

        * ``/agent off``（无任务正文）—— 退场当前Agent；
        * ``/agent <name> 任务`` —— 一次性调用，不改变常驻选择。
        """
        parts = stripped.split(maxsplit=2)
        if not parts or parts[0] != "/agent":
            return None
        if self.agents is None or self.agent_mount is None:
            return "", tr('python.harness.e070c424a431')
        head = parts[1] if len(parts) > 1 else ""
        message = parts[2] if len(parts) > 2 else ""

        if head == "off" and not message:
            await self.agent_mount.set(session_obj, None)
            await self.bus.emit(
                "agent/loaded", session_obj.id, {"action": "off"}
            )
            return "", tr('python.harness.6577cb93ba5b')

        if not head:
            return '', tr('agent.once.task_required')

        try:
            agent = self.agents_for(session_obj).get(head)
        except Exception as exc:
            return "", tr('python.harness.d77f64589348', p0=f'{exc}')

        if agent.is_team:
            if not self.config.enable_subagent:
                return "", tr('python.harness.49504b0dbdef', p0=f'{agent.name!r}')
            if not message:
                return "", tr('python.harness.3fae22b1d92c')
            while message:
                parts = message.split(maxsplit=1)
                if parts[0] not in ('--worktrees', '--working-copy'):
                    break
                message = parts[1].strip() if len(parts) == 2 else ''
                run_context = {**(run_context or {}), 'team_worktrees': True}
                if parts[0] == '--working-copy':
                    run_context['team_working_copy'] = True
            if not message:
                return "", tr('python.harness.3fae22b1d92c')
            self.agent_mount.select_once(session_obj.id, agent,
                **{key: (run_context or {}).get(key, False) for key in ('team_worktrees', 'team_working_copy')})
            return message, ''

        if not message:
            return '', tr('agent.once.task_required')
        self.agent_mount.select_once(session_obj.id, agent)
        return message, ''

    def create_session(self, *, cwd: str | Path | None = None, title: str | None = None,
                       metadata: dict[str, Any] | None = None) -> Session:
        """创建新对话，初始元数据与空对话头一起提交。"""
        return self.sessions.create(cwd=str(cwd or self.config.cwd or Path.cwd()), title=title, metadata=metadata)

    def get_session(self, session_id: str) -> Session:
        """按 id 取会话（必要时从磁盘加载）。"""
        return self.sessions.get(session_id)

    def list_sessions(self) -> list[dict[str, Any]]:
        """列出全部会话。"""
        return [h.to_dict() for h in self.sessions.list_sessions()]

    async def delete_session_async(self, session_id: str) -> None:
        """在所属事件循环收敛活动与原生资源，再删除持久会话。"""
        owner = self.runtime.loop
        if owner is not None and owner.is_running() and owner is not asyncio.get_running_loop():
            await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(self.delete_session_async(session_id), owner))
            return
        task = self.runtime.active.get(session_id)
        await self.cancel(session_id)
        if task is not None and not task.done():
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await self.subagents.shutdown(session_id)
        await self.browser.close_session(session_id)
        await self.terminals.close_owner(session_id)
        self.delete_session(session_id)

    def delete_session(self, session_id: str) -> None:
        """删除一个会话。"""
        if self.terminals.list(session_id):
            raise HarnessError(tr('harness.session.resources_active'), code="session/resources-active")
        self.plan.assert_idle(session_id)
        self.permissions.assert_idle(session_id)
        self.questions.cancel_session(session_id)
        self.goals.clear(session_id)
        self.subagents.delete_contexts(session_id)
        self.sessions.delete(session_id)
        self.plan.forget(session_id)
        self.permissions.forget(session_id)
        if self.repeat_reminder is not None:
            self.repeat_reminder.forget(session_id)
        self._agents.pop(session_id, None)

    @property
    def has_model_credentials(self) -> bool:
        """统一识别配置密钥或已登录订阅，不代替远端模型可用性检查。"""
        return bool(self.config.api_key or self.chatgpt.logged_in or self.provider_connections.catalog() or any(s.account.logged_in for s in self.codex_gateway.slots.values()))

    def _session_llm_config(self, session: Session) -> LlmCallConfig:
        """解析该会话实际使用的请求配置。

        模型选择是**会话级**的：``header.metadata.model`` 覆盖全局默认。
        之所以不直接改 ``self.config``，是因为同时跑两个项目、模型不同是常态，
        全局开关必然打架；而 AgentLoop 本身就是按会话缓存的，正好承载这份状态。
        """
        base = self.agent.config
        metadata = session.header.metadata or {}
        model = str(metadata.get("model") or "").strip()
        if model.startswith('harness/') or self.config.model.startswith('harness/') and not model:
            model = model or self.config.model
        elif self.adapter.route_pool is not None:
            model = self.adapter.route_pool.select()[0]['model']
        elif model.startswith('chatgpt/'):
            pass
        effort = metadata.get("reasoning_effort")
        # 挂载的Agent可以声明模型覆盖：优先级低于会话显式选择的模型，
        # 高于全局默认——"以Agent身份工作"自然包含"用Agent擅长的模型"。
        if not model and self.agent_mount is not None:
            resolved = self.agent_mount.resolve_active(session.id)
            if resolved is not None:
                projection, agent = resolved
                if agent.is_team:
                    # 提示词驱动团队：主理人即会话主 agent，按主理人的解析链取模型
                    #（成员固定模型 → 引用Agent模型 → 团队默认模型）。
                    from ..agents.prompt_team import lead_model
                    model = lead_model(agent, self.agents_for(session)) or ""
                else:
                    model = agent.model or ""
        if not model and effort is None:
            return base
        resolved_effort = base.reasoning_effort
        if effort is not None:
            resolved_effort = str(effort).strip() or None
        return replace(
            base,
            model=model or base.model,
            reasoning_effort=resolved_effort,
        )

    def refresh_session_config(self, session_id: str) -> None:
        """会话设置变更后，让已存在的 AgentLoop 立刻采用新模型。

        尚在跑的回合不打断 —— 正在流式输出的那条回复本来就属于旧配置，
        下一次请求再切换才符合直觉。
        """
        agent = self._agents.get(session_id)
        if agent is None:
            return
        try:
            session = self.sessions.get(session_id)
        except SessionNotFoundError:
            return
        agent.config = self._session_llm_config(session)

    def _message_speaker_for_session(self, session: Session) -> dict[str, str] | None:
        """只为具名的单 Agent 生成展示身份，团队作者由团队运行器保存。"""
        selected = self.agent_mount.resolve_active(session.id) if self.agent_mount else None
        if selected is None or selected[1].is_team:
            return None
        from ..agents.presentation import agent_presentation
        agent = selected[1]
        return {'agent': agent.name, **agent_presentation(agent)}

    def _bind_agent_services(self, agent: AgentLoop, session: Session | None) -> None:
        """绑定主会话和成员共用的宿主服务，不改写请求配置或执行身份。"""
        agent.repeat_reminder = self.repeat_reminder
        agent.skills_for_session = self.skills_for
        agent.identity = self.identity
        agent.stream_checkpoints = self.config.enable_stream_checkpoints
        agent.context_window = self._effective_context_window()
        agent.compact_threshold = self._compact_threshold(agent.context_window)
        agent.context_control = self.runtime.process_compaction
        agent.context_policy = self._context_policy
        agent.summary_max_tokens = self.config.summary_max_tokens
        agent.on_context_window_learned = self._on_context_window_learned
        agent.tool_schema_mode = self.config.tool_schema_mode
        agent.tool_schema_policy = lambda: self.config.tool_schema_mode
        if session is not None:
            agent.tool_discovery.bind(session)

    def _agent_for(self, session: Session) -> AgentLoop:
        """取（或创建）该会话专属的 AgentLoop。"""
        agent = self._agents.get(session.id)
        if agent is None:
            agent = AgentLoop(
                self.adapter,
                self.tools,
                self.prompts,
                skills=self.skills,
                config=self.agent.config,
                bus=self.bus,
            )
            if self.config.enable_plan_mode:
                agent.plan_controller = self.plan
                agent.add_pre_step_hook(_settle_plan_mode)
            if self.tool_result_pruner is not None:
                agent.tool_result_pruner = self.tool_result_pruner
            self._agents[session.id] = agent
        self._bind_agent_services(agent, session)
        agent.user_origin_for_session = self.runtime.message_origin
        agent.message_speaker_for_session = self._message_speaker_for_session
        agent.language_preference = lambda: self.config.language
        agent.stop_guard = self._prompt_team_stop
        agent.lifecycle_hook = self.hooks.lifecycle
        agent.message_author_for_session = lambda current: self._prompt_team_runs[current.id].lead_author() if current.id in self._prompt_team_runs else None
        agent.cancel_cleanup = lambda session_id=session.id: self.subagents.shutdown(session_id)
        agent.max_steps = self.config.max_steps
        agent.tool_result_max_chars = self.config.tool_result_max_chars
        agent.token_budget = self.config.token_budget
        agent.config = self._session_llm_config(session)
        if session.header.metadata.get('superagent_worker') is not None:
            bound = [service for service in self._superagent_workers if service.bind_session(session, agent)]
            if len(bound) != 1:
                raise HarnessError(tr('superagent.worker-unavailable'), code='superagent/worker-unavailable')
        return agent

    def _effective_context_window(self) -> int | None:
        from ..llm.capabilities import minimum
        adapter = self.adapter
        if self.config.model.startswith('harness/'):
            entry = next((m for m in self.provider_connections.catalog() if m['id'] == self.config.model), {})
            return minimum(entry.get('context_window'), self.config.context_window)
        if self.chatgpt.logged_in and self.config.model.startswith('chatgpt/'):
            info = self.chatgpt.resolve()
            return minimum(info.get('context_window'), self.config.context_window)
        model = adapter.route_pool.select()[0] if adapter.route_pool else {
            'model': self.config.model, 'base_url': self.config.base_url,
            'context_window': self.config.model_context_window, 'max_output_tokens': self.config.model_max_output_tokens}
        caps = adapter.capabilities.peek(model['base_url'], model['model'], model)
        return minimum(caps.get('context_window'), self.config.context_window)

    def _compact_threshold(self, window):
        from ..llm.context_policy import compact_threshold
        return compact_threshold(window, self.config.context_working_budget, self.config.output_reserve)

    def _context_policy(self):
        """仅在请求安全边界读取，已发出的请求保持配置快照。"""
        return {name: getattr(self.config, name) for name in (
            'context_auto_manage', 'context_working_budget', 'summary_max_tokens', 'output_reserve',
            'tool_result_max_chars')}

    def _on_context_window_learned(self, limit: int, base_url=None, model=None) -> None:
        """端点 400 自报真实窗口后：缓存并持久化，之后的请求直接按真实值钳制。"""
        url = base_url or self.config.base_url
        selected = model or self.config.model
        key = f"{url}|{selected}"
        if self._learned_windows.get(key) != limit:
            windows = {**self._learned_windows, key: limit}
            save_context_windows(self.config.home / "context-windows.json", windows)
            self._learned_windows = windows
        self.adapter.capabilities.learn(url, selected, context_window=limit)

    def _language_for_parent(self, session_id: str) -> str:
        """入队时捕获直接父回合语言，避免等待期间设置变更影响子任务。"""
        from ..core.language import current_language
        parent = self._agents.get(session_id)
        if parent is None:
            parent = next((state.agent for state in self.subagents.list_states()
                           if state.session_id == session_id), None)
        return getattr(parent, "language", current_language.get())

    def _guard_subagent_preparation(self, parent_session_id, workspace_cwd):
        """子代理创建前按最终继承目录检查宿主准备租约。"""
        if workspace_cwd is None:
            source = self.sessions._cache.get(parent_session_id)
            if source is None:
                source_state = self.subagents.state_for_session(parent_session_id)
                source = source_state.session if source_state else None
            if source is None:
                try:
                    source = self.get_session(parent_session_id)
                except SessionNotFoundError:
                    pass
            workspace_cwd = source.header.cwd if source is not None else self.subagents.cwd
        self.workspace_preparations.assert_available(workspace_cwd)

    async def _activate_prompt_team(self, session, team):
        """普通回合首次激活即绑定验收，已有团队回合保持冻结身份。"""
        current = self._prompt_team_runs.get(session.id)
        if current:
            return current.team.name == team.name
        parent = self._agents.get(session.id)
        if parent is None or session.open_turn is None:
            return False
        from .prompt_team_run import PromptTeamRun
        run = PromptTeamRun(self, session, parent, team, self.agent_mount.active_projection(session.id))
        self._prompt_team_runs[session.id] = run
        await run.start()
        run.bind_goal()
        return True

    async def _spawn_configured_subagent(self, args, ctx):
        """由宿主解析成员配置；模型只提供任务和稳定成员标识。"""
        run = self._prompt_team_runs.get(ctx.session_id)
        if run is None:
            selected = self.agent_mount.resolve_active(ctx.session_id) if self.agent_mount else None
            if selected and selected[1].is_team:
                from .prompt_team_run import PromptTeamRun
                session = self.get_session(ctx.session_id)
                run = PromptTeamRun(self, session, self._agents[ctx.session_id], selected[1], selected[0])
                self._prompt_team_runs[ctx.session_id] = run
                await run.start()
        if run is not None:
            return await run.spawn(args, ctx)
        if args.get('member') or args.get('dependencies'):
            return None
        name = args.get('agent')
        if not name:
            return None
        from ..core.errors import ToolExecutionError
        persona = self._agent_persona_for_subagent(name, ctx.session_id)
        if persona is None:
            raise ToolExecutionError(tr('subagent.agent.unavailable', p0=name))
        agent = self.agents_for(ctx.session_id).get(name)
        runner = agent.metadata.get('runner')
        if runner and (args.get('model') is not None or args.get('reasoning_effort') is not None or args.get('fork_turns', 'none') != 'none'):
            raise ToolExecutionError(tr('harness.subagent.runner_context_unsupported'))
        return self.subagents.spawn(prompt=persona + '\n\n' + args['prompt'],
            description=args.get('description') or name, parent_session_id=ctx.session_id,
            source_call_id=ctx.call_id, model=None if runner else args.get('model') or agent.model, runner=runner,
            reasoning_effort=args.get('reasoning_effort'), fork_turns=args.get('fork_turns', 'none'),
            prompt_input={**(args.get('_prompt_input') or {}), 'skills': list(agent.skills) if agent.skills is not None else None})

    async def _followup_subagent(self, previous, text, metadata, ctx):
        """团队续接仍走原成员编排，普通续接直接复用同一成员会话。"""
        member = (previous.prompt_input or {}).get('member')
        if member:
            run = self._prompt_team_runs.get(ctx.session_id)
            if (run is None or previous.parent_session_id != ctx.session_id
                    or run.scope == previous.scope and run.states.get(member) is not previous
                    or run.scope != previous.scope and not previous.settled):
                raise ValueError(tr('harness.subagent.team_followup_unavailable'))
            # 新回合按当前团队解析模型与权限，并由上下文签名核对原成员身份。
            overrides = ({'model': previous.model, 'reasoning_effort': previous.reasoning_effort}
                         if run.scope == previous.scope else {})
            return await run.spawn({'member': member, 'prompt': text, **overrides, '_prompt_input': metadata,
                                    '_resume_context': previous.session_id}, ctx)
        if previous.scope is not None:
            raise ValueError(tr('harness.subagent.team_followup_unavailable'))
        return self.subagents.spawn(prompt=text, description=previous.description,
            parent_session_id=ctx.session_id, context_session_id=previous.session_id,
            model=previous.model, reasoning_effort=previous.reasoning_effort,
            read_only=previous.read_only, workspace_cwd=previous.workspace_cwd,
            runner=previous.runner, source_call_id=ctx.call_id,
            prompt_input={**metadata, **({'skills': previous.prompt_input['skills']} if 'skills' in (previous.prompt_input or {}) else {})})

    async def _prompt_team_stop(self, agent, session, turn, step, continued, final_text):
        """保留项目结束钩子，同时核验本轮真实成员交付。"""
        run = self._prompt_team_runs.get(session.id)
        if run:
            feedback = await run.completion_feedback(final_text)
            if feedback:
                return feedback
        return await self.hooks.stop(agent, session, turn, step, continued, final_text)

    def _agent_persona_for_subagent(self, name: str, session_id: str | None = None) -> str | None:
        """按Agent名解析人设正文（spawn_agent 工具 ``agent`` 参数用）。

        只解析单Agent；团队不能作为子代理人设（嵌套团队由引擎拒绝）。
        解析不到返回 ``None``，由工具层给出明确错误，不静默忽略。
        """
        if self.agents is None:
            return None
        try:
            registry = self.agents_for(session_id) if session_id else self.agents
            agent = registry.get(str(name).strip())
        except Exception:
            return None
        if agent.is_team:
            return None
        from ..agents.personality import render_agent, system_personality
        persona = agent.system_prompt.strip()
        if not persona:
            return None
        personality = system_personality(render_agent(agent))
        return f"{persona}\n\n{personality}" if personality else persona

    def _subagent_parent_config(self, session_id: str) -> LlmCallConfig:
        """派工时冻结父配置，后续选择变化不得改写尚未执行的子任务模型。"""
        parent = self._agents.get(session_id)
        if parent is None:
            state = self.subagents.state_for_session(session_id)
            parent = state.agent if state else None
        if parent is not None:
            return parent.config
        session = self._agent_session(session_id)
        return self._session_llm_config(session) if session is not None else self.agent.config

    def _configure_subagent(self, state: Any, agent: AgentLoop) -> None:
        if 'skills' not in (state.prompt_input or {}):
            parent_session = self._agent_session(state.parent_session_id)
            selected = self._skill_selection_for(parent_session) if parent_session else None
            state.prompt_input = {**(state.prompt_input or {}), 'skills': list(selected) if selected is not None else None}
        started = False
        agent.reply_max_chars = getattr(state, '_reply_max_chars', None)
        metadata = {'agent_id': state.session_id, 'agent_type': state.description,
                    'subagent_id': state.id}
        async def lifecycle(actor, session, event, turn, step, extra):
            nonlocal started
            if event == 'SessionStart':
                if not started:
                    started = True
                    await self.hooks.lifecycle(actor, session, 'SubagentStart', turn, step, metadata)
            elif event not in ('UserPromptSubmit', 'Interrupt'):
                await self.hooks.lifecycle(actor, session, event, turn, step, extra)
        async def stop(actor, session, turn, step, continued, text):
            feedback = await self.hooks.stop(actor, session, turn, step, continued, text,
                                             event='SubagentStop', extra=metadata)
            if feedback:
                return feedback
            from ..agents.limits import reply_feedback
            return reply_feedback(text, getattr(state, '_reply_max_chars', None))
        self._bind_agent_services(agent, state.session)
        agent.lifecycle_hook = lifecycle
        agent.stop_guard = stop
        agent.team_goal = getattr(state, "_team_goal", None)
        agent.team_goal_writer = getattr(state, "_team_goal_writer", False)
        agent.root_session_id = state.root_session_id or state.parent_session_id
        agent.parent_session_id = state.parent_session_id
        agent.language_preference = lambda: getattr(state, 'language', None) or self.config.language
        if self.config.enable_plan_mode and state.session is not None:
            self.plan.bind_parent(state.session.id, state.parent_session_id)
            agent.plan_controller = self.plan
            agent.add_pre_step_hook(_settle_plan_mode)
        agent.cancel_cleanup = lambda: self.subagents.shutdown(state.session.id) if state.session is not None else asyncio.sleep(0)
        parent = self._agents.get(state.parent_session_id)
        if parent is None:
            parent_state = self.subagents.state_for_session(state.parent_session_id)
            parent = parent_state.agent if parent_state else None
        source = getattr(state, '_team_steering_source', None)
        if source is not None:
            agent.steering_source = source
        elif parent is not None and parent.steering_source is not None:
            agent.steering_source = parent.steering_source.fork(state)
        if parent:
            if agent.team_goal is None and getattr(parent, 'team_goal', None) is not None:
                agent.team_goal = parent.team_goal
                agent.team_goal_writer = False
            inherited_language = getattr(state, "language", None) or getattr(parent, "language", "zh-CN")
            state.language = inherited_language
            agent.language_preference = lambda: inherited_language
            agent.recovery_read_only = parent.recovery_read_only
            agent.task_read_only = parent.task_read_only
            agent.request_id = getattr(parent, "request_id", None)
            agent.budget = parent.budget
            agent.compact_threshold = parent.compact_threshold
            limits = [limit for limit in (agent.max_steps, parent.max_steps) if limit is not None]
            agent.max_steps = min(limits) if limits else None
        if state.session is not None:
            source = self.sessions._cache.get(state.parent_session_id)
            if source is None:
                source_state = self.subagents.state_for_session(state.parent_session_id)
                source = source_state.session if source_state else None
            if source is not None and getattr(state, 'workspace_cwd', None) is None:
                from ..session.events import SessionHeader
                state.session.header = SessionHeader.from_dict({**state.session.header.to_dict(), "cwd": source.header.cwd})

    # ==================================================================
    # 运行
    # ==================================================================
    async def ensure_started(self) -> None:
        """所有调用者等待同一次异步启动；取消等待者不重放已开始的连接。"""
        owner = self.runtime.claim_loop(asyncio.get_running_loop())
        if owner is not asyncio.get_running_loop():
            await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(self.ensure_started(), owner))
            return
        if self._close_task is not None or self.runtime.closed:
            raise HarnessError(tr('python.runtime.5ad5897727ec'), code='run/closed')
        if self._started:
            return
        if self._start_task is None:
            self._start_task = asyncio.create_task(self._start(), name='harness-start')
            # 等待者全部取消时仍读取失败，后续调用共享同一失败而不隐式重连。
            self._start_task.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
        await asyncio.shield(self._start_task)

    async def _start(self) -> None:
        """连接完成并注册目录后才对外确认启动；失败与取消保留原任务结果。"""
        if self.automations is not None:
            self.automations.start()
        if self.connectors is not None:
            await self.connectors.start_enabled()
        if self.config.enable_mcp and self.config.mcp_servers:
            await self.mcp.connect_all()
            for supervisor in self.mcp.supervisors:
                for name in supervisor.public_names():
                    remote = supervisor.remote_tool(name)
                    if remote is not None:
                        self.tools.register(build_remote_tool(supervisor, remote), replace=True)
            instructions = self.mcp.instructions()
            if instructions:
                self._register_mcp_prompt(instructions)
        self._started = True

    def _register_mcp_prompt(self, instructions: list[str]) -> None:
        """把各 MCP 服务器的字面说明并入系统提示。"""
        body = "\n\n".join(instructions).strip()
        if not body:
            self.prompts.unregister("mcp_servers")
            return
        try:
            self.prompts.add_section(
                "mcp_servers",
                lambda variables: tr('python.harness.d5b841c2fc03', body=body),
                interpolate=False,
            )
        except ConfigError:
            # 该片段已注册（例如 ensure_started 被并发触发两次）：更新内容，
            # 保留服务器原文，不提升其指令优先级。
            self.prompts.replace_section(
                "mcp_servers",
                lambda variables: tr('python.harness.d5b841c2fc03', body=body),
                interpolate=False,
            )
        except Exception:
            # 提示词注册失败不应让 MCP 工具失效。
            logger.warning(tr('log.3f918e2c4195'), exc_info=True)

    async def run_async(
        self,
        session: Session | str,
        message: str,
        *,
        model: str | None = None,
        reasoning_effort: str | None = None,
        source: str = "user",
    ) -> TurnResult:
        """异步处理一次输入，完成整个回合。

        :param session: 会话对象或会话 id。
        :param message: 用户输入。
        :param model:   覆盖模型（默认用配置里的）。
        :param reasoning_effort: 覆盖思考强度。
        """
        owner = self.runtime.claim_loop(asyncio.get_running_loop())
        if owner is not asyncio.get_running_loop():
            future = asyncio.run_coroutine_threadsafe(self.run_async(session, message, model=model, reasoning_effort=reasoning_effort, source=source), owner)
            return await asyncio.wrap_future(future)
        session_obj = self.get_session(session) if isinstance(session, str) else session
        if session_obj.header.metadata.get('superagent_worker') is not None:
            # 角色必须在命令、技能及外部运行分流前核实，不能只限制末端 AgentLoop。
            self._agent_for(session_obj)
        owner_task = asyncio.current_task()
        from ..core.runs import AgentRun
        live_record = self.runtime._task_records.get(owner_task) or AgentRun(
            id='run_' + uuid.uuid4().hex, session_id=session_obj.id, text=message,
            request_id=uuid.uuid4().hex, root_session_id=session_obj.id)
        async def localized_operation() -> TurnResult:
            actual_message = live_record.text
            attachments = (live_record.prompt_input or {}).get("attachment_refs", [])
            if live_record.prompt_input is not None:
                from ..web.prompt import AttachmentPart, compose_prompt
                supplied = live_record.prompt_input
                actual_message = compose_prompt(supplied['text'], [AttachmentPart(**part) for part in supplied['parts']])
            if (session_obj.header.metadata.get('superagent_worker') is not None
                    and actual_message.strip().split(maxsplit=1)[:1] in
                    [['/agent'], ['/runner'], ['/skill'], ['/goal'], ['/plan']]):
                raise HarnessError(tr('superagent.worker-host-command'), code='superagent/worker-host-command')
            if live_record.goal_id:
                actual_message = tr('python.goal_runtime.48cf5c7b3b57')
                self.goals.start_round(session_obj.id, live_record.goal_id)
                await self.goals.publish(session_obj.id)
            if getattr(live_record, 'team_route', None) is not None and actual_message.strip().split(maxsplit=1)[:1] in [['/agent'], ['/runner'], ['/skill'], ['/goal'], ['/plan']]:
                raise HarnessError(tr('collaboration.invalid_recipients'), code='team/invalid-recipients')
            mention_text = live_record.prompt_input["text"] if live_record.prompt_input is not None else actual_message
            is_goal_message = actual_message.strip().split(maxsplit=1)[:1] == ['/goal']
            if (actual_message.strip().split(maxsplit=1)[:1] == ['/runner']
                    or self.config.enable_plan_mode or self.config.enable_goal
                    or self.config.enable_skills or self.config.enable_agents):
                handled = await self._apply_slash_command(session_obj, actual_message, run_context={
                    'user_text': mention_text, 'source':source, 'run_id':live_record.id,
                    'request_id':live_record.request_id,
                    'model':model, 'reasoning_effort':reasoning_effort,
                    'recovery_read_only':bool(live_record.recovery and live_record.recovery.get('mode') == 'inspect'),
                })
                if isinstance(handled, TurnResult):
                    return handled
                if handled is not None:
                    actual_message, notice = handled
                    if not actual_message.strip():
                        await session_obj.append("note", {"kind": "command", "text": notice})
                        await self.bus.emit("assistant/message", {"session_id": session_obj.id, "text": notice})
                        return TurnResult(session_obj.id, session_obj.current_turn, "completed", text=notice)
            actual_message = await self._apply_skill_mentions(session_obj, actual_message, mention_text=mention_text)
            message_goal = self.goals.get(session_obj.id) if is_goal_message else None
            message_goal_id = message_goal.id if message_goal else None
            team_route = getattr(live_record, 'team_route', None)
            if team_route is not None:
                from ..collaboration.routing import validate_team_route
                validate_team_route(self, session_obj, team_route)
            selected = self.agent_mount.resolve_active(session_obj.id) if self.agent_mount else None
            projection = self.agent_mount.active_projection(session_obj.id) if self.agent_mount else None
            if projection and projection.agent_type == "team" and selected is None:
                raise HarnessError(tr("agent.team.selection_unavailable", name=projection.agent), code="agent/team-selection-unavailable")
            team_selected = (selected is not None and selected[1].is_team) or (projection is not None and projection.agent_type == 'team')
            # 提示词驱动团队：主理人（会话主 agent）用通用原语现场编排，
            # 不路由到团队引擎；主理人决定派工，宿主继续核验权限与交付。
            if team_route is not None:
                from .team_commands import run_team_command
                return await run_team_command(self, session_obj, selected[1], actual_message, {
                    'source': source, 'run_id': live_record.id,
                    'request_id': live_record.request_id,
                    'model': model, 'reasoning_effort': reasoning_effort,
                    'recovery_read_only': bool(live_record.recovery and live_record.recovery.get('mode') == 'inspect'),
                    'team_worktrees': projection.team_worktrees, 'team_working_copy': projection.team_working_copy,
                    'team_session': True, 'team_name': projection.agent, 'user_text': mention_text,
                    'goal_id': message_goal_id, 'attachments': attachments,
                    'recipients': team_route['recipients'],
                })
            if selected and selected[1].metadata.get('runner'):
                from .external_agent_commands import run_external_agent_command
                from .team_commands import team_conversation_context
                agent = selected[1]
                persona = self.agent_mount.render_section(session_obj.id)
                personality = self.personalities.context({'session_id': session_obj.id})
                if personality:
                    persona += '\n\n' + personality
                external_task = tr('agent.execution.prompt', persona=persona,
                    history=team_conversation_context(session_obj), task=actual_message)
                return await run_external_agent_command(self, session_obj, agent.metadata['runner'], external_task, {
                    'user_text': mention_text, 'source': source, 'goal_id': message_goal_id, 'attachments': attachments,
                    'speaker': self._message_speaker_for_session(session_obj),
                    'run_id': live_record.id,
                    'request_id': live_record.request_id,
                    'recovery_read_only': bool(live_record.recovery and live_record.recovery.get('mode') == 'inspect'),
                })
            if not self.has_model_credentials:
                raise ConfigError(tr('python.harness.3abc241f3dfd'))
            await self.ensure_started()
            config = self._session_llm_config(session_obj)
            if model or reasoning_effort:
                config = config.merged(**{k: v for k, v in {'model': model, 'reasoning_effort': reasoning_effort}.items() if v is not None})
            agent = self._agent_for(session_obj)
            agent.task_read_only = bool(session_obj.header.metadata.get('project_task_review'))
            agent.recovery_read_only = bool(live_record.recovery and live_record.recovery.get('mode') == 'inspect')
            from .goal_budget import resolve_execution_budget
            agent.request_id = live_record.request_id
            agent.run_id = live_record.id
            agent.budget = resolve_execution_budget(self, session_obj)
            agent.reset()
            agent.config = config
            result = None
            try:
                if team_selected:
                    from .prompt_team_run import PromptTeamRun
                    run = PromptTeamRun(self, session_obj, agent, selected[1], projection)
                    self._prompt_team_runs[session_obj.id] = run
                    await run.start()
                frozen_text = None
                if team_selected:
                    from ..agents.prompt_team import render_team_lead_section
                    frozen_text = render_team_lead_section(run.team, run.registry)
                with self._freeze_skill_selection(session_obj), self.agent_mount.freeze_persona(session_obj.id, text=frozen_text) if self.agent_mount else contextlib.nullcontext():
                    result = await agent.prompt(session_obj, actual_message, config=config, source="goal" if live_record.goal_id else source, goal_id=message_goal_id, original_text=mention_text, **({"attachments": attachments} if attachments else {}))
                return result
            finally:
                run = self._prompt_team_runs.pop(session_obj.id, None)
                if run:
                    await join_cleanup(asyncio.create_task(run.close(result.reason if result else 'aborted')))
                if session_obj.open_turn is None:
                    # 最后一步批准、停止或限额结束时，也必须结算尚未进入下一步的切换。
                    await self.plan.settle(session_obj)
        async def operation() -> TurnResult:
            from ..core.language import current_language, resolve_language
            token = current_language.set(resolve_language(self.config.language).effective_language)
            try:
                from ..skills.context import load_scope
                with load_scope(session_obj.id), self.agent_mount.selection_scope() if self.agent_mount else contextlib.nullcontext():
                    return await localized_operation()
            finally:
                current_language.reset(token)
        return await self.runtime.execute(session_obj, operation, record=live_record)

    def run(
        self,
        session: Session | str,
        message: str,
        *,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> TurnResult:
        """同步处理一次输入（内部跑事件循环）。

        适合脚本与测试。注意：**不要**在已有事件循环里调用。
        """

        async def _run() -> TurnResult:
            return await self.run_async(session, message, **kwargs)

        owner = self.runtime.loop
        if owner is not None and owner.is_running():
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                pass
            else:
                raise RuntimeError(tr('python.harness.fe1669995a24'))
            future = asyncio.run_coroutine_threadsafe(_run(), owner)
            try:
                return future.result(timeout)
            except TimeoutError:
                future.cancel()
                raise
        return asyncio.run(asyncio.wait_for(_run(), timeout=timeout) if timeout else _run())

    async def stream(
        self,
        session: Session | str,
        message: str,
        *,
        model: str | None = None,
        reasoning_effort: str | None = None,
        source: str = "user",
    ) -> AsyncIterator[dict[str, Any]]:
        """流式处理一次输入，实时产出 UI 事件。

        事件类型（与 UI 消费方约定）：

        * ``user/message``     —— 用户输入回显
        * ``system/prompt``    —— 系统提示已装配
        * ``request/start``    —— 一次模型请求开始
        * ``assistant/delta``  —— 文本/思考增量（``type`` 区分 text/thinking）
        * ``tool/start``       —— 工具开始执行
        * ``tool/end``         —— 工具执行结束
        * ``request/end``      —— 一次模型请求结束
        * ``assistant/message``—— 完整的助手消息
        * ``assistant/partial``—— 停止/失败时已提交的部分回复（不进入模型上下文）
        * ``turn/start`` / ``turn/end`` —— 回合边界
        * ``compaction/start`` / ``compaction/end`` —— 上下文压缩
        * ``subagent/status`` / ``subagent/message`` —— 子代理生命周期

        提前关闭或取消迭代器会取消它启动的回合并等待清理完成。
        需要脱离消费者继续执行的任务应通过持久运行队列提交。
        """
        owner = self.runtime.claim_loop(asyncio.get_running_loop())
        if owner is not asyncio.get_running_loop():
            iterator = self.stream(session, message, model=model, reasoning_effort=reasoning_effort, source=source)
            # 取消跨事件循环的 anext() 后，所属循环可能仍在清理。
            # aclose() 必须与此串行执行，避免关闭正在运行的
            # 异步生成器并遗留其模型请求。
            advance_lock = asyncio.Lock()

            async def advance() -> dict[str, Any]:
                async with advance_lock:
                    return await anext(iterator)

            async def close_iterator() -> None:
                async with advance_lock:
                    await iterator.aclose()

            try:
                while True:
                    try:
                        event = await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(advance(), owner))
                    except StopAsyncIteration:
                        break
                    yield event
            finally:
                await join_cleanup(asyncio.wrap_future(
                    asyncio.run_coroutine_threadsafe(close_iterator(), owner)))
            return
        await self.ensure_started()
        session_obj = self.get_session(session) if isinstance(session, str) else session

        # 临时订阅总线，把事件转成字典流
        queue = _StreamEventBuffer()
        marker = object()
        events = (
            "turn/start", "turn/end", "user/message", "system/prompt",
            "request/start", "request/end", "assistant/delta", "assistant/message", "assistant/partial",
            "tool/start", "tool/output", "tool/end", "hook/state", "compaction/start", "compaction/end",
            "todo/write", "skill/loaded",
            "subagent/status", "subagent/message",
            # 交付物卡片：present 工具声明的文件。不转发的话前端永远拿不到
            # "这一轮改了哪些文件"，而这恰恰是回合结束时最该被看见的东西。
            "deliverables/presented",
        )

        # 不同事件的参数个数不一样：agent 循环发的是单个 payload，
        # 而 todo/write、skill/loaded 这类广播是 (session_id, payload)。
        # 这里用 *args 统一接住，再挑出真正的数据部分。
        async def forward(name: str, *args: Any) -> None:
            if _stream_owner.get() is not marker:
                return
            if name not in events:
                return
            if not args:
                return
            payload = args[-1]
            origin = payload.get("session_id") if isinstance(payload, dict) else None
            if origin is None and len(args) > 1 and isinstance(args[0], str):
                origin = args[0]
            if origin is not None and origin != session_obj.id:
                return
            operation = self.runtime.active.get(session_obj.id)
            # 取消计数也包含嵌套工具超时；只有任务终止
            # 才能绕过流控制，普通工具错误不能绕过。
            agent = self._agents.get(session_obj.id)
            terminating = isinstance(payload, dict) and (
                (name == 'assistant/partial' and payload.get('status') in {'stopped', 'failed'})
                or (name == 'turn/end' and payload.get('reason') in {'aborted', 'error'})
                or (name == 'tool/end' and isinstance(payload.get('display'), dict)
                    and payload['display'].get('interrupted')))
            if operation is not None and (
                    terminating or self.runtime.closed or (agent is not None and agent.cancelled)):
                queue.interrupt()
            await queue.put((name, payload if isinstance(payload, dict) else {"payload": payload}))

        # 注册转发器并保留引用：stream() 结束后必须注销，否则每调用一次
        # 就多挂一个监听器，同一条事件被转发 N 次，UI 上表现为消息重复
        # 且越用越严重。
        registered: list[tuple[str, Any]] = []
        for name in events:
            listener = (lambda n: (lambda *a: forward(n, *a)))(name)
            self.bus.on(name, listener)
            registered.append((name, listener))

        async def run_stream() -> TurnResult:
            token = _stream_owner.set(marker)
            try:
                return await self.run_async(session_obj, message, model=model,
                                            reasoning_effort=reasoning_effort, source=source)
            finally:
                if asyncio.current_task().cancelling():
                    queue.interrupt()
                _stream_owner.reset(token)

        task = asyncio.create_task(run_stream())

        async def drain() -> AsyncIterator[dict[str, Any]]:
            while True:
                item = await queue.get()
                if item is None:
                    break
                name, payload = item
                payload = dict(payload)
                # 事件名放 "type"；如果负载里已有 type 字段（例如 assistant/delta 的
                # 文本类型），改名成 delta_type，避免前端 switch 分不清。
                if "type" in payload:
                    payload["delta_type"] = payload.pop("type")
                payload["type"] = name
                payload["session_id"] = session_obj.id
                yield payload

        agen = drain()

        def unsubscribe() -> None:
            """摘掉本次注册的转发器，避免监听器随回合数无限累积。"""
            for name, listener in registered:
                try:
                    self.bus.off(name, listener)
                except Exception:
                    logger.debug(tr('log.e4ea89f0b269'), name, exc_info=True)

        # 并行：一边等 agent 跑完一边吐事件
        async def pump() -> None:
            try:
                await task
            except Exception as exc:
                # 兜底：agent 抛出的异常也要变成一条 error 事件，
                # 否则前端会一直转圈等不到收尾。
                await queue.put(("error", {"message": str(exc)}))
            finally:
                # turn/end 之后再补一条 done：UI 靠它解除"生成中"状态。
                await queue.put(("done", {}))
                # 最后放哨兵，通知消费者没有更多事件了。
                await queue.put(None)

        pump_task = asyncio.create_task(pump())
        try:
            async for item in agen:
                yield item
        finally:
            unsubscribe()
            # 等待清理前，先唤醒排队的输出回调和终端泵写入操作。
            # aclose() 之后，满缓冲区已没有消费者。
            queue.close()
            # 只管理此流自己的 run_async 任务；按会话标识取消，
            # 可能在本任务等待锁时停止其他调用方的活动回合。
            # 保护等待收尾，避免重复取消中断工具或传输清理，
            # 或过早释放该锁。
            if not task.done():
                task.cancel()
            try:
                await join_cleanup(asyncio.gather(pump_task, return_exceptions=True))
            finally:
                await agen.aclose()

    # ==================================================================
    # 状态
    # ==================================================================
    def status(self, session_id: str | None = None) -> dict[str, Any]:
        """返回 agent 状态与配置摘要。"""
        agent = self._agents.get(session_id) if session_id else self.agent
        return {
            "agent": agent.status.to_dict() if agent else None,
            "config": self.config.to_dict(),
            "has_model_credentials": self.has_model_credentials,
            "tools": self.tools.names(),
            "skills": [s.name for s in self.skills.list()] if self.skills else [],
        }

    async def cancel(self, session_id: str, *, expected_turn: int | None = None) -> bool:
        """取消指定会话正在进行的回合。

        顺带中断该会话下所有挂起的 ``ask_user_question``：否则模型正等答案时
        取消回合，挂起的 Future 会一直等到超时才释放，白占一个工具调用槽位。
        """
        owner = self.runtime.loop
        if owner is not None and owner.is_running() and owner is not asyncio.get_running_loop():
            return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(self.cancel(session_id, expected_turn=expected_turn), owner))
        session = self.get_session(session_id)
        if expected_turn is not None:
            if type(expected_turn) is not int or expected_turn < 1:
                raise HarnessError(tr('python.harness.ee4027dde607'), code='run/invalid')
            if session.open_turn != expected_turn:
                raise HarnessError(tr('python.harness.1fc2c30df2ec'), code='run/stale-turn')
        pause_error = None
        try:
            self.goals.disarm(session_id, "user-stopped")
            await self.goals.publish(session_id)
            self.goal_runtime.wake(session_id)
        except (OSError, PersistenceError):
            pause_error = PersistenceError(tr('python.harness.f51afc6b0fb4'))
        task = self.runtime.active.get(session_id)
        if self.runtime.queue(session_id):
            try:
                await self.runtime.set_queue_paused(session_id, True, reason='stopped')
            except PersistenceError as exc:
                # 控制状态写入失败会通过 storage_error 阻止队列执行，
                # 但仍须允许停止活动任务。
                pause_error = exc
        if task is None or task.done() or self.runtime.active.get(session_id) is not task:
            if pause_error is not None:
                raise pause_error
            return False
        self.questions.cancel_session(session_id)
        agent = self._agents.get(session_id)
        if agent is not None:
            if agent.status.status == "stopping":
                if pause_error is not None:
                    raise pause_error
                return True
            agent.cancel("user")
            agent.status.status = "stopping"
        await self.bus.emit("stopping", {"session_id": session_id})
        task.cancel()
        if pause_error is not None:
            raise pause_error
        return True

    def steering_receipt(self, session_id: str, request_id: str, *, summary: bool = False) -> dict[str, Any] | None:
        if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 200:
            raise HarnessError(tr('python.harness.94e6e0dac54c'), code='run/invalid')
        session = self.get_session(session_id)
        return session.steering.receipt(request_id, active_turn=session.open_turn if session_id in self.runtime.active else None, summary=summary)

    async def steer(self, session_id: str, text: str, *, expected_turn: int, request_id: str | None = None, attachments: list[dict[str, Any]] | None = None, original_text: str | None = None, before_accept: Callable[[], None] | None = None) -> dict[str, Any]:
        """提交一次引导，模型正文与用户原文分开保存。"""
        owner = self.runtime.loop
        if owner is not None and owner.is_running() and owner is not asyncio.get_running_loop():
            return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(self.steer(session_id, text, expected_turn=expected_turn, request_id=request_id, attachments=attachments, original_text=original_text, before_accept=before_accept), owner))
        if before_accept is not None and (not callable(before_accept) or inspect.iscoroutinefunction(before_accept)):
            raise HarnessError(tr('runtime.steering.invalid_guard'), code='run/invalid')
        if original_text is not None and not isinstance(original_text, str):
            raise HarnessError(tr('web.input.invalid_text'), code='run/invalid')
        agent = self._agents.get(session_id)
        session = self.get_session(session_id)
        if type(expected_turn) is not int or expected_turn < 1:
            raise HarnessError(tr('python.harness.ee4027dde607'), code='run/invalid')
        if not isinstance(text, str) or not text.strip():
            raise HarnessError(tr('python.harness.16283ec05a6d'), code="run/empty")
        if len(text) > 262144:
            raise HarnessError(tr('python.harness.accd0431511b'), code='run/steering-capacity')
        if request_id is None:
            request_id = uuid.uuid4().hex
        if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 200:
            raise HarnessError(tr('python.harness.94e6e0dac54c'), code='run/invalid')
        agent = agent or self._agent_for(session)
        async with agent.steering_lock:
            existing = session.steering.records.get(request_id)
            if existing is not None:
                if (existing['text'] != text or existing.get('original_text') != original_text or existing['turn'] != expected_turn
                        or [dict(item) for item in existing.get('attachments', ())] != (attachments or [])):
                    raise HarnessError(tr('python.harness.bdc16d1fef1c'), code='run/idempotency-conflict')
            else:
                if session.open_turn != expected_turn or session_id not in self.runtime.active or not agent.accepting_steering or agent.cancelled:
                    raise HarnessError(tr('python.harness.d8a11828ed37'), code="run/stale-turn")
                controller = agent.team_steering
                if controller is not None:
                    if not controller.supported:
                        raise HarnessError(tr('agent.team.steering_unsupported'), code='run/steering-unsupported')
                    if not controller.can_accept():
                        raise HarnessError(tr('python.harness.d8a11828ed37'), code='run/stale-turn')
                pending = session.steering.pending(expected_turn)
                if len(pending) >= 32 or sum(len(item['text']) for item in pending) + len(text) > 1048576:
                    raise HarnessError(tr('python.harness.260af227ba70'), code='run/steering-capacity')
                # 可信宿主在锁等待后重新核验归属与授权；既有回执只回查，不重新准入。
                if before_accept is not None:
                    checked = before_accept()
                    if checked is not None:
                        if inspect.iscoroutine(checked) or inspect.isgenerator(checked):
                            checked.close()
                        raise HarnessError(tr('runtime.steering.invalid_guard'), code='run/invalid')
                await session.append('note', {'kind': 'steering/accepted', 'request_id': request_id,
                    'turn': expected_turn, 'text': text, **({'original_text': original_text} if original_text is not None else {}), **({'attachments': attachments} if attachments else {}), 'run_id': getattr(agent, 'run_id', None)}, ignorable=True)
            receipt = self.steering_receipt(session_id, request_id, summary=False)
            await self.bus.emit('steer/state', {'session_id': session_id, 'receipt': receipt})
            return {"session_id": session_id, "turn": expected_turn, "accepted": True, 'request_id': request_id, 'receipt': receipt}

    async def close(self) -> None:
        """关闭 harness：结束所有活动会话并把排队中的落盘跑完。

        调用方不必逐个 close 会话：这里会遍历 store 里的全部会话，
        补完未结束的回合（interrupted），并等待事件落盘，
        避免遗留 "coroutine was never awaited" 与半截日志。
        """
        owner = self.runtime.loop
        if owner is not None and owner.is_running() and owner is not asyncio.get_running_loop():
            await join_cleanup(asyncio.wrap_future(asyncio.run_coroutine_threadsafe(self.close(), owner)))
            return
        if self._close_task is None:
            # 任何资源等待前先停止生产并取消所属运行，避免迟到续跑进入启动拒绝分支。
            self.runtime.begin_close()
            self._close_task = asyncio.create_task(self._close(), name='harness-close')
        await join_cleanup(self._close_task)

    @property
    def superagent_workbench(self):
        """工作台惰性绑定当前主宿主，原生运行始终复用统一服务。"""
        if self._close_task is not None:
            from .superagent_common import _error
            raise _error('closed')
        if self._superagent_workbench is None:
            from .superagent_workbench import SuperagentWorkbench
            self._superagent_workbench = SuperagentWorkbench()
        return self._superagent_workbench

    async def _close(self) -> None:
        """按原顺序回收全部资源，再向共享等待者报告原始失败。"""
        errors = []

        async def attempt(operation, *args):
            try:
                await operation(*args)
            except (Exception, asyncio.CancelledError) as exc:
                errors.append(exc)

        if self._start_task is not None and not self._start_task.done():
            self._start_task.cancel()
            await join_cleanup(asyncio.gather(self._start_task, return_exceptions=True))
        if self._superagent_workbench is not None:
            await attempt(self._superagent_workbench.close)
        for service in tuple(self._superagent_workers):
            await attempt(service.aclose)
        await attempt(asyncio.to_thread, self.chatgpt.cancel_login)
        if hasattr(self, 'provider_authorizations'):
            await attempt(self.provider_authorizations.close)
        await attempt(self.codex_gateway.close)
        await attempt(self.terminal_setup.close)
        await attempt(run_hex_owned, self.hex_service.stop)
        await attempt(self.goal_runtime.close)
        await attempt(self.runtime.close)
        await attempt(self.session_titles.close)
        if self._owned_memory_candidates is not None:
            await attempt(self._owned_memory_candidates.aclose)
        await attempt(self.project_execution.close)
        if self.automations is not None:
            await attempt(self.automations.close)
        await attempt(self.browser.close)
        await attempt(self.subagents.shutdown)
        for session in (*self.sessions.loaded_sessions(), *self.subagents.contexts.loaded_sessions()):
            try:
                self.questions.cancel_session(session.id)
            except (Exception, asyncio.CancelledError) as exc:
                errors.append(exc)
            if session.id in self.hooks.started_sessions:
                await attempt(self.hooks.lifecycle, self._agents.get(session.id), session,
                              'SessionEnd', session.current_turn, 0, {'reason': 'shutdown'})
            await attempt(session.close)
        await attempt(self.jobs.cancel_all)
        await attempt(self.terminals.close_all)
        await attempt(self.mcp.aclose)
        if self.connectors is not None:
            await attempt(self.connectors.aclose)
        self._agents.clear()
        raise_cleanup_errors(errors)

    def __repr__(self) -> str:
        return (
            f"<Harness model={self.config.model} tools={len(self.tools.names())} "
            f"skills={len(self.skills) if self.skills else 0}>"
        )




__all__ = ["Harness"]
