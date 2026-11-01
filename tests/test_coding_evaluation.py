"""固定开发任务验证真实工具、独立验收及报告边界。"""
import copy
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from scripts.evaluate_coding import evaluate_coding_tasks, evaluate_task, independent_check, load_tasks


class CodingEvaluationTests(unittest.IsolatedAsyncioTestCase):
    def test_contract_covers_four_categories_without_duplicate_identity(self):
        tasks = load_tasks()
        self.assertEqual(len(tasks), 12)
        for category in ('bugfix', 'feature', 'refactor', 'recovery'):
            self.assertEqual(sum(task['category'] == category for task in tasks), 3)

    async def test_unknown_task_is_rejected_before_execution(self):
        with self.assertRaises(ValueError):
            await evaluate_coding_tasks(task_ids=['missing'])

    async def test_online_refuses_before_reading_credentials_or_running_code(self):
        with self.assertRaisesRegex(ValueError, 'isolated execution'):
            await evaluate_coding_tasks(mode='online')

    async def test_every_starter_fails_and_reference_satisfies_independent_acceptance(self):
        for task in load_tasks():
            for key, expected in [('files', False), ('reference_files', True)]:
                with self.subTest(task=task['id'], files=key), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    for name, content in task[key].items():
                        (root / name).write_text(content, encoding='utf-8', newline='')
                    passed, error = await independent_check(root, task['checks'])
                    self.assertEqual(passed, expected, error)

    async def test_independent_acceptance_rejects_wrong_result(self):
        task = copy.deepcopy(load_tasks()[0])
        task['checks'][0]['expected'] = False
        result = await evaluate_task(task)
        self.assertFalse(result['passed'])
        self.assertTrue(result['errors'])

    async def test_repeat_has_independent_execution_identity(self):
        report = await evaluate_coding_tasks(task_ids=['bug-empty'], repeat=2)
        self.assertTrue(report['passed'], report)
        self.assertFalse(report['model_quality_evaluated'])
        self.assertNotEqual(report['results'][0]['evaluation_id'], report['results'][1]['evaluation_id'])

    async def test_accounts_are_constructed_only_in_owned_temporary_directories(self):
        from harness.llm.chatgpt_auth import ChatGPTAccount
        roots = []
        def account(home):
            roots.append(Path(home))
            return ChatGPTAccount(home)
        with patch('harness.llm.chatgpt_auth.ChatGPTAccount', side_effect=account):
            result = await evaluate_task(next(task for task in load_tasks() if task['id'] == 'bug-empty'))
        self.assertTrue(result['passed'], result)
        self.assertEqual(len(roots), 1)
        self.assertTrue(all(path.name == '.gateway' and path.parent.name.startswith('harness-coding-eval-') for path in roots))

    def test_invalid_task_file_does_not_escape_project(self):
        task = copy.deepcopy(load_tasks()[0])
        task['files'] = {'../escape.py': 'value = 1'}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'tasks.json'
            path.write_text(json.dumps({'schema': 1, 'tasks': [task]}), encoding='utf-8')
            with self.assertRaises(ValueError):
                load_tasks(path)
