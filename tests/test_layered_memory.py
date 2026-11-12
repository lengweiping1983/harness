"""验证记忆层之间的隔离、容量和独立失败行为。"""
import tempfile
import os
import subprocess
import unittest
from pathlib import Path

from harness.app.layered_memory import LayeredMemory
from harness.app.memory import ProjectMemory
from harness.core.errors import HarnessError


class LayeredMemoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.a, self.b = self.root / 'project-a', self.root / 'project-b'
        self.a.mkdir()
        self.b.mkdir()
        self.memory = LayeredMemory(self.root / 'state', ProjectMemory())

    def test_user_shared_project_and_session_isolated(self):
        self.memory.user.save(self.a, '偏好中文', pinned=True)
        self.memory.project.save(self.a, '甲项目内部架构', pinned=True)
        self.memory.store('session', session_id='one').save(self.a, '会话一任务笔记', pinned=True)
        first = self.memory.context(self.a, session_id='one')
        second = self.memory.context(self.b, session_id='two')
        self.assertIn('偏好中文', first)
        self.assertIn('甲项目内部架构', first)
        self.assertIn('会话一任务笔记', first)
        self.assertIn('偏好中文', second)
        self.assertNotIn('甲项目内部架构', second)
        self.assertNotIn('会话一任务笔记', second)

    def test_new_instance_restores_same_scopes(self):
        self.memory.store('session', session_id='one').save(self.a, '可恢复笔记', pinned=True)
        restored = LayeredMemory(self.root / 'state', ProjectMemory())
        self.assertIn('可恢复笔记', restored.context(self.a, session_id='one'))
        self.assertNotIn('可恢复笔记', restored.context(self.a, session_id='two'))

    def test_session_identity_cannot_escape_home(self):
        store = self.memory.store('session', session_id='../../outside')
        self.assertTrue(store.path(self.a).is_relative_to(self.memory.home / 'memory' / 'sessions'))
        self.assertNotEqual(store.path(self.a), self.memory.store('session', session_id='outside').path(self.a))
        for scope, identity in [('session', ''), ('unknown', 'one')]:
            with self.assertRaises(HarnessError):
                self.memory.store(scope, session_id=identity)

    def test_corrupt_user_layer_does_not_hide_project(self):
        path = self.memory.user.path(self.a)
        path.parent.mkdir(parents=True)
        path.write_bytes(b'not a sqlite database')
        self.memory.project.save(self.a, '健康项目资料', pinned=True)
        result = self.memory.context(self.a, session_id='one')
        self.assertIn('健康项目资料', result)
        self.assertIn('user', result)
        self.assertEqual(path.read_bytes(), b'not a sqlite database')

    def test_combined_budget_and_no_read_side_effects(self):
        self.assertEqual(self.memory.context(self.a, session_id='one'), '')
        self.assertFalse(self.memory.home.exists())
        for scope in ('user', 'project', 'session'):
            self.memory.store(scope, session_id='one').save(self.a, '预算测试' * 30, pinned=True)
        for budget in (0, 1, 100, 500, 1000, 8000):
            with self.subTest(budget=budget):
                result = self.memory.context(self.a, session_id='one', budget=budget)
                self.assertLessEqual(len(result), budget)

    def test_separate_homes_do_not_share_user_preferences(self):
        self.memory.user.save(self.a, '私人偏好', pinned=True)
        other = LayeredMemory(self.root / 'other-state', ProjectMemory())
        self.assertNotIn('私人偏好', other.context(self.a, session_id='one'))

    def test_empty_layers_release_capacity_for_large_project_record(self):
        row = self.memory.project.save(self.a, '队列架构依据' * 640, pinned=True)
        report = self.memory.preview(self.a, session_id='one')
        project = report.layers[1]
        self.assertEqual(project.budget, 8000)
        self.assertEqual([item.id for item in project.recall.selected], [row['id']])
        self.assertLessEqual(len(report.text), 8000)
        self.assertEqual(report.layers[0].budget, 0)
        self.assertEqual(report.layers[2].budget, 0)

    def test_nonmatching_layer_releases_capacity_but_keeps_explanation(self):
        self.memory.user.save(self.a, '无关字体偏好')
        self.memory.project.save(self.a, '队列架构依据' * 640, pinned=True)
        report = self.memory.preview(self.a, session_id='one', query='队列')
        self.assertEqual(report.layers[0].recall.unmatched, 1)
        self.assertEqual(report.layers[0].budget, 0)
        self.assertEqual(len(report.layers[1].recall.selected), 1)

    def test_active_layers_keep_reserved_capacity_and_read_once(self):
        from unittest.mock import patch
        self.memory.user.save(self.a, '用户偏好', pinned=True)
        self.memory.project.save(self.a, '项目事实', pinned=True)
        original = ProjectMemory.list
        calls = []
        def read(store, cwd, *args, **kwargs):
            calls.append(str(store.path(cwd)))
            return original(store, cwd, *args, **kwargs)
        with patch.object(ProjectMemory, 'list', read):
            report = self.memory.preview(self.a, session_id='one', budget=8000)
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(set(calls)), 3)
        self.assertEqual(report.layers[0].budget, 2667)
        self.assertEqual(report.layers[1].budget, 5332)
        self.assertEqual(report.layers[2].budget, 0)
        self.assertLessEqual(len(report.text), 8000)

    def test_all_active_layers_preserve_original_shares_at_tiny_budgets(self):
        for scope in ('user', 'project', 'session'):
            self.memory.store(scope, session_id='one').save(self.a, scope, pinned=True)
        for budget in range(17):
            report = self.memory.preview(self.a, session_id='one', budget=budget)
            self.assertLessEqual(len(report.text), budget)
            self.assertEqual(sum(layer.budget for layer in report.layers), budget)

    def test_unavailable_layer_keeps_error_share_while_empty_session_yields(self):
        path = self.memory.user.path(self.a)
        path.parent.mkdir(parents=True)
        path.write_bytes(b'corrupt-preserve')
        self.memory.project.save(self.a, '队列架构依据' * 640, pinned=True)
        report = self.memory.preview(self.a, session_id='one')
        self.assertEqual(report.layers[0].status, 'unavailable')
        self.assertEqual(report.layers[0].budget, 2000)
        self.assertEqual(report.layers[1].budget, 5999)
        self.assertEqual(len(report.layers[1].recall.selected), 1)
        self.assertEqual(path.read_bytes(), b'corrupt-preserve')
        self.assertLessEqual(len(report.text), 8000)

    def test_export_cannot_accidentally_import_into_another_layer(self):
        self.memory.project.save(self.a, '项目内部信息')
        export = self.memory.project.export(self.a)
        with self.assertRaises(HarnessError):
            self.memory.user.import_data(self.a, export)
        self.assertEqual(self.memory.user.list(self.a), [])
        for scope in ('user', 'session'):
            store = self.memory.store(scope, session_id='one')
            store.save(self.a, '资料')
            payload = store.export(self.a)
            self.assertEqual(payload['format'], f'harness-{scope}-memory-v1')
            self.assertEqual(store.import_data(self.a, payload), {'imported': 0, 'duplicates': 1})

    def test_preexisting_session_directory_link_cannot_redirect_storage(self):
        directory = self.memory.home / 'memory' / 'sessions'
        directory.parent.mkdir(parents=True)
        target = self.root / 'outside'
        target.mkdir()
        if os.name == 'nt':
            result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(directory), str(target)],
                                    capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            directory.symlink_to(target, target_is_directory=True)
        try:
            store = self.memory.store('session', session_id='one')
            with self.assertRaises(HarnessError):
                store.save(self.a, '不得重定向')
            self.assertEqual(list(target.iterdir()), [])
        finally:
            # 仅移除本测试创建的链接，不递归操作目标目录。
            if os.name == 'nt':
                directory.rmdir()
            else:
                directory.unlink()
