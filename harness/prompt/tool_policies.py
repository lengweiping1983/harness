"""按当前语言和真实工具能力装配工具策略，不保存已翻译的模块常量。"""
from __future__ import annotations

from ..i18n import tr


def browser_testing_policy(names):
    """仅声明当前宿主真实提供的浏览器能力，保留通用测试约束。"""
    parts = ['policy.browser_automation']
    if 'browser' in names:
        parts.extend(('policy.browser_native', 'policy.browser_verification'))
    elif {'bash', 'pwsh'} & names:
        parts.append('policy.browser_verification')
    else:
        return ''
    return ' '.join(tr(part) for part in parts)


def goal_text() -> str:
    """读取当前语言的目标记录策略。"""
    return tr('policy.goal')


def build_tool_policies(names: set) -> list:
    """按工具可用性产出策略提供函数；完整句式由资源维护。"""
    out = []
    for tool, order, key in (
        ('read', 1100, 'policy.read'),
        ('write', 1200, 'policy.write_edit' if 'edit' in names else 'policy.write'),
        ('edit', 1300, 'policy.edit'),
        ('glob', 1400, 'policy.glob'),
        ('grep', 1500, 'policy.grep_read' if 'read' in names else 'policy.grep'),
        ('lsp', 2200, 'policy.lsp'),
        ('load_workspace_dependencies', 1050, 'policy.workspace_dependencies'),
        ('show_widget', 1800, 'policy.visualization'),
    ):
        if tool in names:
            out.append((f'tool:{tool}', order, lambda key=key: tr(key)))
    for tools, section, order, key in (
        ({'job_output', 'job_kill'}, 'jobs', 1600, 'policy.jobs'),
        ({'terminal_list'}, 'terminal', 1650, 'policy.terminal'),
        ({'create_goal', 'update_goal'}, 'goal', 2400, 'policy.goal'),
        ({'schedule_automation', 'list_automations', 'update_automation', 'delete_automation'}, 'automation', 2380, 'policy.automation'),
        ({'todo_write', 'todo_update'}, 'todo', 2450, 'policy.todo'),
    ):
        if tools & names:
            out.append((f'tool:{section}', order, lambda key=key: tr(key)))
    if {'browser', 'bash', 'pwsh'} & names:
        browser_names = frozenset(names)
        out.append(('tool:browser-testing', 1700, lambda: browser_testing_policy(browser_names)))
    return sorted(out, key=lambda item: (item[1], item[0]))


__all__ = ['browser_testing_policy', 'build_tool_policies', 'goal_text']
