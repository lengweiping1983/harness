"""验证记录必须绑定真实前台检查，输入变化使历史通过失效。"""
import tempfile
import hashlib
import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from harness.core.errors import HarnessError
from harness.session.events import SessionEvent
from harness.session.verification import VerificationCapture, verification_summary


class VerificationCaptureTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / 'input.py').write_text('value = 1\n', encoding='utf-8')
        self.events = [SessionEvent('turn/start', 0, 1, {'turn': 1, 'run_id': 'run'})]

        async def append(kind, data):
            event = SessionEvent(kind, len(self.events), len(self.events), data)
            self.events.append(event)
            return event

        self.session = SimpleNamespace(id='session', events=self.events, header=SimpleNamespace(cwd=str(self.root)), append=append,
            events_of_type=lambda kind: [event for event in self.events if event.type == kind])

    def test_argument_fingerprint_keeps_current_serialization(self):
        from harness.core.jsonutil import deep_freeze, to_jsonable
        from harness.session.verification import fingerprint

        for value in ({'z': '中文😀', 'a': [True, None, 1.5]}, deep_freeze({'命令': ['python', '-V']})):
            expected = hashlib.sha256(json.dumps(to_jsonable(value), sort_keys=True, ensure_ascii=False,
                                                separators=(',', ':')).encode('utf-8')).hexdigest()
            self.assertEqual(fingerprint(value), expected)

    def begin(self):
        return VerificationCapture.begin(self.session, turn=1, check_id='unit-tests', tool='bash',
                                         arguments={'command': 'python -m unittest'}, paths=['input.py'])

    def command(self, *, code=0, execution_state='exited', background=False):
        self.events.extend([
            SessionEvent('tool/call', 1, 2, {'turn': 1, 'run_id': 'run', 'name': 'bash', 'call_id': 'call', 'status': 'running', 'input': {'command': 'python -m unittest'}}),
            SessionEvent('tool/result', 2, 3, {'turn': 1, 'run_id': 'run', 'name': 'bash', 'call_id': 'call',
                'results': [{'tool_use_id': 'call', 'is_error': code != 0}],
                'display': {'execution_state': execution_state, 'exit_code': code, 'background': background}})])

    async def test_pass_becomes_stale_after_edit_and_receipt_is_reused(self):
        capture = self.begin()
        self.command()
        first = await capture.complete(self.session, result_seq=2)
        self.assertIs(await capture.complete(self.session, result_seq=2), first)
        self.assertEqual(verification_summary(self.session, self.events)[0], 'passed')
        (self.root / 'input.py').write_text('value = 2\n', encoding='utf-8')
        self.assertEqual(verification_summary(self.session, self.events)[0], 'stale')
        self.assertEqual(first.data['status'], 'passed')

    async def test_check_that_changes_its_input_cannot_pass(self):
        capture = self.begin()
        self.command()
        (self.root / 'input.py').write_text('value = 2\n', encoding='utf-8')
        self.assertEqual((await capture.complete(self.session, result_seq=2)).data['status'], 'stale')

    async def test_unknown_exit_and_failed_exit_stay_distinct(self):
        capture = self.begin()
        self.command(execution_state='running')
        self.assertEqual((await capture.complete(self.session, result_seq=2)).data['status'], 'unknown')

    async def test_mismatched_command_is_rejected(self):
        capture = self.begin()
        self.command()
        self.events[1].data['input']['command'] = 'echo finished'
        with self.assertRaises(HarnessError):
            await capture.complete(self.session, result_seq=2)

    async def test_old_result_cannot_be_registered_after_execution(self):
        self.command()
        capture = self.begin()
        with self.assertRaises(HarnessError):
            await capture.complete(self.session, result_seq=2)

    def test_scope_rejects_research_and_parent_paths(self):
        for path in ['../input.py', 'research/source.py', 'Research/source.py', 'RESEARCH/source.py']:
            with self.subTest(path=path), self.assertRaises(ValueError):
                VerificationCapture.begin(self.session, turn=1, check_id='check', tool='bash', arguments={}, paths=[path])

    async def test_result_cannot_disguise_original_command_arguments(self):
        capture = self.begin()
        self.command()
        self.events[1].data['input'] = {'command': 'echo fake'}
        self.events[2].data['input'] = {'command': 'python -m unittest'}
        with self.assertRaises(HarnessError):
            await capture.complete(self.session, result_seq=2)

    async def test_conflicting_result_call_identity_cannot_verify(self):
        capture = self.begin()
        self.command()
        self.events[2].data['call_id'] = 'another-call'
        with self.assertRaises(HarnessError):
            await capture.complete(self.session, result_seq=2)

    async def test_duplicate_receipts_are_rejected(self):
        capture = self.begin()
        self.command()
        first = await capture.complete(self.session, result_seq=2)
        self.events.append(SessionEvent('note', 4, 5, dict(first.data)))
        with self.assertRaises(HarnessError):
            verification_summary(self.session, self.events)

    def test_directory_links_cannot_expand_verification_scope(self):
        from tests.filesystem_links import directory_link
        with tempfile.TemporaryDirectory() as external:
            target = Path(external)
            (target / 'outside.py').write_text('outside = True', encoding='utf-8')
            directory_link(self.root / 'linked', target)
            with self.assertRaises((ValueError, OSError)):
                VerificationCapture.begin(self.session, turn=1, check_id='check', tool='bash', arguments={}, paths=['linked/outside.py'])

    async def test_real_frozen_session_survives_persistent_reopen(self):
        from harness.session.store import SessionStore
        from harness.session.events import AppendOp
        from harness.web.api.turn_completion import completion_snapshot
        store = SessionStore(self.root / '.state')
        session = store.create(cwd=str(self.root))
        await session.append('turn/start', {'turn': 1, 'run_id': 'actual'})
        capture = VerificationCapture.begin(session, turn=1, check_id='unit', tool='bash',
                                             arguments={'command': 'python -m unittest'}, paths=['input.py'])
        await session.append('tool/call', {'turn': 1, 'run_id': 'actual', 'call_id': 'c', 'name': 'bash',
                                          'status': 'running', 'input': {'command': 'python -m unittest'}})
        result = await session.append('tool/result', {'turn': 1, 'run_id': 'actual', 'call_id': 'c', 'name': 'bash',
            'results': [{'tool_use_id': 'c', 'is_error': False}], 'display': {'execution_state': 'exited', 'exit_code': 0}}, surface_op=AppendOp())
        await capture.complete(session, result_seq=result.seq)
        await session.append('tool/call', {'call_id': 'p', 'name': 'present', 'status': 'running'})
        await session.append('tool/result', {'call_id': 'p', 'name': 'present', 'results': [{'tool_use_id': 'p', 'is_error': False}],
                                            'display': {'files': [{'path': str(self.root / 'input.py')}]}}, surface_op=AppendOp())
        await session.append('turn/end', {'turn': 1, 'run_id': 'actual', 'reason': 'completed'})
        self.assertEqual(completion_snapshot(session, 1)['delivery']['verification'], 'passed')
        reopened = SessionStore(self.root / '.state').get(session.id)
        delivery = completion_snapshot(reopened, 1)['delivery']
        self.assertEqual(delivery['verification'], 'passed')
        self.assertEqual(delivery['artifact_count'], 1)
        (self.root / 'input.py').write_text('value = 2\n', encoding='utf-8')
        self.assertEqual(completion_snapshot(reopened, 1)['delivery']['verification'], 'stale')
