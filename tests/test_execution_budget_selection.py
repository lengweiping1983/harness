"""目标预算选择保持激活条件、当前额度与普通回合预算的独立性。"""
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from harness.app.goal_budget import GoalExecutionBudget, resolve_execution_budget
from harness.app.runtime import ExecutionBudget


class ExecutionBudgetSelectionTests(unittest.TestCase):
    def test_only_active_armed_goal_selects_persistent_budget(self):
        for enabled in (False, True):
            for phase in ('active', 'paused', 'blocked', 'complete'):
                for activation in ('armed', 'disarmed'):
                    with self.subTest(enabled=enabled, phase=phase, activation=activation):
                        goal = SimpleNamespace(id='goal', phase=phase, activation=activation,
                                               token_budget=1000, tokens_used=100, budget_reservations={'pending': 200})
                        get = Mock(return_value=goal)
                        harness = SimpleNamespace(config=SimpleNamespace(enable_goal=enabled, token_budget=800),
                                                  goals=SimpleNamespace(get=get))
                        budget = resolve_execution_budget(harness, SimpleNamespace(id='session'))
                        if enabled and phase == 'active' and activation == 'armed':
                            self.assertIsInstance(budget, GoalExecutionBudget)
                            self.assertEqual((budget.session_id, budget.goal_id, budget.limit), ('session', 'goal', 700))
                        else:
                            self.assertIs(type(budget), ExecutionBudget)
                            self.assertEqual(budget.limit, 800)
                        if not enabled:
                            get.assert_not_called()

    def test_no_goal_preserves_unlimited_budget_and_does_not_share_instances(self):
        harness = SimpleNamespace(config=SimpleNamespace(enable_goal=True, token_budget=None),
                                  goals=SimpleNamespace(get=Mock(return_value=None)))
        first = resolve_execution_budget(harness, SimpleNamespace(id='session'))
        second = resolve_execution_budget(harness, SimpleNamespace(id='session'))
        self.assertIs(type(first), ExecutionBudget)
        self.assertIsNot(first, second)
        self.assertIsNone(first.limit)


class ExternalBudgetOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_agent_budget_survives_external_command_admission(self):
        from harness.app.external_agent_commands import run_external_agent_command

        budget = ExecutionBudget(800)
        budget.spent = 50
        parent = SimpleNamespace(reset=Mock(), recovery_read_only=False, budget=budget)
        failure = RuntimeError('turn admission refused')
        session = SimpleNamespace(id='session', start_turn=AsyncMock(side_effect=failure))
        with patch('harness.app.external_agent_commands.resolve_execution_budget') as resolve:
            with self.assertRaises(RuntimeError) as caught:
                await run_external_agent_command(SimpleNamespace(), session, 'codex', 'Original task', {}, agent=parent)
        self.assertIs(caught.exception, failure)
        resolve.assert_not_called()
        self.assertIs(parent.budget, budget)
        self.assertEqual(parent.budget.spent, 50)
