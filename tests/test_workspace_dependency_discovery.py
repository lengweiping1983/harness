"""工作区发现绑定调用目录，模型正文保留可用路径和包名。"""
import json
import os
import threading
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from harness.core.errors import ToolExecutionError
from harness.core.language import current_language
from harness.i18n import tr
from harness.tools.builtin import workspace_tools as module
from harness.tools.processes import ProcessCleanupError
from tests.filesystem_links import directory_link


class WorkspaceDependencyDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_parallel_versions_keep_each_call_language_in_real_tool_errors(self):
        tool = module.build_workspace_dependency_tools()[0]
        with tempfile.TemporaryDirectory() as directory, patch.object(module, '_cached', side_effect=lambda key, produce: produce()), \
                patch.object(module.shutil, 'which', side_effect=lambda name: '/fixture/' + name if name in ('node', 'npm') else None):
            for language in ('en', 'zh-CN', 'en'):
                observed, barrier = [], threading.Barrier(2)
                def fail(path):
                    barrier.wait(5)
                    observed.append(current_language.get())
                    if path.endswith('/node'):
                        raise ProcessCleanupError(tr('python.processes.d0d6da778004'))
                    return 'fixture version'
                with current_language.set(language), patch.object(module, '_version_of', side_effect=fail):
                    with self.assertRaises(ToolExecutionError) as caught:
                        await tool.execute({'include_packages': False}, SimpleNamespace(cwd=directory))
                    self.assertEqual(caught.exception.message, tr('python.workspace_tools.484acd7994ec',
                        p0=str(ProcessCleanupError(tr('python.processes.d0d6da778004')))))
                    self.assertIsInstance(caught.exception.__cause__, ProcessCleanupError)
                    self.assertEqual(caught.exception.__cause__.message, tr('python.processes.d0d6da778004'))
                self.assertEqual(observed, [language, language])

    async def test_parallel_probe_failures_preserve_every_original_cleanup_cause(self):
        errors = {name: ProcessCleanupError('Original ' + name + ' cleanup') for name in ('node', 'npm')}
        def fail(path):
            raise errors[Path(path).name]
        tool = module.build_workspace_dependency_tools()[0]
        with tempfile.TemporaryDirectory() as directory, patch.object(module, '_cached', side_effect=lambda key, produce: produce()), \
                patch.object(module.shutil, 'which', side_effect=lambda name: '/fixture/' + name if name in errors else None), \
                patch.object(module, '_version_of', side_effect=fail):
            with self.assertRaises(ToolExecutionError) as caught:
                await tool.execute({'include_packages': False}, SimpleNamespace(cwd=directory))
        group = caught.exception.__cause__
        self.assertIsInstance(group, ExceptionGroup)
        self.assertEqual(group.exceptions, (errors['node'], errors['npm']))

    async def test_same_tool_uses_each_workspace_and_exposes_packages_to_model(self):
        tool = module.build_workspace_dependency_tools()[0]
        process_cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            first, second = root / 'first', root / 'second'
            first.mkdir()
            second.mkdir()
            (first / '.venv').mkdir()
            (first / 'package.json').write_bytes(b'\xffinvalid metadata is not read')
            (second / 'node_modules').mkdir()
            with patch.object(module, '_node_runtimes', return_value=[]), patch.object(module, '_python_packages',
                    return_value=([{'name': 'FixturePackage', 'version': '1.2'}], 1)):
                for language, selected in (('en', first), ('zh-CN', second), ('en', first)):
                    with self.subTest(language=language), current_language.set(language):
                        result = await tool.execute({}, SimpleNamespace(cwd=str(selected)))
                        data = result.data
                        self.assertEqual(data['cwd'], str(selected))
                        self.assertEqual(data['process_cwd'], process_cwd)
                        self.assertEqual(os.getcwd(), process_cwd)
                        self.assertEqual(data['python']['source'], 'harness_process')
                        self.assertIn('FixturePackage', result.content[0].text)
                        self.assertIn(json.dumps(str(selected), ensure_ascii=False), result.content[0].text)
                        self.assertIn(tr('workspace.runtime_scope'), result.content[0].text)
                        expected = '.venv' if selected == first else 'node_modules'
                        self.assertEqual([Path(item['path']).name for item in data['workspace']['dependency_roots']], [expected])
                        self.assertEqual([Path(item['path']).name for item in data['workspace']['manifests']],
                                         ['package.json'] if selected == first else [])
            self.assertEqual((first / 'package.json').read_bytes(), b'\xffinvalid metadata is not read')
        self.assertEqual(tool.effect, 'read')
        self.assertTrue(tool.idempotent)

    async def test_false_omits_package_probe_and_layout_refreshes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            with patch.object(module, '_node_runtimes', return_value=[]), patch.object(module, '_python_packages') as packages:
                before = module.collect_workspace_dependencies(cwd=root, include_packages=False)
                (root / 'node_modules').mkdir()
                after = module.collect_workspace_dependencies(cwd=root, include_packages=False)
            packages.assert_not_called()
            self.assertNotIn('packages', after['python'])
            self.assertNotIn('package_count', after['python'])
            self.assertEqual(before['workspace']['dependency_roots'], [])
            self.assertEqual(after['workspace']['dependency_roots'],
                             [{'path': str(root / 'node_modules'), 'linked': False, 'kind': 'node_modules'}])

    async def test_invalid_workspace_and_unreadable_layout_do_not_report_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            file = root / 'ordinary-file'
            file.write_text('Original 原文', encoding='utf-8')
            for language in ('en', 'zh-CN', 'en'):
                with current_language.set(language), patch.object(module, '_node_runtimes') as node:
                    for value in ('', 1, False, [], '\0', file, root / 'missing'):
                        with self.subTest(value=value), self.assertRaises(ToolExecutionError) as error:
                            module.collect_workspace_dependencies(cwd=value)
                        self.assertEqual(error.exception.message, tr('workspace.invalid_cwd'))
                    node.assert_not_called()
            original = PermissionError('Original directory failure')
            tool = module.build_workspace_dependency_tools()[0]
            with patch.object(module, '_node_runtimes', return_value=[]), patch.object(module, '_workspace_layout', side_effect=original):
                with self.assertRaises(ToolExecutionError) as error:
                    await tool.execute({'include_packages': False}, SimpleNamespace(cwd=str(root)))
            self.assertIs(error.exception.__cause__, original)
            self.assertEqual(file.read_text(encoding='utf-8'), 'Original 原文')

    async def test_dependency_links_are_marked_without_following_or_reading_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            workspace, outside = root / 'workspace', root / 'outside'
            workspace.mkdir()
            outside.mkdir()
            (outside / 'secret').write_text('Must not read', encoding='utf-8')
            directory_link(workspace / 'node_modules', outside)
            if os.name != 'nt':
                (workspace / 'package.json').symlink_to(outside / 'secret')
            with patch.object(module, '_node_runtimes', return_value=[]):
                data = module.collect_workspace_dependencies(cwd=workspace, include_packages=False)
            self.assertEqual(data['workspace']['dependency_roots'],
                [{'path': str(workspace / 'node_modules'), 'linked': True, 'kind': 'node_modules'}])
            self.assertEqual(data['workspace']['manifests'],
                             [{'path': str(workspace / 'package.json'), 'linked': True}] if os.name != 'nt' else [])
            self.assertNotIn(str(outside), json.dumps(data))

    async def test_path_changes_invalidate_runtime_cache_without_polluting_previous_snapshot(self):
        with module._PROBE_LOCK:
            original = dict(module._PROBE_CACHE)
            module._PROBE_CACHE.clear()
        try:
            with patch.object(module, '_probe_node_runtimes', side_effect=[
                    [{'command': 'node', 'path': '/first/node', 'version': '1'}],
                    [{'command': 'node', 'path': '/second/node', 'version': '2'}]]) as probe:
                with patch.dict(os.environ, {'PATH': '/first'}):
                    first = module._node_runtimes()
                    first[0]['path'] = 'Caller change'
                with patch.dict(os.environ, {'PATH': '/second'}):
                    self.assertEqual(module._node_runtimes()[0]['path'], '/second/node')
                with patch.dict(os.environ, {'PATH': '/first'}):
                    self.assertEqual(module._node_runtimes()[0]['path'], '/first/node')
                self.assertEqual(probe.call_count, 2)
        finally:
            with module._PROBE_LOCK:
                module._PROBE_CACHE.clear()
                module._PROBE_CACHE.update(original)
