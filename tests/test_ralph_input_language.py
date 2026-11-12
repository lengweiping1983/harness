"""Ralph 当前参数严格校验，提示按调用语言生成且不改写用户报告。"""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from harness.core.errors import ToolExecutionError
from harness.core.language import current_language
from harness.i18n import tr
from harness.tools.builtin.ralph_tools import build_ralph_tools, resolve_max_rounds, render_result


class RalphInputLanguageTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_objective_or_round_type_never_starts_an_agent(self):
        for language in ('en', 'zh-CN', 'en'):
            manager = SimpleNamespace(spawn=Mock(), wait=AsyncMock())
            tool = build_ralph_tools(manager)[0]
            ctx = SimpleNamespace(session_id='fixture')
            with current_language.set(language):
                cases = [{'objective': value} for value in (None, False, True, 42, [], {}, '', ' ')]
                cases += [{'objective': 'Original goal 原文', 'maxRounds': value}
                          for value in (None, '2', 2.0, 1.5, float('inf'), float('nan'), 10**400, True, 0, -1, 65)]
                for args in cases:
                    with self.subTest(language=language, args=args), self.assertRaises(ToolExecutionError) as caught:
                        await tool.execute(args, ctx)
                    self.assertEqual(any('\u4e00' <= c <= '\u9fff' for c in str(caught.exception)), language == 'zh-CN')
                manager.spawn.assert_not_called()
                manager.wait.assert_not_called()
                for value in (1, 64):
                    self.assertEqual(resolve_max_rounds(value), value)

    async def test_timeout_reason_and_terminal_text_follow_call_language(self):
        report = {'status': 'complete', 'summary': 'Original summary 原文', 'evidence': ['Original proof 原文'],
                  'nextSteps': [], 'blocker': ''}
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                for status in ('complete', 'blocked', 'budget-limited'):
                    for count in (1, 2):
                        rendered = render_result({'status': status, 'roundsStarted': count, 'report': report})
                        self.assertIn(json.dumps(report, ensure_ascii=False, indent=2), rendered)
                        self.assertIn(str(count), rendered)
                        self.assertNotIn('1 rounds', rendered)
                manager = SimpleNamespace(spawn=Mock(return_value=SimpleNamespace(id='owned')),
                    wait=AsyncMock(return_value=SimpleNamespace(id='owned', finished=False)), interrupt=AsyncMock())
                with self.assertRaises(ToolExecutionError):
                    await build_ralph_tools(manager)[0].execute({'objective': 'Original goal 原文', 'maxRounds': 1},
                                                              SimpleNamespace(session_id='fixture'))
                manager.interrupt.assert_awaited_once()
                reason = manager.interrupt.call_args.kwargs['reason']
                self.assertEqual(any('\u4e00' <= c <= '\u9fff' for c in reason), language == 'zh-CN')
                self.assertIn('1', reason)
                prompt = manager.spawn.call_args.kwargs['prompt']
                self.assertIn('Original goal 原文', prompt)
                self.assertIn(tr('python.ralph_tools.eda2bfab0704'), prompt)

    async def test_current_reports_drive_complete_blocked_and_budget_outcomes(self):
        continuing = {'status': 'continue', 'summary': 'Original progress 原文', 'evidence': [],
                      'nextSteps': ['Original next step 原文'], 'blocker': ''}
        complete = {'status': 'complete', 'summary': 'Original completion 原文', 'evidence': ['Original proof 原文'],
                    'nextSteps': [], 'blocker': ''}
        blocked = {'status': 'blocked', 'summary': 'Original blocker 原文', 'evidence': [],
                   'nextSteps': [], 'blocker': 'Original dependency 原文'}
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                for reports, expected in (([continuing, complete], 'complete'), ([blocked], 'blocked'),
                                          ([continuing], 'budget-limited')):
                    with self.subTest(language=language, expected=expected):
                        manager = SimpleNamespace(spawn=Mock(return_value=SimpleNamespace(id='fixture-agent')),
                            wait=AsyncMock(side_effect=[SimpleNamespace(finished=True, status='completed',
                                output=json.dumps(report, ensure_ascii=False)) for report in reports]))
                        result = await build_ralph_tools(manager)[0].execute(
                            {'objective': ' Original goal 原文 ', 'maxRounds': len(reports)}, SimpleNamespace(session_id='fixture'))
                        self.assertEqual(result.data['status'], expected)
                        self.assertEqual(result.data['roundsStarted'], len(reports))
                        self.assertEqual(result.data['report'], reports[-1])
                        self.assertEqual(manager.spawn.call_count, len(reports))
                        if len(reports) > 1:
                            self.assertIn(json.dumps(continuing, ensure_ascii=False, indent=2),
                                          manager.spawn.call_args.kwargs['prompt'])

    async def test_deployment_limits_reject_invalid_values_before_tool_registration(self):
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                for field, values in (
                    ('round_timeout', (None, True, False, '2', [], {}, 0, -1, float('inf'), float('nan'), 10**400)),
                    ('max_rounds_ceiling', (None, True, False, '2', [], {}, 0, -1, 1.5, float('inf'))),
                ):
                    for value in values:
                        with self.subTest(language=language, field=field, value=value):
                            manager = SimpleNamespace(spawn=Mock(), wait=AsyncMock())
                            with self.assertRaises(ValueError) as caught:
                                build_ralph_tools(manager, **{field: value})
                            self.assertEqual(str(caught.exception), tr('ralph.config.' + field))
                            manager.spawn.assert_not_called()
                            manager.wait.assert_not_called()
                for options in ({'round_timeout': 1e308, 'max_rounds_ceiling': 64}, {'round_timeout': 1.0, 'max_rounds_ceiling': 10**400}):
                    with self.assertRaises(ValueError) as caught:
                        build_ralph_tools(SimpleNamespace(), **options)
                    self.assertEqual(str(caught.exception), tr('ralph.config.round_timeout'))
                for options in ({'round_timeout': .01, 'max_rounds_ceiling': 1}, {'round_timeout': 900, 'max_rounds_ceiling': 64}, {'round_timeout': 1, 'max_rounds_ceiling': 128}):
                    self.assertEqual(len(build_ralph_tools(SimpleNamespace(), **options)), 1)
