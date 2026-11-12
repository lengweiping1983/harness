"""验证选测边界、完整构建与交付回执的失败语义。"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import yaml
from unittest.mock import patch

from scripts import verify


class VerificationPolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = verify.load_policy()

    def tasks(self, *paths):
        return set(verify.plan_checks(list(paths), self.policy)['tasks'])

    def test_document_backend_frontend_and_desktop_keep_build(self):
        self.assertEqual(self.tasks('docs/api.md'), {'check', 'build'})
        self.assertEqual(self.tasks('harness/llm/transport.py'), {'check', 'build', 'python'})
        self.assertEqual(self.tasks('web/src/lib/http.ts'), {'check', 'build', 'web-unit'})
        self.assertEqual(self.tasks('desktop/downloads.mjs'), {'check', 'build', 'python', 'web-unit'})
        self.assertIn('python', self.tasks('README.md'))

    def test_shared_unknown_and_infrastructure_force_all_core_domains(self):
        full = {'check', 'build', 'python', 'web-unit'}
        for path in ('harness/session/store.py', 'harness/tools/builtin/new.mjs', 'harness/new_domain/data.json',
                     'scripts/new.py', 'web/package-lock.json', 'web/vite.config.ts',
                     'tests/web_fixture.py', 'new-domain/data.json', 'desktop/requirements.lock'):
            with self.subTest(path=path):
                self.assertEqual(self.tasks(path), full)
        self.assertEqual(self.tasks(), full)
        self.assertEqual(set(verify.plan_checks(['docs/api.md'], self.policy, fallback='缺少基线')['tasks']), full)

    def test_mixed_changes_and_full_never_implicitly_start_specialized_suites(self):
        selected = self.tasks('tests/test_questions.py', 'web/src/store/queue.ts', 'harness/llm/transport.py')
        self.assertEqual(selected, {'check', 'build', 'python', 'web-unit'})
        for paths in ([], ['docs/api.md'], ['web/src/lib/http.ts']):
            with self.subTest(paths=paths):
                plan = verify.plan_checks(paths, self.policy, full=True)
                self.assertEqual(set(plan['tasks']), {'check', 'build', 'python', 'web-unit'})

    def test_desktop_and_browser_require_independent_explicit_selection(self):
        for desktop, browser in ((False, False), (True, False), (False, True), (True, True)):
            for full in (False, True):
                with self.subTest(desktop=desktop, browser=browser, full=full):
                    plan = verify.plan_checks(['scripts/verify.py'], self.policy, full=full, desktop=desktop, browser=browser)
                    self.assertEqual('desktop' in plan['tasks'], desktop)
                    self.assertEqual('browser' in plan['tasks'], browser)
                    self.assertTrue({'check', 'build', 'python', 'web-unit'} <= set(plan['tasks']))

    def test_invalid_policy_cannot_disable_build_or_hide_unknown_tasks(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'policy.json'
            for content in ({}, [], {**self.policy, 'always': ['check']},
                            *({**self.policy, 'rules': [{'patterns': ['*'], 'tasks': [task], 'reason': '错误'}]}
                              for task in ('typo', 'browser', 'desktop', 'runtime'))):
                path.write_text(json.dumps(content))
                with self.assertRaises(ValueError):
                    verify.load_policy(path)

    def test_manual_browser_workflow_is_separate_from_required_ci(self):
        root = Path(__file__).resolve().parents[1]
        ci = yaml.load((root / '.github/workflows/ci.yml').read_text(encoding='utf-8'), Loader=yaml.BaseLoader)
        browser = yaml.load((root / '.github/workflows/browser-tests.yml').read_text(encoding='utf-8'), Loader=yaml.BaseLoader)
        desktop = yaml.load((root / '.github/workflows/desktop-tests.yml').read_text(encoding='utf-8'), Loader=yaml.BaseLoader)
        installers = yaml.load((root / '.github/workflows/desktop-build.yml').read_text(encoding='utf-8'), Loader=yaml.BaseLoader)
        self.assertEqual(set(ci['on']), {'push', 'pull_request', 'merge_group', 'workflow_dispatch'})
        self.assertEqual(set(browser['on']), {'workflow_dispatch'})
        self.assertEqual(set(desktop['on']), {'workflow_dispatch'})
        self.assertEqual(set(installers['on']), {'workflow_dispatch'})
        self.assertEqual(set(ci['jobs']), {'unittest', 'web', 'required'})
        self.assertEqual(set(ci['jobs']['required']['needs']), {'unittest', 'web'})
        commands = '\n'.join(step.get('run', '') for job in ci['jobs'].values() for step in job['steps'])
        self.assertIn('npm --prefix web run test:unit', commands)
        self.assertIn('npm --prefix web run build', commands)
        self.assertNotIn('test:browser', commands)
        self.assertNotIn('npm --prefix desktop test', commands)
        web = json.loads((root / 'web/package.json').read_text(encoding='utf-8'))
        self.assertNotIn('test:browser', web['scripts']['test'])
        make = (root / 'Makefile').read_text(encoding='utf-8')
        self.assertIn('test: check test-python test-web\n', make)
        self.assertEqual(browser['jobs']['browser']['strategy']['matrix']['shard'], ['1', '2', '3', '4'])


class VerificationGitTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.git('init', '-q')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('config', 'user.name', '验证夹具')
        for name in ('staged.py', 'unstaged.py', 'deleted.py', 'old name.py', 'overlap.py'):
            (self.root / name).write_text('before')
        self.git('add', '.')
        self.git('commit', '-qm', 'fixture')

    def git(self, *args):
        return verify.git(self.root, *args)

    def test_changes_include_all_states_and_both_rename_paths(self):
        (self.root / 'staged.py').write_text('staged')
        (self.root / 'overlap.py').write_text('staged')
        self.git('add', 'staged.py', 'overlap.py')
        (self.root / 'overlap.py').write_text('before')
        (self.root / 'unstaged.py').write_text('unstaged')
        (self.root / 'deleted.py').unlink()
        self.git('mv', 'old name.py', 'new name.py')
        (self.root / 'untracked 中文.py').write_text('new')
        self.assertEqual(set(verify.changed_files(self.root, 'HEAD')), {
            'staged.py', 'unstaged.py', 'deleted.py', 'old name.py', 'new name.py',
            'overlap.py', 'untracked 中文.py'})

    def test_branch_base_includes_committed_changes_and_bad_base_fails(self):
        baseline = self.git('rev-parse', 'HEAD').decode().strip()
        (self.root / 'staged.py').write_text('branch')
        self.git('add', '.')
        self.git('commit', '-qm', 'branch')
        self.assertEqual(verify.changed_files(self.root, baseline), ['staged.py'])
        with self.assertRaises(subprocess.CalledProcessError):
            verify.changed_files(self.root, 'missing-ref')

    def test_input_fingerprint_detects_content_changes_and_ignores_build_outputs(self):
        (self.root / '.gitignore').write_text('output/\n.run/\nweb/dist/\n')
        before = verify.source_fingerprint(self.root)
        (self.root / 'output').mkdir()
        (self.root / 'output' / 'record').write_text('derived')
        self.assertEqual(verify.source_fingerprint(self.root), before)
        (self.root / 'unstaged.py').write_text('after')
        self.assertNotEqual(verify.source_fingerprint(self.root), before)


class VerificationExecutionTests(unittest.TestCase):
    def write_build(self, root):
        for directory in (root / '.run' / 'web-build', root / 'web' / 'dist'):
            (directory / 'assets').mkdir(parents=True)
            (directory / 'index.html').write_text('<script src="/assets/main.js"></script>')
            (directory / 'assets' / 'main.js').write_text('complete')
            (directory / 'assets' / 'lazy.js').write_text('lazy chunk')

    def test_build_manifest_rejects_missing_extra_and_corrupt_lazy_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_build(root)
            manifest = verify.build_manifest(root)
            self.assertIn('assets/lazy.js', manifest)
            lazy = root / 'web' / 'dist' / 'assets' / 'lazy.js'
            for content in (None, 'corrupt'):
                if content is None:
                    lazy.unlink()
                else:
                    lazy.write_text(content)
                with self.assertRaises(ValueError):
                    verify.build_manifest(root)
            lazy.write_text('lazy chunk')
            (lazy.parent / 'stale.js').write_text('old')
            with self.assertRaises(ValueError):
                verify.build_manifest(root)

    def test_failure_stops_execution_and_reports_remaining_tasks(self):
        with patch.object(verify, 'source_fingerprint', return_value='same'), \
                patch.object(verify.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1)) as run:
            report = verify.run_plan({'tasks': ['check', 'build', 'python']}, Path('/fixture'), '/python', '/npm')
        self.assertFalse(report['passed'])
        self.assertEqual(run.call_count, 1)
        self.assertEqual(report['results'][-1]['status'], 'not-run')

    def test_success_uses_current_build_and_detects_source_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_build(root)
            for fingerprints, expected in ((['before', 'before'], True), (['before', 'after'], False)):
                with patch.object(verify, 'source_fingerprint', side_effect=fingerprints), \
                        patch.object(verify.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as run:
                    report = verify.run_plan({'tasks': ['build', 'runtime']}, root, '/python', '/npm')
                self.assertEqual(report['passed'], expected)
                self.assertEqual(run.call_args.kwargs['env']['HARNESS_WEB_DIST'], str(root / 'web' / 'dist'))
                self.assertEqual(run.call_args.kwargs['env']['HARNESS_PYTHON'], '/python')
                self.assertTrue(report['build_manifest'])

    def test_modified_artifacts_cannot_pass_after_tests(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_build(root)
            def execute(command, **kwargs):
                if 'runtime-smoke.mjs' in command:
                    (root / 'web' / 'dist' / 'assets' / 'lazy.js').write_text('changed during tests')
                return subprocess.CompletedProcess(command, 0)
            with patch.object(verify, 'source_fingerprint', return_value='same'), \
                    patch.object(verify.subprocess, 'run', side_effect=execute):
                report = verify.run_plan({'tasks': ['build', 'runtime']}, root, '/python', '/npm')
            self.assertFalse(report['passed'])
            self.assertFalse(report['build_unchanged'])

    def test_planning_race_does_not_run_stale_selection(self):
        with patch.object(verify, 'source_fingerprint', return_value='after'), \
                patch.object(verify.subprocess, 'run') as run:
            with self.assertRaisesRegex(ValueError, '规划期间'):
                verify.run_plan({'tasks': ['build']}, Path('/fixture'), '/python', '/npm', expected_input='before')
        run.assert_not_called()

    def test_incomplete_build_marks_failure_and_retains_unexecuted_tasks(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(verify, 'source_fingerprint', return_value='same'), \
                    patch.object(verify.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as run:
                report = verify.run_plan({'tasks': ['build', 'browser']}, Path(directory), '/python', '/npm')
            self.assertFalse(report['passed'])
            self.assertEqual(run.call_count, 1)
            self.assertEqual(report['results'][0]['status'], 'failed')
            self.assertEqual(report['results'][1]['status'], 'not-run')

    def test_bad_policy_replaces_stale_pass_receipt_and_explain_is_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(verify, 'ROOT', root):
                verify.save_report({'passed': True})
                with patch('sys.argv', ['verify.py']), patch.object(verify, 'source_fingerprint', return_value='same'), \
                        patch.object(verify, 'load_policy', side_effect=ValueError('坏策略')):
                    self.assertEqual(verify.main(), 1)
                receipt = root / 'output' / 'verification.json'
                self.assertFalse(json.loads(receipt.read_text(encoding='utf-8'))['passed'])
                before = receipt.read_bytes()
                with patch('sys.argv', ['verify.py', '--explain']), \
                        patch.object(verify, 'load_policy', return_value=verify.load_policy()), \
                        patch.object(verify, 'changed_files', return_value=['docs/api.md']):
                    self.assertEqual(verify.main(), 0)
                self.assertEqual(receipt.read_bytes(), before)

    @unittest.skipUnless(hasattr(os, 'symlink'), '当前系统没有符号链接接口')
    def test_build_manifest_rejects_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_build(root)
            try:
                (root / 'web' / 'dist' / 'linked').symlink_to(root / 'web' / 'dist' / 'index.html')
            except OSError as error:
                self.skipTest(str(error))
            with self.assertRaises(ValueError):
                verify.build_manifest(root)
