"""可信本机提交的授权、幂等、原子公开及无宿主 CLI 回归。"""
import io
import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from harness.app.superagent_local import read_status, submit_local
from harness.cli import build_parser, main
from harness.core.errors import HarnessError
from harness.core.language import current_language
from harness.i18n import tr
from tests.filesystem_links import directory_link


class SuperagentLocalTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / 'repo').mkdir()
        self.path = self.root / 'config.json'
        self.spec = {'version': 1, 'instance_id': 'local-instance', 'state_dir': 'state', 'language': 'en',
            'router_model': 'router', 'worker_model': 'worker', 'base_url': 'https://fixture.invalid/v1',
            'permission_policy': 'read_only',
            'sources': {'local': {'mode': 'requests', 'actors': ['operator'], 'threads': ['project']}},
            'profiles': {'inspect': {'repo': 'repo', 'description': 'Inspect the project.', 'sources': ['local'], 'tools': ['read']}}}
        self.save()

    def save(self):
        self.path.write_text(json.dumps(self.spec), encoding='utf-8')

    def test_submission_is_atomic_idempotent_and_does_not_construct_hosts(self):
        with patch('harness.app.superagent_service.Harness', side_effect=AssertionError('不应构造宿主')):
            first = submit_local(self.path, '/goal 原文 {data}', request_id='stable')
            second = submit_local(self.path, '/goal 原文 {data}', request_id='stable')
        self.assertFalse(first['duplicate'])
        self.assertTrue(second['duplicate'])
        self.assertEqual(first['event_id'], second['event_id'])
        target = Path(first['path'])
        value = json.loads(target.read_text(encoding='utf-8'))
        self.assertEqual(value['text'], '/goal 原文 {data}')
        self.assertEqual((value['source_id'], value['actor_id'], value['thread_ids']), ('local', 'operator', ['project']))
        self.assertEqual(list(target.parent.iterdir()), [target])
        self.assertFalse(list((self.root / 'state').glob('*.sqlite3')))
        with self.assertRaises(HarnessError) as caught:
            submit_local(self.path, 'different text', request_id='stable')
        self.assertEqual(caught.exception.code, 'superagent/event-conflict')
        self.assertEqual(json.loads(target.read_text(encoding='utf-8')), value)

    def test_concurrent_identical_submissions_publish_exactly_one_complete_file(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: submit_local(self.path, 'a' * 65536, request_id='parallel'), range(4)))
        self.assertEqual(sum(not item['duplicate'] for item in results), 1)
        incoming = self.root / 'state' / 'incoming'
        self.assertEqual(len(list(incoming.iterdir())), 1)
        self.assertEqual(json.loads(next(incoming.iterdir()).read_text(encoding='utf-8'))['text'], 'a' * 65536)

    def test_ambiguous_or_unauthorized_identities_are_rejected_before_creating_state(self):
        self.spec['sources']['local']['actors'].append('other')
        self.save()
        for kwargs in ({}, {'actor_id': 'outside'}, {'actor_id': 'operator', 'thread_id': 'outside'}):
            with self.assertRaises(HarnessError):
                submit_local(self.path, 'Inspect', **kwargs)
        self.assertFalse((self.root / 'state').exists())

    def test_status_is_read_only_and_requires_a_matching_snapshot(self):
        with self.assertRaises(HarnessError) as caught:
            read_status(self.path)
        self.assertEqual(caught.exception.code, 'superagent/status-unavailable')
        self.assertFalse((self.root / 'state').exists())
        directory = self.root / 'state'
        directory.mkdir()
        value = {'instance_id': 'local-instance', 'runtime_state': 'stopped', 'missions': {'states': {'completed': 1}}}
        path = directory / 'status.json'
        path.write_text(json.dumps(value), encoding='utf-8')
        self.assertEqual(read_status(self.path), value)
        original = path.read_bytes()
        with patch('harness.app.superagent_service.Harness', side_effect=AssertionError('不应构造宿主')), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(['--superagent', str(self.path), '--superagent-status']), 0)
        self.assertEqual(json.loads(output.getvalue()), value)
        self.assertEqual(path.read_bytes(), original)
        path.write_text('{"instance_id":"other","runtime_state":"running"}', encoding='utf-8')
        with self.assertRaises(HarnessError):
            read_status(self.path)

    def test_cli_submission_and_help_use_current_language_without_model_credentials(self):
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                actions = {action.dest: action for action in build_parser()._actions}
                self.assertEqual(actions['superagent_submit'].help, tr('superagent.cli.submit'))
                self.assertEqual(actions['superagent_status'].help, tr('superagent.cli.status'))
        with patch('harness.app.superagent_service.Harness', side_effect=AssertionError('不应构造宿主')), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(['--superagent', str(self.path), '--superagent-submit', 'Inspect the project',
                                   '--superagent-request-id', 'cli']), 0)
        self.assertTrue(json.loads(output.getvalue())['queued'])

    def test_cli_submission_can_run_one_batch_without_a_second_command(self):
        from unittest.mock import AsyncMock
        with patch('harness.app.superagent_service.Harness', side_effect=AssertionError('提交阶段不应构造宿主')), \
                patch('harness.superagent_cli.run', new=AsyncMock(return_value=0)), \
                redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(['--superagent', str(self.path), '--superagent-submit', 'Inspect the project',
                                   '--superagent-request-id', 'one-shot', '--superagent-once']), 0)
        self.assertTrue(json.loads(output.getvalue())['queued'])

    def test_status_cannot_start_a_model_run(self):
        from unittest.mock import AsyncMock
        directory = self.root / 'state'
        directory.mkdir()
        (directory / 'status.json').write_text(json.dumps({'instance_id': 'local-instance', 'runtime_state': 'stopped'}), encoding='utf-8')
        with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()), patch('harness.superagent_cli.run', new=AsyncMock()) as run:
            with self.assertRaises(SystemExit) as caught:
                main(['--superagent', str(self.path), '--superagent-status', '--superagent-once'])
        self.assertEqual(caught.exception.code, 2)
        run.assert_not_awaited()

    def test_auxiliary_modes_reject_missing_config_and_conflicting_actions(self):
        cases = [ ['--superagent-submit', 'goal'], ['--superagent-status'], ['--superagent-source', 'local'],
                  ['--superagent', str(self.path), '--superagent-status', '--superagent-once'],
                  ['--superagent', str(self.path), '--superagent-source', 'local'],
                  ['--superagent', str(self.path), '--superagent-submit', 'goal', '--superagent-status'] ]
        for args in cases:
            with self.subTest(args=args), redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    main(args)
                self.assertEqual(caught.exception.code, 2)
        self.assertFalse((self.root / 'state').exists())

    def test_directory_link_cannot_redirect_local_submission(self):
        state = self.root / 'state'
        state.mkdir()
        outside = self.root / 'outside'
        outside.mkdir()
        directory_link(state / 'incoming', outside)
        with self.assertRaises(HarnessError) as caught:
            submit_local(self.path, 'Inspect', request_id='linked')
        self.assertEqual(caught.exception.code, 'superagent/conflict')
        self.assertEqual(list(outside.iterdir()), [])

    def test_missing_platform_nonblock_flag_keeps_duplicate_and_status_readable(self):
        submit_local(self.path, 'Inspect', request_id='platform')
        value = {'instance_id': 'local-instance', 'runtime_state': 'stopped'}
        (self.root / 'state' / 'status.json').write_text(json.dumps(value), encoding='utf-8')
        platform_os = SimpleNamespace(**{name: getattr(os, name) for name in dir(os) if name != 'O_NONBLOCK'})
        with patch('harness.app.superagent_local.os', platform_os):
            self.assertTrue(submit_local(self.path, 'Inspect', request_id='platform')['duplicate'])
            self.assertEqual(read_status(self.path), value)
