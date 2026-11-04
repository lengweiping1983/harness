"""通用课程引擎、安全边界、回执和恢复的业务回归。"""
from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from contextlib import closing
from unittest.mock import patch

from harness.academy.errors import AcademyError
from harness.academy.runtime import AcademyRuntime
from harness.academy.service import AcademyService
from harness.core.language import current_language
from harness.web.api.academy import build_router
from harness.web.errors import ApiError
from harness.web.router import Request
from tests.filesystem_links import directory_link


def write_engine_fixture(root):
    """框架回归使用稳定双文件课程，避免教学文本调整改变恢复测试的触发条件。"""
    directory = root / 'harness-foundations'
    starter = directory / 'lessons/agent-loop/starter'
    starter.mkdir(parents=True)
    (starter / 'agent.py').write_text('''import json
from tools import add

def model_fixture():
    return (2, 3)

def run():
    return add(*model_fixture())

if __name__ == "__main__":
    print(json.dumps({"answer": run(), "events": ["model/fixture", "tool/result"]}))
''', encoding='utf-8')
    (starter / 'tools.py').write_text('def add(a, b):\n    return a + b\n', encoding='utf-8')
    (directory / 'lessons/agent-loop/check.py').write_text('from agent import run\nassert run() == 5\nprint("CHECK PASS")\n', encoding='utf-8')
    manifest = {'schema': 1, 'id': directory.name, 'version': '1.0.0', 'source_revision': 'fixture',
        'title_key': 'title', 'description_key': 'description', 'references': [], 'lessons': [{
            'id': 'agent-loop', 'order': 1, 'milestone': True, 'title_key': 'lesson', 'goal_key': 'goal',
            'reading_key': 'reading', 'starter': 'lessons/agent-loop/starter', 'check': 'lessons/agent-loop/check.py',
            'run_command': ['python', 'agent.py'], 'source_manifest': [{'path': 'agent.py', 'symbol': 'run'}],
            'references': [], 'prerequisites': []}]}
    (directory / 'course.json').write_text(json.dumps(manifest), encoding='utf-8')
    (directory / 'locales').mkdir()
    for language, title in [('en', 'Harness development foundations'), ('zh-CN', 'Harness 开发入门')]:
        (directory / 'locales' / (language + '.json')).write_text(json.dumps({'title': title,
            'description': title, 'lesson': title, 'goal': title, 'reading': title}), encoding='utf-8')


class AcademyServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.library = self.root / 'library'
        write_engine_fixture(self.library)
        self.service = AcademyService(self.root / 'home', catalog_root=self.library)
        self.service.catalog.additional_root = self.root / 'additional'
        self.service.catalog.additional_root.mkdir()
        receipt = self.service.create_project('create', 'harness-foundations')
        self.project = receipt['result']['project']
        self.pid = self.project['id']

    def tearDown(self):
        self.temp.cleanup()

    def execute(self, request_id, check=False):
        project = self.service.project(self.pid)
        return self.service.execute(self.pid, request_id, 'agent-loop', project['fingerprint'], check=check)

    def test_create_is_idempotent_and_conflicts_do_not_create_more_projects(self):
        same = self.service.create_project('create', 'harness-foundations')
        self.assertEqual(same['result']['project']['id'], self.pid)
        self.assertEqual(len(self.service.projects()), 1)
        with self.assertRaisesRegex(AcademyError, 'request-conflict'):
            self.service.create_project('create', 'harness-foundations', 'different')

    @unittest.skipIf(AcademyRuntime.backend() == 'unavailable', '系统隔离器未安装')
    def test_save_and_execute_use_saved_input_and_one_durable_receipt(self):
        original = self.service.source(self.pid, 'tools.py')
        for check in (False, True):
            project = self.service.project(self.pid)
            source = self.service.source(self.pid, 'tools.py')
            edited = {'path': source['path'], 'content': original['content'] + ('\n# 核对\n' if check else '\n# 运行\n'),
                      'expected_version': source['version']}
            request = 'save-check' if check else 'save-run'
            receipt = self.service.execute(self.pid, request, 'agent-loop', project['fingerprint'], check=check, source=edited)
            self.assertEqual(receipt['status'], 'ready', receipt)
            result = receipt['result']
            self.assertTrue(result['passed'], receipt)
            self.assertEqual(result['source']['content'], edited['content'])
            self.assertEqual(result['run']['input_fingerprint'], result['project']['fingerprint'])
            self.assertNotEqual(result['run']['input_fingerprint'], project['fingerprint'])
            self.assertEqual(self.service.execute(self.pid, request, 'agent-loop', project['fingerprint'], check=check, source=edited), receipt)
            reopened = AcademyService(self.root / 'home', catalog_root=self.library)
            self.assertEqual(reopened.receipt(request), receipt)
            with self.assertRaisesRegex(AcademyError, 'request-conflict'):
                self.service.execute(self.pid, request, 'agent-loop', project['fingerprint'], check=check,
                                     source={**edited, 'content': 'different'})
        self.assertEqual(len(self.service.runs(self.pid)), 2)
        self.assertEqual(self.service.project(self.pid)['progress'][0]['state'], 'passed')

    def test_save_and_execute_rejects_stale_or_invalid_source_before_running(self):
        source = self.service.source(self.pid, 'tools.py')
        for index, edited in enumerate([
            {'path': 'tools.py', 'content': 'replacement', 'expected_version': '0' * 64},
            {'path': '../outside.py', 'content': 'replacement', 'expected_version': source['version']},
            {'path': 'missing.py', 'content': 'replacement', 'expected_version': source['version']},
        ]):
            with patch.object(AcademyRuntime, 'run') as run:
                receipt = self.service.execute(self.pid, 'rejected-' + str(index), 'agent-loop', self.project['fingerprint'], source=edited)
                self.assertEqual(receipt['status'], 'failed')
                run.assert_not_called()
            self.assertEqual(self.service.source(self.pid, 'tools.py'), source)
        for invalid in [True, {}, {'path': 'tools.py', 'content': False, 'expected_version': source['version']}]:
            with self.assertRaisesRegex(AcademyError, 'invalid-request'):
                self.service.execute(self.pid, 'invalid-source', 'agent-loop', self.project['fingerprint'], source=invalid)
        self.assertEqual(self.service.runs(self.pid), [])

    def test_save_then_execution_failure_stays_unknown_without_replay(self):
        source = self.service.source(self.pid, 'tools.py')
        edited = {'path': source['path'], 'content': source['content'] + '\n# 保留已接受的保存\n', 'expected_version': source['version']}
        with patch.object(AcademyRuntime, 'run', side_effect=AcademyError('sandbox-unavailable')) as run:
            with self.assertRaisesRegex(RuntimeError, 'outcome-unknown'):
                self.service.execute(self.pid, 'saved-unknown', 'agent-loop', self.project['fingerprint'], source=edited)
            self.assertEqual(self.service.receipt('saved-unknown')['status'], 'unknown')
            with self.assertRaisesRegex(AcademyError, 'outcome-unknown'):
                self.service.execute(self.pid, 'saved-unknown', 'agent-loop', self.project['fingerprint'], source=edited)
            self.assertEqual(run.call_count, 1)
        self.assertEqual(self.service.source(self.pid, 'tools.py')['content'], edited['content'])
        self.assertEqual(self.service.project(self.pid)['progress'][0]['state'], 'in_progress')

    def test_output_reader_failure_preserves_unknown_receipt_and_never_replays(self):
        """监督器的原读取与清理异常不能落成成功证据，也不能重跑已受理执行。"""
        originals = (OSError('原始输出读取失败'),
                     ExceptionGroup('读取及清理失败', [OSError('stdout 读取失败'), RuntimeError('收尾失败')]))
        source = self.service.source(self.pid, 'tools.py')
        for index, original in enumerate(originals):
            request_id = f'reader-unknown-{index}'
            def failed(command, **options):
                options['on_started']()
                raise original
            with patch.object(AcademyRuntime, 'run', side_effect=failed) as run:
                with self.assertRaises(type(original)) as caught:
                    self.execute(request_id)
                self.assertIs(caught.exception, original)
                self.assertEqual(self.service.receipt(request_id)['status'], 'unknown')
                reopened = AcademyService(self.root / 'home', catalog_root=self.library)
                self.assertEqual(reopened.receipt(request_id)['status'], 'unknown')
                with self.assertRaisesRegex(AcademyError, 'outcome-unknown'):
                    self.execute(request_id)
                self.assertEqual(run.call_count, 1)
            self.assertEqual(self.service.runs(self.pid), [])
            self.assertEqual(self.service.source(self.pid, 'tools.py'), source)
            self.assertEqual(self.service.project(self.pid)['progress'][0]['state'], 'in_progress')

    @unittest.skipIf(AcademyRuntime.backend() == 'unavailable', '系统隔离器未安装')
    def test_break_check_restore_and_reopen_keep_evidence(self):
        good = self.execute('baseline', True)
        self.assertTrue(good['result']['passed'], good)
        project = self.service.project(self.pid)
        checkpoint = project['progress'][0]['checkpoint_id']
        source = self.service.source(self.pid, 'tools.py')
        written = self.service.write_file(self.pid, 'break', 'tools.py',
            source['content'].replace('return a + b', 'return a - b'), source['version'], project['fingerprint'])
        self.assertEqual(written['status'], 'ready')
        self.assertEqual(written['result']['project']['progress'][0]['state'], 'in_progress')
        self.assertEqual(written['result']['project']['progress'][0]['evidence_ids'], [])
        # 相同请求的再次回查先命中回执，不能因文件版本已变而误判。
        replay = self.service.write_file(self.pid, 'break', 'tools.py',
            source['content'].replace('return a + b', 'return a - b'), source['version'], project['fingerprint'])
        self.assertEqual(replay, written)
        broken = self.execute('broken', True)
        self.assertFalse(broken['result']['passed'])
        self.assertIn('AssertionError', broken['result']['run']['result']['stderr'])
        preview = self.service.restore_preview(self.pid, checkpoint)
        self.assertEqual([item['path'] for item in preview['changes']], ['tools.py'])
        restored = self.service.restore(self.pid, 'restore', checkpoint, preview['fingerprint'])
        self.assertIn('backup_checkpoint_id', restored['result'])
        self.assertEqual(self.service.source(self.pid, 'tools.py')['content'], source['content'])
        self.assertTrue(self.execute('after-restore', True)['result']['passed'])
        reopened = AcademyService(self.root / 'home', catalog_root=self.library)
        self.assertEqual(reopened.receipt('restore'), restored)
        self.assertEqual(len(reopened.runs(self.pid)), 3)

    def test_paths_versions_checkpoint_identity_and_empty_file(self):
        source = self.service.source(self.pid, 'tools.py')
        for raw in ['../secret', '/tmp/secret', '.venv/config.py', 'x\\y']:
            with self.subTest(raw=raw), self.assertRaises(AcademyError):
                self.service.source(self.pid, raw)
        path = Path(self.project['root']) / 'link'
        directory_link(path, self.root)
        with self.assertRaises(AcademyError):
            self.service.source(self.pid, 'link/missing')
        path.unlink()
        failed = self.service.write_file(self.pid, 'stale', 'tools.py', '', '0'*64, self.project['fingerprint'])
        self.assertEqual(failed['result']['error_code'], 'stale-file')
        self.assertEqual(self.service.source(self.pid, 'tools.py'), source)
        receipt = self.service.write_file(self.pid, 'empty', 'helper.py', '', 'absent', self.project['fingerprint'])
        self.assertEqual(receipt['status'], 'ready')
        other = self.service.create_project('another', 'harness-foundations')['result']['project']
        checkpoint = other['progress'][0]['checkpoint_id']
        with self.assertRaisesRegex(AcademyError, 'checkpoint-not-found'):
            self.service.restore_preview(self.pid, checkpoint)

    def test_restore_preflights_every_file_before_overwriting_any(self):
        original = self.service.source(self.pid, 'agent.py')
        written = self.service.write_file(self.pid, 'edit-agent', 'agent.py', original['content'] + '\\n# 学习实验\\n',
                                         original['version'], self.project['fingerprint'])
        project = written['result']['project']
        checkpoint = self.project['progress'][0]['checkpoint_id']
        temp = Path(project['root']) / 'tools.py.academy-tmp'
        temp.write_text('keep', encoding='utf-8')
        preview = self.service.restore_preview(self.pid, checkpoint)
        receipt = self.service.restore(self.pid, 'blocked-restore', checkpoint, preview['fingerprint'])
        self.assertEqual(receipt['status'], 'failed')
        self.assertEqual(receipt['result']['error_code'], 'restore-conflict')
        self.assertIn('# 学习实验', self.service.source(self.pid, 'agent.py')['content'])
        self.assertEqual(temp.read_text(encoding='utf-8'), 'keep')

    def test_partial_restore_is_unknown_and_keeps_backup(self):
        source = self.service.source(self.pid, 'agent.py')
        self.service.write_file(self.pid, 'edit-before-restore', 'agent.py', source['content'] + '\\n# 保留实验\\n',
                                source['version'], self.project['fingerprint'])
        checkpoint = self.project['progress'][0]['checkpoint_id']
        preview = self.service.restore_preview(self.pid, checkpoint)
        from os import replace
        calls = []
        def fail_second(source, target):
            calls.append(target)
            if len(calls) == 2:
                raise OSError('fixture: second file write failed')
            replace(source, target)
        with patch('harness.academy.service.os.replace', side_effect=fail_second):
            with self.assertRaisesRegex(RuntimeError, 'outcome-unknown'):
                self.service.restore(self.pid, 'partial', checkpoint, preview['fingerprint'])
        self.assertEqual(self.service.receipt('partial')['status'], 'unknown')
        self.assertTrue(any(item['kind'] == 'before-restore' for item in self.service.checkpoints(self.pid)))
        with self.assertRaisesRegex(AcademyError, 'outcome-unknown'):
            self.service.restore(self.pid, 'partial', checkpoint, preview['fingerprint'])

    def test_changed_inputs_during_check_cannot_pass_current_project(self):
        def changing_run(runtime, command, **options):
            options['on_started']()
            path = Path(self.project['root']) / 'tools.py'
            path.write_text(path.read_text(encoding='utf-8') + '\\n# 运行期间变化\\n', encoding='utf-8')
            return {'status': 'passed', 'exit_code': 0, 'stdout': 'CHECK PASS', 'stderr': '',
                    'truncated': False, 'duration_ms': 1, 'sandbox': 'fixture',
                    'memory_limited': False, 'peak_memory_kib': 0}
        with patch.object(AcademyRuntime, 'run', changing_run):
            receipt = self.execute('changing-check', True)
        self.assertFalse(receipt['result']['passed'])
        self.assertTrue(receipt['result']['run']['input_changed'])
        progress = self.service.project(self.pid)['progress'][0]
        self.assertEqual(progress['state'], 'in_progress')
        self.assertEqual(progress['evidence_ids'], [])

    def test_unknown_and_corrupt_receipts_never_replay(self):
        self.service.store.begin('lost', 'create', 'harness-foundations', 'fingerprint')
        with self.assertRaisesRegex(AcademyError, 'outcome-unknown'):
            self.service.store.begin('lost', 'create', 'harness-foundations', 'fingerprint')
        self.assertEqual(self.service.receipt('lost')['status'], 'unknown')
        with closing(sqlite3.connect(self.service.store.path)) as db, db:
            db.execute("UPDATE calls SET body='null' WHERE id='create'")
        with self.assertRaisesRegex(AcademyError, 'store-invalid'):
            self.service.create_project('create', 'harness-foundations')
        self.assertEqual(len(self.service.projects()), 1)

    def test_course_package_is_generic_without_code_changes(self):
        library = self.service.catalog.additional_root
        custom = library / 'logic-lab'
        shutil.copytree(self.library / 'harness-foundations', custom)
        manifest = json.loads((custom / 'course.json').read_text(encoding='utf-8'))
        manifest['id'] = 'logic-lab'
        lesson = manifest['lessons'][0]
        lesson['id'] = 'input_validation'
        lesson['run_command'] = ['python', 'exercise.py']
        lesson['source_manifest'][0]['path'] = 'exercise.py'
        starter = custom / lesson['starter']
        (starter / 'agent.py').rename(starter / 'exercise.py')
        checker = custom / lesson['check']
        checker.write_text(checker.read_text(encoding='utf-8').replace('from agent import run', 'from exercise import run'), encoding='utf-8')
        (custom / 'course.json').write_text(json.dumps(manifest), encoding='utf-8')
        # 第二个课程使用另一身份、另一课号及入口，框架不作任何变更。
        self.assertEqual({course['id'] for course in self.service.courses()}, {'harness-foundations', 'logic-lab'})
        project = self.service.create_project('logic', 'logic-lab')['result']['project']
        self.assertEqual(project['active_lesson_id'], 'input_validation')
        self.assertIn('exercise.py', [item['path'] for item in self.service.files(project['id'])])
        if AcademyRuntime.backend() != 'unavailable':
            checked = self.service.execute(project['id'], 'logic-check', 'input_validation', project['fingerprint'], check=True)
            self.assertTrue(checked['result']['passed'], checked)
        for language, expected in [('en', 'Harness development foundations'), ('zh-CN', 'Harness 开发入门'), ('en', 'Harness development foundations')]:
            token = current_language.set(language)
            try:
                self.assertEqual(self.service.course('logic-lab')['title'], expected)
            finally:
                current_language.reset(token)
        # 教学内容变更不会迁移已有项目，也不把旧测试当作新版的证据。
        (starter / 'exercise.py').write_text('print("changed")\n', encoding='utf-8')
        receipt = self.service.execute(project['id'], 'updated-course', 'input_validation', project['fingerprint'])
        self.assertEqual(receipt['result']['error_code'], 'course-changed')
        self.assertIn('model_fixture', self.service.source(project['id'], 'exercise.py')['content'])

    @unittest.skipIf(AcademyRuntime.backend() == 'unavailable', '系统隔离器未安装')
    def test_os_sandbox_rejects_outside_files_network_and_inherited_secrets(self):
        root = Path(self.project['root'])
        outside = self.root / 'private.txt'
        outside.write_text('secret', encoding='utf-8')
        script = f'''import json, os, socket
from pathlib import Path
from contextlib import closing
checks = {{}}
for name, action in [
    ("read", lambda: Path({str(outside)!r}).read_text()),
    ("write", lambda: Path({str(outside)!r}).write_text("overwrite")),
    ("network", lambda: socket.create_connection(("127.0.0.1", 9), timeout=0.2)),
]:
    try:
        action()
        checks[name] = False
    except OSError:
        checks[name] = True
checks["secret"] = "ACADEMY_TEST_SECRET" not in os.environ
print(json.dumps(checks))
'''
        (root / 'probe.py').write_text(script, encoding='utf-8')
        with patch.dict('os.environ', {'ACADEMY_TEST_SECRET': 'do-not-forward'}):
            result = AcademyRuntime(root).run(['python', 'probe.py'])
        self.assertEqual(result['status'], 'passed', result)
        self.assertEqual(json.loads(result['stdout']), {'read': True, 'write': True, 'network': True, 'secret': True})
        self.assertEqual(outside.read_text(encoding='utf-8'), 'secret')

    @unittest.skipIf(AcademyRuntime.backend() == 'unavailable', '系统隔离器未安装')
    def test_timeout_and_output_are_bounded(self):
        root = Path(self.project['root'])
        (root / 'flood.py').write_text('print("x"*100000)\n', encoding='utf-8')
        result = AcademyRuntime(root, output_limit=1024).run(['python', 'flood.py'])
        self.assertEqual(result['status'], 'passed', result)
        self.assertTrue(result['truncated'], result)
        self.assertEqual(len(result['stdout']), 1024)
        (root / 'loop.py').write_text('while True: pass\n', encoding='utf-8')
        result = AcademyRuntime(root, timeout=0.2).run(['python', 'loop.py'])
        self.assertEqual(result['status'], 'timeout', result)
        with patch.object(AcademyRuntime, 'backend', return_value='unavailable'):
            with self.assertRaisesRegex(AcademyError, 'sandbox-unavailable'):
                AcademyRuntime(root).run(['python', 'loop.py'])


class AcademyApiTests(unittest.TestCase):
    def test_route_authorization_and_validation_precede_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            class Config:
                home = directory
            class Harness:
                config = Config()
            class Context:
                harness = Harness()
            router = build_router(Context())
            handler, params = router.resolve('POST', '/api/v1/academy/projects')
            for address, subject in [('203.0.113.1', None), ('127.0.0.1', 'guest')]:
                request = Request('POST', '/api/v1/academy/projects', {}, {}, b'{}', client_ip=address, subject=subject)
                with self.assertRaises(ApiError) as caught:
                    handler(request, **params)
                self.assertEqual(caught.exception.status, 403)
            request = Request('POST', '/api/v1/academy/projects', {}, {}, b'{"course_id":"harness-foundations"}', client_ip='127.0.0.1')
            with self.assertRaises(ApiError) as caught:
                handler(request, **params)
            self.assertEqual(caught.exception.code, 'academy/invalid-request')
            self.assertFalse((Path(directory) / 'academy').exists())


if __name__ == '__main__':
    unittest.main()
