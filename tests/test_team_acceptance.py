"""团队验收必须核对真实工具状态，不能由成员声明降低证据要求。"""
from types import SimpleNamespace
import unittest

from harness.collaboration.acceptance import evaluate_report, acceptance_example
from harness.core.language import current_language
import json


class TeamAcceptanceTests(unittest.TestCase):
    def verdict(self, *, name='bash', kind='observation', display=None, turn=3, error=False):
        event = SimpleNamespace(seq=12, data={'call_id': 'actual-call', 'turn': turn,
            'name': name, 'results': [{'is_error': error}], 'display': display or {}})
        session = SimpleNamespace(current_turn=3, events_of_type=lambda _: [event])
        report = {'status': 'ok', 'checks': [{'name': '业务检查', 'passed': True,
            'detail': '实际观察', 'kind': kind, 'tool_call_id': 'actual-call'}],
            'evidence': ['工具记录']}
        return evaluate_report(report, True, session=session)

    def test_background_command_cannot_pass_as_observation(self):
        self.assertEqual(self.verdict(display={'exit_code': 0, 'background': True})['reason'], 'command_not_completed')

    def test_running_job_output_is_not_completion_evidence(self):
        self.assertEqual(self.verdict(name='job_output', display={'job': {
            'kind': 'shell', 'status': 'running'}})['status'], 'unverified')

    def test_completed_job_output_can_be_command_evidence(self):
        self.assertEqual(self.verdict(name='job_output', kind='command', display={'job': {
            'kind': 'shell', 'status': 'completed', 'exitCode': 0}})['status'], 'passed')

    def test_failed_job_is_failed_evidence(self):
        self.assertEqual(self.verdict(name='job_output', display={'job': {
            'kind': 'shell', 'status': 'completed', 'exitCode': 2}})['status'], 'failed')

    def test_terminal_wait_and_archived_jobs_are_not_completion(self):
        for job in ({'kind': 'pty-send'}, {'completionScope': 'terminal-wait'}, {'archived': True}):
            with self.subTest(job=job):
                self.assertEqual(self.verdict(name='job_output', display={'job': {
                    'status': 'completed', 'exitCode': 0, **job}})['status'], 'unverified')
        self.assertEqual(self.verdict(name='terminal_send')['status'], 'unverified')

    def test_command_without_exit_code_cannot_pass_as_observation(self):
        self.assertEqual(self.verdict()['reason'], 'command_not_completed')

    def test_completed_command_can_support_observation(self):
        self.assertEqual(self.verdict(display={'exit_code': 0})['status'], 'passed')

    def test_read_observation_does_not_require_exit_code(self):
        self.assertEqual(self.verdict(name='read')['status'], 'passed')

    def test_read_is_not_command_evidence(self):
        self.assertEqual(self.verdict(name='read', kind='command')['status'], 'unverified')

    def test_failed_and_stale_tools_do_not_pass(self):
        self.assertEqual(self.verdict(display={'exit_code': 1})['status'], 'failed')
        self.assertEqual(self.verdict(error=True)['status'], 'failed')
        self.assertEqual(self.verdict(turn=2)['status'], 'unverified')

    def test_reused_current_turn_call_id_cannot_replace_failed_evidence(self):
        report = {'status': 'ok', 'checks': [{'name': '业务检查', 'passed': True,
            'detail': '实际观察', 'kind': 'observation', 'tool_call_id': 'duplicate'}], 'evidence': ['工具记录']}
        for errors in ((True, False), (False, True), (False, False)):
            events = [SimpleNamespace(seq=index, data={'call_id': 'duplicate', 'turn': 3,
                'name': 'read', 'results': [{'is_error': error}]}) for index, error in enumerate(errors)]
            session = SimpleNamespace(current_turn=3, events_of_type=lambda _: events)
            with self.subTest(errors=errors):
                verdict = evaluate_report(report, True, session=session)
                self.assertEqual(verdict['status'], 'unverified')
                self.assertEqual(verdict['reason'], 'ambiguous_tool_evidence')
                self.assertEqual(verdict['verified_tool_calls'], [])

    def test_job_completion_requires_exact_zero_integer_exit_and_completed_status(self):
        for job in ({'status': 'failed', 'exitCode': 0}, {'status': 'killed', 'exitCode': 0}):
            with self.subTest(job=job):
                self.assertEqual(self.verdict(name='job_output', display={'job': job})['status'], 'failed')
        for job in ({'status': 'running', 'exitCode': 0}, {'status': 'completed'},
                    {'status': 'completed', 'exitCode': False}, {'status': 'completed', 'exitCode': '0'}):
            with self.subTest(job=job):
                self.assertEqual(self.verdict(name='job_output', display={'job': job})['status'], 'unverified')

    def test_persisted_session_duplicate_results_remain_unverified(self):
        import asyncio
        import tempfile
        from pathlib import Path
        from harness.session.store import SessionStore
        from harness.session.events import AppendOp

        async def verify(root):
            session = SessionStore(root).create()
            for error in (True, False):
                await session.append('tool/result', {'call_id': 'duplicate', 'turn': session.current_turn,
                    'name': 'read', 'results': [{'tool_use_id': 'duplicate', 'is_error': error,
                                               'content': '失败' if error else '成功'}]}, surface_op=AppendOp())
            report = {'status': 'ok', 'checks': [{'name': '业务检查', 'passed': True,
                'detail': '实际观察', 'kind': 'observation', 'tool_call_id': 'duplicate'}], 'evidence': ['工具记录']}
            self.assertEqual(evaluate_report(report, True, session=session)['reason'], 'ambiguous_tool_evidence')
            self.assertEqual(len(session.events_of_type('tool/result')), 2)

        with tempfile.TemporaryDirectory() as directory:
            asyncio.run(verify(Path(directory)))

    def test_unreferenced_duplicate_and_prior_turn_do_not_invalidate_unique_evidence(self):
        report = {'status': 'ok', 'checks': [{'name': '唯一检查', 'passed': True,
            'detail': '原始记录', 'kind': 'observation', 'tool_call_id': 'unique'}], 'evidence': ['工具记录']}
        events = [SimpleNamespace(seq=index, data={'call_id': identifier, 'turn': turn,
            'name': 'read', 'results': [{'is_error': False}]}) for index, (identifier, turn) in enumerate(
                [('unique', 2), ('duplicate', 3), ('duplicate', 3), ('unique', 3)])]
        session = SimpleNamespace(current_turn=3, events_of_type=lambda _: events)
        result = evaluate_report(report, True, session=session)
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(result['verified_tool_calls'][0]['result_seq'], 3)

    def test_acceptance_example_resolves_language_without_translating_protocol(self):
        for language in ('en', 'zh-CN', 'en'):
            with self.subTest(language=language), current_language.set(language):
                example = json.loads(acceptance_example())
                self.assertEqual(example['status'], 'ok | failed')
                self.assertEqual(example['checks'][0]['kind'], 'command | observation')
                self.assertIs(example['checks'][0]['passed'], True)
                self.assertEqual(any('\u4e00' <= char <= '\u9fff' for char in example['checks'][0]['detail']), language == 'zh-CN')
