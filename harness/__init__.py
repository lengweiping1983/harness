"""Harness —— 可嵌入业务系统的 Agent 运行框架。

这个包把一个可运行的 coding/任务 agent 拆成若干可替换的层：

.. code-block:: text

    harness/
      core/     事件总线、异常、JSON 工具、Agent 循环   ← 驱动核心
      llm/      消息类型、wire 转换、SSE 传输、模型适配器 ← 大脑
      session/  事件日志、有序表面、消息派生、持久化     ← 记忆
      tools/    工具注册表、执行流水线、内置工具         ← 手脚
      skills/   SKILL.md 解析、技能目录、加载工具         ← 可插拔知识
      prompt/   系统提示词注册表                        ← 人格
      app/      配置与装配                              ← 组装
      web/      Web API + 实时事件流                     ← 界面

快速开始::

    from harness import Harness, HarnessConfig

    harness = Harness(HarnessConfig.from_env(cwd="/srv/app"))
    session = harness.create_session()
    result = harness.run(session, "你好")
    print(result.text)
"""

from .app import Harness, HarnessConfig
from .app.runtime import IdentityContext, RunController
from .core.agent import AgentLoop
from .core.turn import AgentStatus, TurnResult
from .core.errors import HarnessError
from .core.events import Context, EventBus
from .llm import (
    AssistantMessage,
    LlmCallConfig,
    OpenRouterAdapter,
    TextBlock,
    ThinkingBlock,
    TokenUsage,
    ToolResultBlock,
    ToolResultMessage,
    ToolSchema,
    ToolUseBlock,
    UserMessage,
)
from .session import Session, SessionStore, create_session
from .skills import SkillMetadata, SkillRegistry
from .tools import ToolDefinition, ToolRegistry, ToolRunContext

__version__ = "1.0.0"

__all__ = [
    "__version__",
    # 应用
    "Harness",
    "HarnessConfig",
    "IdentityContext",
    "RunController",
    # 核心
    "AgentLoop",
    "AgentStatus",
    "TurnResult",
    "HarnessError",
    "EventBus",
    "Context",
    # 大语言模型
    "OpenRouterAdapter",
    "LlmCallConfig",
    "AssistantMessage",
    "UserMessage",
    "TextBlock",
    "ThinkingBlock",
    "ToolUseBlock",
    "ToolResultBlock",
    "ToolResultMessage",
    "ToolSchema",
    "TokenUsage",
    # 会话
    "Session",
    "SessionStore",
    "create_session",
    # 工具
    "ToolRegistry",
    "ToolDefinition",
    "ToolRunContext",
    # 技能
    "SkillRegistry",
    "SkillMetadata",
]
