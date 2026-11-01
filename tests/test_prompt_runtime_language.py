"""导入后切换语言，持久开发者提示和交付说明仍按本次上下文解析。"""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from harness.app.plan_mode import PlanModeController, plan_policy_section
from harness.app.hook_output import context_preview
from harness.core.errors import ToolExecutionError
from harness.core.language import current_language
from harness.i18n import tr
from harness.session.store import SessionStore
from harness.tools.builtin.present_tools import build_present_tools


class PromptRuntimeLanguageTests(unittest.IsolatedAsyncioTestCase):
    async def test_plan_policy_and_persisted_narration_follow_runtime_language(self):
        store = SessionStore()
        controller = PlanModeController(store)
        for language in ('en', 'zh-CN', 'en'):
            session = store.create()
            with current_language.set(language):
                self.assertEqual(plan_policy_section(), tr('owned.plan_mode.f50c7c7b353e'))
                for active, key in ((True, 'python.plan_mode.6d0ccc16d935'), (False, 'python.plan_mode.0f01235ff956')):
                    await controller.set(session, active)
                    self.assertEqual(session.latest('developer/message').data['text'], tr(key))

    async def test_present_description_and_lists_use_runtime_punctuation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'original.txt').write_text('original')
            ctx = SimpleNamespace(cwd=directory, call_id='fixture', session_id='fixture', turn=1, step=1)
            for language in ('en', 'zh-CN', 'en'):
                with current_language.set(language):
                    tool = build_present_tools()[0]
                    self.assertEqual(tool.schema.description, tr('python.present_tools.2cfa899caa53', p0=2, p1=8))
                    result = await tool.execute({'files': [{'path': 'original.txt', 'description': 'User 原文'}]}, ctx)
                    self.assertIn('(User 原文)' if language == 'en' else '（User 原文）', result.content[0].text)
                    with self.assertRaises(ToolExecutionError) as caught:
                        await tool.execute({'files': [{'path': 'missingA'}, {'path': 'missingB'}]}, ctx)
                    self.assertIn(tr('ui.common.list_separator').join(['missingA', 'missingB']), caught.exception.message)

    async def test_hook_preview_reports_saved_original_and_unsaved_failure_in_both_languages(self):
        text = 'Original 原文\u2028' * 2000
        for language in ('en', 'zh-CN', 'en'):
            with tempfile.TemporaryDirectory() as directory, current_language.set(language):
                root = Path(directory)
                preview = context_preview(text, root, 'fixture', limit=100)
                saved = list(root.rglob('*.txt'))
                self.assertEqual(len(saved), 1)
                self.assertEqual(saved[0].read_text(encoding='utf-8'), text)
                self.assertIn(tr('hook.output.preview', path=saved[0]), preview)
                with patch('harness.app.hook_output.os.open', side_effect=OSError('fixture failure')):
                    failed = context_preview(text, root, 'fixture', limit=100)
                self.assertIn(tr('hook.output.unsaved'), failed)

    async def test_plan_title_cannot_borrow_text_from_the_next_line(self):
        from unittest.mock import AsyncMock
        from harness.app import plans
        from harness.tools.builtin.plan_tools import build_exit_plan_mode_tool
        store = SessionStore()
        session = store.create()
        controller = PlanModeController(store)
        await controller.set(session, True)
        broker = SimpleNamespace(ask=AsyncMock(return_value=[{'id': 'plan-review', 'selected': ['Keep planning']}]))
        tool = build_exit_plan_mode_tool(controller, broker)
        ctx = SimpleNamespace(services={'session': session}, session_id=session.id, call_id='fixture', turn=1, step=1)
        for plan in ('#\nTitle', '#\rTitle', '#\u2028Title', '# \nTitle'):
            with self.subTest(plan=plan), self.assertRaises(ToolExecutionError):
                await tool.execute({'plan': plan}, ctx)
            self.assertEqual(plans.versions(session), [])
            broker.ask.assert_not_awaited()
        for plan, title in (('# Title\nbody', 'Title'), ('#\t中文标题\r\n正文', '中文标题')):
            result = await tool.execute({'plan': plan}, ctx)
            self.assertEqual(result.data['title'], title)
            self.assertEqual(plans.versions(session)[-1]['title'], title)
