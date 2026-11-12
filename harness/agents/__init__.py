"""Agent体系：可召唤的领域角色（人设 + 方法论 + 工具链）。

Agent 使用目录与声明文件组织：

一个Agent = 一个目录 + 一个 ``AGENTS.md``：

.. code-block:: markdown

    ---
    name: code-reviewer
    description: 资深代码审查Agent，按 P0/P1/P2 分级产出可执行修复建议
    agent_type: agent          # agent（单Agent）| team（Agent 团队）
    model: deepseek/deepseek-r1 # 可选：该Agent使用的模型覆盖
    ---

    # 代码审查Agent

    你是一名有 10 年经验的……

Agent 团队（team）在 frontmatter 里声明成员，每个成员要么内联一段专属指令，
要么引用另一个已定义的单Agent：

.. code-block:: yaml

    ---
    name: content-team
    description: 内容创作Agent 团队（策略 + 撰写 + 审核）
    agent_type: team
    members:
      - name: strategist
        role: lead
        prompt: 负责拆解任务、分配给成员并汇总交付
      - name: writer
        role: member
        agent: writing-agent     # 引用已定义的单Agent
    ---

    # 内容创作Agent 团队

    团队协作协议正文……

团队统一使用主会话原语调度。成员定义与协议进入主会话，协调者通过
``spawn_agent``／``send_message`` 等通用工具执行；宿主约束身份、依赖、权限与验收。
明确指定收件人时由宿主直接派发，不额外请求主理人规划和改写。

声明约束：

* **禁止声明 tools 字段**——工具权限由系统统一分配，Agent只描述"是谁、
  擅长什么、怎么工作"；
* 名字必须是 kebab-case（与技能同规则）；
* ``agent_type: team`` 必须带 ``members``，否则解析失败。
"""

from harness.agents.parser import (
    AgentMember,
    AgentDefinition,
    is_valid_agent_name,
    parse_agent_file,
)
from harness.agents.registry import (
    FilesystemAgentProvider,
    AgentProvider,
    AgentRegistry,
    PROJECT_AGENT_RANK,
    USER_AGENT_RANK,
    build_default_agent_registry,
)
from harness.agents.mount import AgentMountController, AgentProjection
from harness.agents.prompt_team import (
    lead_model,
    render_activation_notice,
    render_team_lead_section,
)
from harness.agents.tool import build_agent_tool, render_catalog

__all__ = [
    "AgentMember",
    "AgentDefinition",
    "is_valid_agent_name",
    "parse_agent_file",
    "AgentProvider",
    "FilesystemAgentProvider",
    "AgentRegistry",
    "build_default_agent_registry",
    "AgentMountController",
    "AgentProjection",
    "build_agent_tool",
    "render_catalog",
    "PROJECT_AGENT_RANK",
    "USER_AGENT_RANK",
    "lead_model",
    "render_team_lead_section",
    "render_activation_notice",
]
