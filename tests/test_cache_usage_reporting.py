"""缓存报告的分母、部分覆盖与原件保护回归。"""
import copy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from harness.session.request_traces import RequestTraces, _usage_totals


class CacheUsageReportingTests(unittest.TestCase):
    def test_positive_reads_supply_missing_coverage_per_request(self):
        """先按每次请求补足报告证据，再汇总，不能把未报告请求放入分母。"""
        rows = [{'usage': {'input_tokens': 101521, 'output_tokens': 21501, 'cache_read_tokens': 89088}},
                {'usage': {'input_tokens': 200, 'output_tokens': 3}},
                {'usage': {'input_tokens': 100, 'output_tokens': 2, 'cache_read_tokens': 20, 'cache_reported_input_tokens': 50}},
                {'usage': None}]
        original = copy.deepcopy(rows)
        totals, reported, invalid = _usage_totals(rows)
        self.assertEqual((reported, invalid), (3, 0))
        self.assertEqual((totals['cache_read_tokens'], totals['cache_reported_input_tokens']), (89108, 101571))
        self.assertEqual(totals['total_tokens'], 123327)
        self.assertEqual(rows, original)

    def test_explicit_zero_partial_coverage_and_impossible_counts_stay_distinct(self):
        """明确的覆盖量不被推断覆盖，越界数据仍报告异常。"""
        for usage, inconsistent in [
            ({'cache_read_tokens': 5}, 0),
            ({'cache_read_tokens': 0}, 0),
            ({'cache_read_tokens': 0, 'cache_reported_input_tokens': 10}, 0),
            ({'cache_read_tokens': 5, 'cache_reported_input_tokens': 0}, 1),
            ({'cache_read_tokens': 11}, 1),
            ({'cache_read_tokens': 5, 'cache_reported_input_tokens': 4}, 1),
            ({'cache_read_tokens': 5, 'cache_reported_input_tokens': 11}, 1),
        ]:
            with self.subTest(usage=usage):
                _, reported, invalid = _usage_totals([{'usage': {'input_tokens': 10, 'output_tokens': 2, **usage}}])
                self.assertEqual((reported, invalid), (1, inconsistent))
        for value in (True, -1, 1.5, '5', None):
            with self.subTest(value=value):
                _, reported, _ = _usage_totals([{'usage': {'input_tokens': 10, 'output_tokens': 2, 'cache_read_tokens': value}}])
                self.assertEqual(reported, 0)

    def test_memory_and_disk_projections_preserve_the_archive(self):
        """本对话和后代汇总共用规则，重新读取仍返回未经改写的原始用量。"""
        with TemporaryDirectory() as root:
            for path in (None, Path(root) / 'traces.sqlite'):
                archive = RequestTraces(path)
                for session_id, parent in [('root', None), ('child', 'root')]:
                    session = SimpleNamespace(id=session_id, header=SimpleNamespace(parent_session=parent),
                                              request_checkpoint=lambda: {'event_seq': 1})
                    trace_id = archive.start(session, body='{"model":"fixture","messages":[]}',
                                             url='https://example.com', request_id=1, purpose='agent')
                    usage = {'input_tokens': 100, 'output_tokens': 10, 'cache_read_tokens': 80}
                    archive.finish(trace_id, 'completed', usage)
                    summary = archive.context_snapshot(session_id, through=1)
                    self.assertEqual(summary['cache_inconsistent_requests'], 0)
                    self.assertEqual(summary['usage']['cache_reported_input_tokens'], 100)
                    self.assertEqual(archive.read(session_id, trace_id)[0]['usage'], usage)
                totals = archive.cost_summary('root')
                self.assertEqual(totals['cache_inconsistent_requests'], 0)
                self.assertEqual(totals['usage']['cache_reported_input_tokens'], 200)
                self.assertEqual(totals['usage']['cache_read_tokens'], 160)
