"""检验实验融合不突破符号身份，拒绝错配或损坏的向量报告。"""
import copy
import unittest

from scripts.evaluate_memory_hybrid import evaluate


class HybridEvaluationTests(unittest.TestCase):
    def fixture(self, query='unknown_symbol'):
        data = {'rows': [{'id': 'one', 'topic': '', 'content': '数据库损坏处理', 'evidence': ''}],
                'cases': [{'name': 'case', 'query': query, 'expected': []}]}
        report = {'fixture_sha256': 'fixture', 'model': 'fixed', 'top_k': 5,
                  'results': [{'name': 'case', 'expected': [], 'retrieved': [{'id': 'one', 'score': 0.8}]}]}
        return data, report

    def test_exact_symbols_cannot_be_overridden_by_high_semantic_score(self):
        for query in ('unknown_symbol', 'src/missing.py', 'mem_' + 'a' * 32):
            data, report = self.fixture(query)
            result = evaluate(data, report, 'fixture')
            self.assertEqual(result['results'][0]['retrieved'], [])
            self.assertEqual(result['no_answer_false_positive_rate'], 0)

    def test_natural_question_false_positive_is_reported_instead_of_hidden(self):
        data, report = self.fixture('How long are backups retained?')
        result = evaluate(data, report, 'fixture')
        self.assertEqual(result['no_answer_false_positive_rate'], 1)
        self.assertEqual(result['results'][0]['semantic'], ['one'])

    def test_rejects_mismatched_inputs_invalid_ids_and_nonfinite_scores(self):
        data, original = self.fixture()
        for kind in ('hash', 'name', 'expected', 'id', 'duplicate', 'nan'):
            report = copy.deepcopy(original)
            if kind == 'hash':
                report['fixture_sha256'] = 'other'
            elif kind == 'name':
                report['results'][0]['name'] = 'other'
            elif kind == 'expected':
                report['results'][0]['expected'] = ['one']
            elif kind == 'id':
                report['results'][0]['retrieved'][0]['id'] = 'missing'
            elif kind == 'duplicate':
                report['results'][0]['retrieved'] *= 2
            else:
                report['results'][0]['retrieved'][0]['score'] = float('nan')
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                evaluate(data, report, 'fixture')
