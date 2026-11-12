"""显式案例按原调用核对成员读取范围，不把缺失或歧义证据判通过。"""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError
from urllib.parse import parse_qs, urlsplit

from scripts.evaluate_research_team import audit_member_reads, main, member_history


class ResearchToolAuditTests(unittest.TestCase):
    def setUp(self):
        self.manifest = {'session_id': 'parent', 'run_id': 'root-run', 'workspace': '/workspace'}
        self.agent = {'agent_id': 'member', 'child_session_id': 'child', 'status': 'completed',
                      'parent_session_id': 'parent', 'parent_agent_id': 'root-run'}
        self.rows = [{'id': 'tool:1', 'kind': 'tool', 'call_id': 'call', 'name': 'read', 'run_id': 'member', 'status': 'done'}]
        self.detail = {'text': json.dumps({'path': './summary.md'}), 'offset': 0, 'next_offset': None}
        self.pages = 0

    def call(self, path):
        query = parse_qs(urlsplit(path).query)
        self.assertEqual(query['agent_session'], ['child'])
        if 'entry' in query:
            return self.detail
        self.pages += 1
        return {'entries': self.rows, 'session_id': 'child', 'context_missing': False, 'has_more': False}

    def audit(self, agents=None):
        return audit_member_reads(self.manifest, [self.agent] if agents is None else agents, ['summary.md'], self.call)

    def test_requested_read_paths_accept_relative_or_absolute(self):
        for path in ('summary.md', './summary.md', '/workspace/summary.md'):
            self.detail['text'] = json.dumps({'path': path})
            self.assertEqual(self.audit()['violations'], [])
        self.assertEqual(self.audit()['checked_calls'], 1)

    def test_completed_member_without_a_read_cannot_pass(self):
        self.rows = []
        result = self.audit()
        self.assertEqual(result['checked_calls'], 0)
        self.assertIn('missing_successful_read', [row['code'] for row in result['violations']])

    def test_failed_unknown_or_foreign_reads_do_not_prove_source_access(self):
        for change in ({'status': 'error'}, {'status': 'running'}, {'status': 'unknown'},
                       {'status': None}, {'run_id': 'old'}):
            with self.subTest(change=change):
                original = self.rows
                self.rows = [{**self.rows[0], **change}]
                self.assertIn('missing_successful_read', [row['code'] for row in self.audit()['violations']])
                self.rows = original

    def test_one_members_read_does_not_cover_another_completed_member(self):
        other = {**self.agent, 'agent_id': 'other', 'child_session_id': 'other-context'}
        def call(path):
            query = parse_qs(urlsplit(path).query)
            if query['agent_session'] == ['other-context']:
                return {'session_id': 'other-context', 'entries': [], 'has_more': False}
            return self.call(path)
        result = audit_member_reads(self.manifest, [self.agent, other], ['summary.md'], call)
        self.assertIn({'agent_id': 'other', 'call_id': None, 'code': 'missing_successful_read'}, result['violations'])
        self.assertEqual(result['successful_reads'], [{'agent_id': 'member', 'call_id': 'call', 'path': 'summary.md'}])

    def test_later_successful_read_can_satisfy_access_after_failed_allowed_read(self):
        self.rows = [{**self.rows[0], 'status': 'error'}, {**self.rows[0], 'id': 'tool:2', 'call_id': 'recovery'}]
        result = self.audit()
        self.assertEqual(result['violations'], [])
        self.assertEqual(result['successful_reads'], [{'agent_id': 'member', 'call_id': 'recovery', 'path': 'summary.md'}])

    def test_disallowed_tools_and_paths_fail_even_for_failed_attempts(self):
        self.rows[0].update(name='member_evidence', status='error')
        self.assertEqual(self.audit()['violations'][0]['code'], 'disallowed_tool')
        self.rows[0]['name'] = 'read'
        for path in ('run.json', '../summary.md', '/other/summary.md'):
            self.detail['text'] = json.dumps({'path': path})
            self.assertEqual(self.audit()['violations'][0]['code'], 'disallowed_path')

    def test_missing_or_ambiguous_run_cannot_pass(self):
        self.rows[0].pop('run_id')
        self.assertEqual(self.audit()['violations'][0]['code'], 'unverified_run')
        self.rows[0].update(run_id='member', run_attribution_ambiguous=True)
        self.assertEqual(self.audit()['violations'][0]['code'], 'unverified_run')

    def test_other_runs_are_not_relabelled_as_current(self):
        self.rows[0].update(run_id='old', name='execute')
        result = self.audit()
        self.assertEqual(result['checked_calls'], 0)
        self.assertEqual(result['violations'], [{'agent_id': 'member', 'call_id': None, 'code': 'missing_successful_read'}])
        other = {**self.agent, 'parent_agent_id': 'old-parent'}
        self.assertEqual(self.audit([other])['checked_calls'], 0)
        self.assertEqual(self.pages, 1)

    def test_unsettled_and_missing_context_fail(self):
        self.assertEqual(self.audit([{**self.agent, 'status': 'running'}])['violations'][0]['code'], 'unsettled_member')
        self.assertEqual(self.audit([{**self.agent, 'child_session_id': None}])['violations'][0]['code'], 'missing_context')
        def missing(_path):
            return {'session_id': 'child', 'context_missing': True}
        result = audit_member_reads(self.manifest, [self.agent], ['summary.md'], missing)
        self.assertEqual(result['violations'][0]['code'], 'missing_context')

    def test_truncated_missing_or_malformed_input_cannot_pass(self):
        for detail in ({'text': '{}', 'offset': 0, 'next_offset': None},
                       {'text': '{', 'offset': 0, 'next_offset': None},
                       {**self.detail, 'next_offset': 24000},
                       {**self.detail, 'offset': 1}):
            self.detail = detail
            self.assertEqual(self.audit()['violations'][0]['code'], 'unverified_input')

    def test_trace_pagination_checks_later_calls_and_rejects_stalled_cursor(self):
        def call(path):
            query = parse_qs(urlsplit(path).query)
            return {'session_id': 'child', 'entries': [] if 'after' not in query else [
                {'kind': 'tool', 'call_id': 'late', 'run_id': 'member', 'name': 'execute'}],
                'has_more': 'after' not in query, 'next_after': 'cursor'}
        result = audit_member_reads(self.manifest, [self.agent], ['summary.md'], call)
        self.assertEqual(result['violations'][0]['call_id'], 'late')
        def stalled(_path):
            return {'session_id': 'child', 'entries': [], 'has_more': True, 'next_after': 'same'}
        result = audit_member_reads(self.manifest, [self.agent], ['summary.md'], stalled)
        self.assertEqual(result['violations'][0]['code'], 'unverified_pagination')

    def test_member_history_consumes_pages_and_rejects_stalled_cursor(self):
        with patch('scripts.evaluate_research_team.request', side_effect=[
            {'agents': [{'agent_id': 'first'}], 'has_more': True, 'next_before': 'cursor'},
            {'agents': [{'agent_id': 'last'}], 'has_more': False}]):
            self.assertEqual([a['agent_id'] for a in member_history('base', 'parent')], ['first', 'last'])
        with patch('scripts.evaluate_research_team.request', return_value={
            'agents': [], 'has_more': True, 'next_before': 'same'}):
            with self.assertRaises(ValueError):
                member_history('base', 'parent')

    def test_network_failure_is_unverified_not_an_empty_passing_trace(self):
        def unavailable(_path):
            raise URLError('连接中断')
        result = audit_member_reads(self.manifest, [self.agent], ['summary.md'], unavailable)
        self.assertEqual(result['violations'][0]['code'], 'unavailable_trace')

    def test_collect_fails_for_missing_reads_or_extra_tools_even_when_claims_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case, workspace = root / 'case', root / 'workspace'
            case.mkdir()
            workspace.mkdir()
            (case / 'task.json').write_text(json.dumps({'member_read_paths': ['facts.md']}))
            (case / 'expected.json').write_text(json.dumps({'C1': {'verdict': 'supported', 'source': 'facts.md'}}))
            source = '固定事实'.encode('utf-8')
            (case / 'facts.md').write_bytes(source)
            (workspace / 'facts.md').write_bytes(source)
            manifest = {**self.manifest, 'workspace': str(workspace), 'base_url': 'http://127.0.0.1:5273',
                'case_hashes': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in case.glob('*.json')},
                'source_hashes': {'facts.md': hashlib.sha256(source).hexdigest()}}
            path = root / 'evaluation.json'
            path.write_text(json.dumps(manifest))
            report = {'claims': [{'id': 'C1', 'verdict': 'supported',
                                  'sources': [{'path': 'facts.md', 'quote': '固定事实'}]}]}
            run = {'status': 'completed', 'result': {'text': '```json\n' + json.dumps(report) + '\n```'}}
            for rows, violation in (([{**self.rows[0], 'name': 'memory_recall'}], 'disallowed_tool'),
                                    ([], 'missing_successful_read')):
                with self.subTest(violation=violation):
                    trace = {'session_id': 'child', 'entries': rows, 'has_more': False}
                    with patch('sys.argv', ['evaluate', '--collect', str(path), '--case', str(case)]), \
                         patch('scripts.evaluate_research_team.request', side_effect=[run, {'events': []}, {'agents': [self.agent]}, trace]), \
                         contextlib.redirect_stdout(io.StringIO()):
                        code = main()
                    result = json.loads((workspace / 'result.json').read_text(encoding='utf-8'))
                    self.assertEqual(code, 1)
                    self.assertEqual(result['score']['correct'], ['C1'])
                    self.assertFalse(result['score']['passed'])
                    self.assertEqual(result['tool_audit']['violations'][0]['code'], violation)
