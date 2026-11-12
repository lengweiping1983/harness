"""中文产品资源保持统一术语，机器协议和模板参数不参与改名。"""
import json
import re
import unittest
from collections import defaultdict
from pathlib import Path
from string import Formatter

ROOT = Path(__file__).resolve().parents[1]

# 按资源身份固定已核对的语境，不能让同组任意键交换单复数或动作/状态。
# 文件：单个附件、搜索项、预览、LSP类型和菜单用File，文件集合入口用Files。
# 技能、Agent、团队：单个对象/类型用单数，目录、分类及集合选择器用复数。
# 图片、工具结果：单项类型用单数，上下文分类集合用复数。
# 问题：Conversation问题列表用复数，QuestionCard单卡片用单数。
# 通知：CommandPalette单条通知区域用单数，桌面权限类别用复数。
# 拒绝：审批和桌面权限按钮都是动作，统一使用Deny。
# 完成：一般结束标签用Done，命令执行状态用Completed。
# 数量模板：one用于一项，many/lineCount用于多项，不按中文值强行合并。
CONTEXTUAL_COPIES = {
    '拒绝': {
        'python.permissions.136de7a8c46f': 'Deny',
        'ui.components.chat.blocks.QuestionCard.136de7a8c46f': 'Deny',
        'browser.desktop.permissions.deny': 'Deny',
    },
    '完成': {
        'python.cli.c0b3fbff51cc': 'Done',
        'ui.components.chat.blocks.ToolBlock.c0b3fbff51cc': 'Done',
        'ui.lib.browser.c0b3fbff51cc': 'Done',
        'ui.lib.runHistory.c0b3fbff51cc': 'Done',
        'common.completed': 'Completed',
    },
    '技能': {
        'python.installer.99aea2f9131a': 'Skill',
        'python.categories.99aea2f9131a': 'Skill',
        'ui.components.chat.TriggerMenu.99aea2f9131a': 'Skill',
        'ui.components.overlays.EscPanel.99aea2f9131a': 'Skills',
        'common.skill': 'Skill',
        'ui.extensions.skills': 'Skills',
    },
    '智能体': {
        'python.installer.c3a73c5eecea': 'Agent',
        'ui.components.overlays.EscPanel.c3a73c5eecea': 'Agents',
        'ui.components.overlays.EscPanel.b3924b576cfb': 'Agent',
        'common.agent': 'Agent',
        'ui.agent.author.agent': 'Agent',
        'ui.composerAgents.agent': 'Agents',
        'ui.agent.author.agentsLabel': 'Agents',
        'browser.telemetry.agent': 'Agent',
    },
    '文件': {
        'ui.commands.files': 'Files',
        'python.lsp_tools.39932f24fe11': 'File',
        'python.categories.39932f24fe11': 'File',
        'python.prompt.39932f24fe11': 'File',
        'ui.components.inspector.Inspector.39932f24fe11': 'File',
        'ui.components.overlays.CommandPalette.39932f24fe11': 'File',
        'ui.desktop.menu.file': 'File',
        'ui.desktop.menu.files': 'Files',
    },
    '图片': {
        'python.prompt.d24c10d37db0': 'Image',
    },
    '待处理问题': {
        'ui.components.chat.Conversation.56897d5961b4': 'Open questions',
        'ui.shared.pendingQuestion': 'Open question',
    },
    '团队': {
        'ui.components.chat.blocks.MessageBlocks.5d682392c70f': 'Team',
        'ui.agent.author.team': 'Team',
        'ui.composerAgents.team': 'Teams',
        'ui.agent.author.teamsLabel': 'Teams',
    },
    '通知': {
        'ui.components.overlays.CommandPalette.4b6ded8a7f18': 'Notification',
        'browser.desktop.permissions.notifications': 'Notifications',
    },
    '工具结果': {
        'ui.lib.requestTraces.d7fea1743a9d': 'Tool result',
    },
    '{count} 个智能体': {
        'workflow.agent_one': '{count} Agent',
        'workflow.agent_many': '{count} Agents',
    },
    '{count} 行': {
        'ui.filePreview.oneLine': '{count} line',
        'ui.filePreview.lineCount': '{count} lines',
    },
    '{count} 行（预览）': {
        'ui.filePreview.oneLinePreview': '{count} line (preview)',
        'ui.filePreview.lineCountPreview': '{count} lines (preview)',
    },
    '待执行任务队列（{count} 项）': {
        'ui.queue.pendingOne': 'Pending task queue ({count} item)',
        'ui.queue.pendingCount': 'Pending task queue ({count} items)',
    },
    # 临时授权组件按实际条目数选择 one/count；中文同文，英文分别保留单复数。
    '临时操作授权（{count} 项）': {
        'ui.permissions.grantCountOne': 'Temporary operation authorization ({count} grant)',
        'ui.permissions.grantCount': 'Temporary operation authorization ({count} grants)',
    },
    # 工具目录与计划标题分别按实际数量选择完整模板，不能统一成英文复数。
    '{count} 项工具': {
        'ui.settings.tools.total': '{count} tools',
        'ui.settings.tools.totalOne': '{count} tool',
    },
    '计划（{count} 个版本）': {
        'ui.planHistory.titleOne': 'Plan ({count} version)',
        'ui.planHistory.titleCount': 'Plan ({count} versions)',
    },
    # 终端／工具摘要是行内续接，Academy 比较字段是独立标签。
    '退出码：{code}': {
        'terminal.feedback.exit_code': 'exit code: {code}',
        'ui.toolBlock.exitCode': 'exit code: {code}',
        'ui.academy.compare.exitCode': 'Exit code: {code}',
    },
}


class ChineseTerminologyTests(unittest.TestCase):
    def test_catalogs_use_canonical_terms(self):
        for relative in ('harness/i18n/locales', 'web/src/i18n/locales'):
            catalog = json.loads((ROOT / relative / 'zh-CN.json').read_text(encoding='utf-8'))
            for key, value in catalog.items():
                # 只检查产品自然语言；代码、品牌、路径和协议字面量保留原样。
                visible = re.sub(r'```[\s\S]*?```|`[^`\n]+`|Claude Agent SDK|OpenAI Agents SDK|Agent Skills', '', value)
                for term in ('子代理', '后代代理', '父代理', '子 Agent', '父 Agent', '主理人'):
                    with self.subTest(catalog=relative, key=key, term=term):
                        self.assertNotIn(term, visible)
                with self.subTest(catalog=relative, key=key):
                    self.assertIsNone(re.search(r'(?<![A-Za-z_])Agents?(?![A-Za-z_])', visible))

    def test_model_endpoints_use_interface_terminology(self):
        """模型配置及本机模型资料统一使用接口术语，源与浏览器资源一致。"""
        for relative in ('harness/i18n/locales', 'web/src/i18n/locales'):
            catalog = json.loads((ROOT / relative / 'zh-CN.json').read_text(encoding='utf-8'))
            for key, value in catalog.items():
                if key.startswith(('ui.providers.', 'models.config.', 'llm.opencode_local.')):
                    with self.subTest(catalog=relative, key=key):
                        self.assertNotIn('接入', value)
            for key, expected in {
                'ui.providers.protocol': '接口协议',
                'ui.providers.removeConnection': '删除接口',
                'ui.providers.connectionEnabled': '启用接口',
                'ui.providers.summary': '{state} · {models} 个模型',
            }.items():
                with self.subTest(catalog=relative, key=key):
                    self.assertEqual(catalog[key], expected)

    def test_hook_source_names_use_chinese(self):
        """来源名称使用中文，保留单项 Hook 与集合 Hooks 的其他语境。"""
        expected = {
            'ui.components.overlays.ProjectHooksPage.926b04e8ced4': '项目钩子',
            'ui.extensions.pluginHooks': '插件钩子',
            'ui.extensions.userHooks': '用户钩子 · 所有项目',
        }
        for relative in ('harness/i18n/locales', 'web/src/i18n/locales'):
            catalog = json.loads((ROOT / relative / 'zh-CN.json').read_text(encoding='utf-8'))
            for key, text in expected.items():
                with self.subTest(catalog=relative, key=key):
                    self.assertEqual(catalog[key], text)
            for key, value in catalog.items():
                with self.subTest(catalog=relative, key=key):
                    self.assertNotRegex(value, r'(项目|插件|用户) Hooks?\b')

    def test_catalogs_use_curly_double_quotes(self):
        for relative in ('harness/i18n/locales', 'web/src/i18n/locales'):
            catalog = json.loads((ROOT / relative / 'zh-CN.json').read_text(encoding='utf-8'))
            for key, value in catalog.items():
                with self.subTest(catalog=relative, key=key):
                    self.assertNotIn('「', value)
                    self.assertNotIn('」', value)

    def test_representative_labels_and_template_parameters(self):
        catalog = json.loads((ROOT / 'harness/i18n/locales/zh-CN.json').read_text(encoding='utf-8'))
        expected = {
            'python.audit.hook_state_invalid': ('钩子', ['path']),
            'python.harness.219ed8617ef3': ('智能体与能力', ['p0']),
            'query.session': ('对话', ['id', 'title']),
            'python.file_restore.f1e8ccfb97ee': ('轮次', []),
        }
        for key, (term, parameters) in expected.items():
            with self.subTest(key=key):
                self.assertIn(term, catalog[key])
                self.assertEqual([field for _, field, _, _ in Formatter().parse(catalog[key])
                                  if field is not None], parameters)


class EnglishTerminologyTests(unittest.TestCase):
    def test_execution_history_and_log_have_distinct_names(self):
        """跨次任务历史与请求工具日志使用不同名称，避免导航混淆。"""
        expected = {
            'ui.projectBoard.tasks': ('任务队列', 'Task queue'),
            'ui.components.inspector.TaskQueue.9d18bfe48c0b': ('待执行任务队列', 'Pending task queue'),
            'ui.components.inspector.TaskQueue.03a3925c64d1': ('任务历史', 'Task history'),
            'ui.components.inspector.TaskQueue.02451fd58cd3': ('任务历史', 'Task history'),
            'ui.trace.title': ('请求与工具日志', 'Request and tool log'),
            'ui.components.chat.blocks.MessageBlocks.7715a51ffbac': ('查看执行任务', 'View execution task'),
            'ui.delivery.execution': ('查看执行任务', 'View execution task'),
            'ui.components.inspector.RunPreview.874624ff7b10': ('关联执行任务', 'Associated execution task'),
            'ui.components.inspector.RunPreview.ef79587fb63d': ('第 {p0} 轮次执行任务', 'Turn {p0} execution task'),
            'ui.components.inspector.RunPreview.e6a2caa875a5': ('关闭', 'Close'),
        }
        for relative in ('harness/i18n/locales', 'web/src/i18n/locales'):
            catalogs = {lang: json.loads((ROOT / relative / f'{lang}.json').read_text(encoding='utf-8'))
                        for lang in ('en', 'zh-CN')}
            for key, (chinese, english) in expected.items():
                with self.subTest(catalog=relative, key=key):
                    self.assertEqual(catalogs['zh-CN'][key], chinese)
                    self.assertEqual(catalogs['en'][key], english)

    def test_contextual_copy_is_bound_to_resource_identity(self):
        for relative in ('harness/i18n/locales', 'web/src/i18n/locales'):
            catalogs = {lang: json.loads((ROOT / relative / f'{lang}.json').read_text(encoding='utf-8'))
                        for lang in ('en', 'zh-CN')}
            for chinese, entries in CONTEXTUAL_COPIES.items():
                for key, english in entries.items():
                    if relative.startswith('web/') and not key.startswith(('ui.', 'settings.language.', 'files.open.', 'browser.')):
                        continue
                    with self.subTest(catalog=relative, key=key):
                        self.assertEqual(catalogs['zh-CN'][key], chinese)
                        self.assertEqual(catalogs['en'][key], english)

    def test_collection_labels_use_plural_terms(self):
        for relative in ('harness/i18n/locales', 'web/src/i18n/locales'):
            catalog = json.loads((ROOT / relative / 'en.json').read_text(encoding='utf-8'))
            for key, expected in {
                'ui.components.overlays.EscPanel.c3a73c5eecea': 'Agents',
                'ui.components.overlays.EscPanel.99aea2f9131a': 'Skills',
                'ui.components.inspector.RequestTracePanel.2433800bfcbe': 'Tool definitions',
            }.items():
                with self.subTest(catalog=relative, key=key):
                    self.assertEqual(catalog[key], expected)

    def test_equal_chinese_copy_has_consistent_english_except_contextual_variants(self):
        for relative in ('harness/i18n/locales', 'web/src/i18n/locales'):
            catalogs = {lang: json.loads((ROOT / relative / f'{lang}.json').read_text(encoding='utf-8'))
                        for lang in ('en', 'zh-CN')}
            grouped = defaultdict(dict)
            for key, chinese in catalogs['zh-CN'].items():
                grouped[chinese][key] = catalogs['en'][key]
            for chinese, entries in grouped.items():
                translations = set(entries.values())
                if len(translations) > 1:
                    with self.subTest(catalog=relative, chinese=chinese, entries=entries):
                        self.assertIn(chinese, CONTEXTUAL_COPIES)
                        self.assertEqual(entries, {key: value for key, value in CONTEXTUAL_COPIES[chinese].items()
                                                   if key in catalogs['zh-CN']})

    def test_product_conversations_are_distinct_from_tasks_and_terminal_sessions(self):
        catalog = json.loads((ROOT / 'harness/i18n/locales/en.json').read_text(encoding='utf-8'))
        chinese = json.loads((ROOT / 'harness/i18n/locales/zh-CN.json').read_text(encoding='utf-8'))
        for key, value in catalog.items():
            if key.startswith(('ui.', 'browser.')) and ('对话' in chinese[key] or '聊天' in chinese[key]):
                # 终端进程是独立资源，不改成模型对话。
                if key == 'ui.lib.commandResults.b6e712c3287b':
                    continue
                with self.subTest(key=key):
                    visible = re.sub(r'\{[A-Za-z_][A-Za-z_0-9]*\}', '', value)
                    self.assertIsNone(re.search(r'\b(?:sessions?|chats?|chatting)\b', visible, re.I))
        expected = {
            'python.sessions.invalid_field': 'The conversation field {field} must be a string or null.',
            'python.subagent_model_tools.a567bdaa1136': 'Medium',
            'ui.screenshot.width1': 'Medium',
            'ui.projectBoard.tasks': 'Task queue',
            'ui.components.chat.Conversation.e33a219fc51f': 'Start a conversation',
            'ui.components.overlays.CommandPalette.58e21b8737cc': 'New conversation',
            'ui.components.overlays.ConversationSearch.159dfc134c9b': 'Untitled conversation',
            'python.terminal_tools.8a8b1f274e12': 'Terminal session ID',
            'python.terminal_tools.af3e3983d192': 'Terminal session ID returned by terminal_open',
        }
        for key, value in expected.items():
            with self.subTest(key=key):
                self.assertEqual(catalog[key], value)
        self.assertIn('scope=session', catalog['python.memory.save_tool'])
        self.assertIn('session/project', catalog['python.memory.invalid_scope'])
