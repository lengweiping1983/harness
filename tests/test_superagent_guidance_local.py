"""本机引导的完整原子输入、稳定请求及无模型 CLI 消费回归。"""
import io
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from harness.app.superagent_guidance import command_id
from harness.app.superagent_local import read_guidance_receipt, steer_local
from harness.cli import build_parser, main
from harness.core.errors import HarnessError
from harness.core.language import current_language
from harness.i18n import tr
from tests import test_superagent_local as local_fixture
from tests.filesystem_links import directory_link


class GuidanceLocalTests(unittest.TestCase):
    save = local_fixture.SuperagentLocalTests.save

    def setUp(self):
        local_fixture.SuperagentLocalTests.setUp(self)
        self.spec['autonomy'] = {'max_steps': 8, 'max_attempts': 3, 'max_missions': 16,
            'integrator_profile': 'inspect', 'reviewer_profile': 'inspect'}
        self.save()
        self.job = {'id': 'mission/work:first:1', 'role': 'work', 'state': 'accepted', 'run_id': 'original',
                    'run': {'id': 'original', 'status': 'running', 'turn': 1}}
        self.mission = {'id': 'mission', 'source_id': 'local', 'revision': 1, 'thread_ids': ['project'], 'jobs': [self.job]}
        self.status = {'instance_id': 'local-instance', 'runtime_state': 'running',
                       'missions': {'recent': [self.mission], 'has_more': False}}
        self.status_path = self.root / 'state' / 'status.json'
        self.status_path.parent.mkdir()
        self.write_status()

    def write_status(self):
        self.status_path.write_text(json.dumps(self.status), encoding='utf-8')

    def test_atomic_instruction_binds_original_job_and_duplicate_survives_later_status(self):
        with patch('harness.app.superagent_service.Harness', side_effect=AssertionError('排队不得构造宿主')):
            first = steer_local(self.path, '原文 /goal {data}', request_id='stable')
            value = json.loads(Path(first['path']).read_text(encoding='utf-8'))
            self.assertEqual((value['job_id'], value['run_id'], value['turn']), (self.job['id'], 'original', 1))
            self.assertEqual(value['text'], '原文 /goal {data}')
            self.assertTrue(first['queued'])
            self.assertFalse(first['duplicate'])
            self.status['runtime_state'] = 'stopped'
            self.status['missions']['recent'] = []
            self.write_status()
            duplicate = steer_local(self.path, '原文 /goal {data}', request_id='stable')
        self.assertTrue(duplicate['duplicate'])
        self.assertEqual(duplicate['run_id'], 'original')
        original = Path(first['path']).read_bytes()
        with self.assertRaises(HarnessError) as caught:
            steer_local(self.path, 'changed', request_id='stable')
        self.assertEqual(caught.exception.code, 'superagent/event-conflict')
        self.assertEqual(Path(first['path']).read_bytes(), original)
        self.assertFalse(list((self.root / 'state').glob('*.sqlite3')))

    def test_parallel_identical_requests_publish_one_complete_instruction(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: steer_local(self.path, 'x' * 65536, request_id='parallel'), range(4)))
        self.assertEqual(sum(not result['duplicate'] for result in results), 1)
        directory = Path(results[0]['path']).parent
        self.assertEqual(len(list(directory.iterdir())), 1)
        self.assertEqual(len(json.loads(next(directory.iterdir()).read_text(encoding='utf-8'))['text']), 65536)

    def test_ambiguous_and_truncated_status_require_exact_job(self):
        self.status['missions']['has_more'] = True
        self.write_status()
        with self.assertRaises(HarnessError):
            steer_local(self.path, 'hint')
        self.assertFalse((self.root / 'state' / 'guidance').exists())
        self.assertTrue(steer_local(self.path, 'hint', job_id=self.job['id'])['queued'])
        self.status['missions']['has_more'] = False
        self.mission['jobs'].append({**self.job, 'id': 'mission/work:second:1'})
        self.write_status()
        with self.assertRaises(HarnessError):
            steer_local(self.path, 'hint')

    def test_plan_review_inactive_and_stale_snapshots_cannot_queue_instructions(self):
        for key, value in (('role', 'plan'), ('role', 'review'), ('state', 'done'), ('scheduling_halted', True)):
            with self.subTest(field=key, value=value):
                old = self.job.get(key)
                self.job[key] = value
                self.write_status()
                with self.assertRaises(HarnessError):
                    steer_local(self.path, 'hint')
                if old is None:
                    self.job.pop(key)
                else:
                    self.job[key] = old
        self.assertFalse((self.root / 'state' / 'guidance').exists())

    def test_parent_directory_link_does_not_create_external_incoming(self):
        outside = self.root / 'outside'
        outside.mkdir()
        directory_link(self.root / 'state' / 'guidance', outside)
        with self.assertRaises(HarnessError):
            steer_local(self.path, 'hint')
        self.assertEqual(list(outside.iterdir()), [])

    def test_cli_and_receipt_reading_keep_model_host_unconstructed(self):
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                actions = {action.dest: action for action in build_parser()._actions}
                self.assertEqual(actions['superagent_steer'].help, tr('superagent.cli.steer'))
                self.assertEqual(actions['superagent_receipt'].help, tr('superagent.cli.receipt'))
        with patch('harness.app.superagent_service.Harness', side_effect=AssertionError('本机命令不得构造宿主')), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(['--superagent', str(self.path), '--superagent-steer', '原文 instruction', '--superagent-request-id', 'cli']), 0)
        self.assertTrue(json.loads(output.getvalue())['queued'])
        identity = command_id('local-instance', 'cli')
        directory = self.root / 'state' / 'guidance' / 'receipts'
        directory.mkdir()
        receipt = {'version': 1, 'instance_id': 'local-instance', 'id': identity, 'request_id': 'cli',
            'mission_id': 'mission', 'job_id': self.job['id'], 'run_id': 'original', 'turn': 1,
            'state': 'accepted', 'reason': None, 'application': 'applied', 'accepted_seq': 3, 'applied_seq': 5}
        path = directory / (identity + '.json')
        path.write_text(json.dumps(receipt), encoding='utf-8')
        original = path.read_bytes()
        with patch('harness.app.superagent_service.Harness', side_effect=AssertionError('读取不得构造宿主')), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(['--superagent', str(self.path), '--superagent-receipt', 'cli']), 0)
        self.assertEqual(json.loads(output.getvalue()), receipt)
        self.assertEqual(path.read_bytes(), original)
        path.write_text(json.dumps({**receipt, 'turn': True}), encoding='utf-8')
        corrupt = path.read_bytes()
        with self.assertRaises(HarnessError):
            read_guidance_receipt(self.path, 'cli')
        self.assertEqual(path.read_bytes(), corrupt)

    def test_cli_rejects_conflicting_modes_before_touching_guidance(self):
        cases = [['--superagent-steer', 'hint'], ['--superagent-receipt', 'one'],
                 ['--superagent', str(self.path), '--superagent-job', self.job['id']],
                 ['--superagent', str(self.path), '--superagent-steer', 'hint', '--superagent-once'],
                 ['--superagent', str(self.path), '--superagent-receipt', 'one', '--superagent-status']]
        for args in cases:
            with self.subTest(args=args), redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    main(args)
                self.assertEqual(caught.exception.code, 2)
        self.assertFalse((self.root / 'state' / 'guidance').exists())
