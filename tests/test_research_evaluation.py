"""固定资料评测拒绝错判、虚构引句、重复主张与伪造成员来源。"""
import copy
import json
import unittest

from scripts.evaluate_research_team import FIXTURE, completed_member_sources, score_report


class ResearchEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.expected = json.loads((FIXTURE / 'expected.json').read_text(encoding='utf-8'))
        self.corpus = {p.name: p.read_text(encoding='utf-8') for p in FIXTURE.glob('*.md')}
        self.required = ('research', 'crosscheck', 'challenge')
        self.members = {(name, 'run-' + name) for name in ('research', 'crosscheck', 'challenge')}
        self.report = {'claims': [{'id': name, 'verdict': row['verdict'], 'sources': [{
            'path': row['source'], 'quote': self.corpus[row['source']]}]} for name, row in self.expected.items()],
            'member_sources': [{'member': name, 'agent_id': run} for name, run in self.members]}

    def score(self, report=None):
        return score_report(self.report if report is None else report, self.expected, self.corpus, self.members, required_members=self.required)

    def test_complete_matching_report_passes_limited_checks(self):
        result = self.score()
        self.assertTrue(result['passed'])
        self.assertEqual(len(result['correct']), 6)
        self.assertEqual(len(result['traced']), 6)

    def test_wrong_verdict_fails_even_with_valid_quote(self):
        self.report['claims'][0]['verdict'] = 'supported'
        self.assertFalse(self.score()['passed'])

    def test_invented_quote_and_unknown_file_fail(self):
        for source in ({'path': 'cmd.md', 'quote': '此引句并未出现在资料中'},
                       {'path': '../expected.json', 'quote': 'contradicted'}):
            with self.subTest(source=source):
                report = copy.deepcopy(self.report)
                report['claims'][0]['sources'] = [source]
                self.assertFalse(score_report(report, self.expected, self.corpus, self.members, required_members=self.required)['passed'])

    def test_missing_and_duplicate_claims_fail(self):
        self.assertFalse(self.score({**self.report, 'claims': self.report['claims'][:-1]})['passed'])
        self.report['claims'].append(self.report['claims'][0])
        self.assertFalse(self.score()['passed'])

    def test_definition_name_cannot_replace_runtime_id(self):
        self.report['member_sources'][0]['agent_id'] = 'evidence-review-researcher'
        self.assertFalse(self.score()['passed'])

    def test_malformed_model_output_fails_without_crashing(self):
        for report in (None, {}, {'claims': [{'id': []}]},
                       {**self.report, 'member_sources': [{'member': [], 'agent_id': []}]}):
            with self.subTest(report=report):
                self.assertFalse(score_report(report, self.expected, self.corpus, self.members, required_members=self.required)['passed'])

    def test_roster_identity_requires_successful_current_direct_run(self):
        events = [{'type': 'agent/team', 'data': {'members': [{'name': 'research', 'agent_id': 'child'}]}}]
        row = {'agent_id': 'child', 'status': 'completed', 'error': None,
               'parent_session_id': 'session', 'parent_agent_id': 'run'}
        self.assertEqual(completed_member_sources(events, [row], 'session', 'run'), {('research', 'child')})
        for change in ({'status': 'running'}, {'status': 'failed'}, {'error': 'failed'},
                       {'parent_session_id': 'other'}, {'parent_agent_id': 'old'}, {'agent_id': 'other'}):
            with self.subTest(change=change):
                self.assertEqual(completed_member_sources(events, [{**row, **change}], 'session', 'run'), set())
        self.assertEqual(completed_member_sources(events, [], 'session', 'run'), set())
