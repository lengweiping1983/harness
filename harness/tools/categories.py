"""工具分类：给 UI 分组用的执行侧元数据。

**为什么分类不进 :class:`~harness.llm.types.ToolSchema`**：ToolSchema 的三个字段
（name / description / parameters）会被原样发进模型请求。分类只服务于人读的
工具目录与时间线，塞进去就是白白烧 token，还会让"我们给模型看了什么"变得不再
可断言。所以它是 :class:`~harness.tools.registry.ToolDefinition` 的属性，
由 :mod:`harness.web.views` 在投影目录时单独取。

表本身按"人怎么找工具"分组，而不是按模块分文件：一个用户想干的事通常是
"改文件 / 跑命令 / 查网页 / 管子代理"，而不是"fs_tools 模块里的工具"。
"""

from __future__ import annotations

from harness.i18n import tr

from typing import NamedTuple


class Category(NamedTuple):
    """一个工具分类：稳定标识与按当前语言解析的名称。"""

    id: str
    label_key: str

    @property
    def label(self) -> str:
        """分类名称按读取时语言解析。"""
        return tr(self.label_key)



#: 分类表。顺序即 UI 里的展示顺序 —— 越靠前越常用。
CATEGORIES: tuple[Category, ...] = (
    Category("file", 'python.categories.39932f24fe11'),
    Category("shell", 'python.categories.32c8d373afe0'),
    Category("terminal", 'python.categories.e2a76ef1f12e'),
    Category("web", 'python.categories.97b31b5d63f5'),
    Category("session", 'python.categories.a63280253f17'),
    Category("agent", 'python.categories.acd37b4c796c'),
    Category("job", 'python.categories.4c46c904395c'),
    Category("plan", 'python.categories.0f6b1949b093'),
    Category("goal", 'python.categories.57060c88a36b'),
    Category("todo", 'python.categories.52fdbbaeda4c'),
    Category("skill", 'python.categories.99aea2f9131a'),
    Category("mcp", "catalog.category.mcp"),
    Category("interaction", 'python.categories.99f1b08f6464'),
    Category("system", 'python.categories.5b50d7c4b595'),
    Category("other", 'python.categories.d2909f1647e7'),
)

_BY_ID: dict[str, Category] = {category.id: category for category in CATEGORIES}

#: 内置工具的分类归属。名字是注册时的 ``schema.name``，注册表为准。
NAME_CATEGORY: dict[str, str] = {
    # 文件
    "read": "file",
    "write": "file",
    "edit": "file",
    "glob": "file",
    "grep": "file",
    "read_image": "file",
    "read_images": "file",
    "lsp": "file",
    # 执行
    "bash": "shell",
    "pwsh": "shell",
    "run_code": "shell",
    # 终端
    "terminal_open": "terminal",
    "terminal_close": "terminal",
    "terminal_list": "terminal",
    "terminal_read": "terminal",
    "terminal_send": "terminal",
    "terminal_signal": "terminal",
    # 网络
    "web_search": "web",
    "web_fetch": "web",
    # 会话
    "session_search": "session",
    "session_trace": "session",
    "session_event_read": "session",
    "session_event_search": "session",
    "session_event_trace": "session",
    # 子代理
    "spawn_agent": "agent",
    "followup_task": "agent",
    "send_message": "agent",
    "list_agents": "agent",
    "wait_agent": "agent",
    "wait_agents": "agent",
    "interrupt_agent": "agent",
    "list_subagent_models": "agent",
    # 后台任务
    "job_list": "job",
    "job_output": "job",
    "job_kill": "job",
    # 计划
    "exit_plan_mode": "plan",
    "present": "plan",
    # 目标
    "create_goal": "goal",
    "get_goal": "goal",
    "update_goal": "goal",
    # 待办
    "todo_write": "todo",
    "todo_read": "todo",
    "todo_update": "todo",
    "todo_bind": "todo",
    # 技能
    "skill": "skill",
    "ralph": "skill",
    "workflow": "skill",
    # 模型上下文协议 MCP
    "list_mcp_resources": "mcp",
    "list_mcp_resource_templates": "mcp",
    "read_mcp_resource": "mcp",
    # 交互
    "ask_user_question": "interaction",
    # 系统
    "load_workspace_dependencies": "system",
}

#: 名字没登记时的兜底：按前缀/子串猜一层，比全丢进"其他"有用。
_HINTS: tuple[tuple[str, str], ...] = (
    ("terminal", "terminal"),
    ("job", "job"),
    ("session", "session"),
    ("agent", "agent"),
    ("mcp", "mcp"),
    ("skill", "skill"),
    ("goal", "goal"),
    ("todo", "plan"),
    ("web", "web"),
    ("file", "file"),
)


def infer_category(name: str) -> str:
    """返回工具的分类 id。

    先查内置表，再按前缀/子串兜底，最后落 ``other``。永不抛异常：分类错了只是
    目录分组难看一点，不该因为一个第三方工具没登记就让整个工具面板打不开。
    """
    direct = NAME_CATEGORY.get(name)
    if direct is not None:
        return direct
    lowered = name.lower()
    for token, category_id in _HINTS:
        if token in lowered:
            return category_id
    return "other"


def resolve(category_id: str | None, name: str) -> Category:
    """把分类 id 解析成 :class:`Category`；未知 id 落 ``other``。"""
    if category_id:
        found = _BY_ID.get(category_id)
        if found is not None:
            return found
    return _BY_ID[infer_category(name)]


__all__ = ["CATEGORIES", "Category", "NAME_CATEGORY", "infer_category", "resolve"]
