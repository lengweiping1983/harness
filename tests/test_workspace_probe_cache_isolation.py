"""依赖探测快照不能被调用方或生产者的后续修改污染。"""
import unittest
from unittest.mock import Mock, patch

from harness.tools.builtin import workspace_tools as module


class WorkspaceProbeCacheIsolationTests(unittest.TestCase):
    def test_nested_caller_and_producer_changes_never_replace_cached_probe(self):
        key='fixture-isolation'
        payload=[{'command':'fixture','metadata':{'original':['原文']}}]
        produce=Mock(return_value=payload)
        try:
            first=module._cached(key,produce)
            first[0]['metadata']['original'].append('caller')
            payload[0]['metadata']['original'].append('producer')
            second=module._cached(key,produce)
            self.assertEqual(second,[{'command':'fixture','metadata':{'original':['原文']}}])
            produce.assert_called_once()
            self.assertIsNot(first,second)
        finally:
            with module._PROBE_LOCK:
                module._PROBE_CACHE.pop(key,None)
                module._PROBE_FLIGHTS.pop(key,None)

    def test_public_dependency_reads_do_not_expose_cached_rows(self):
        with module._PROBE_LOCK:
            original = dict(module._PROBE_CACHE)
            module._PROBE_CACHE.clear()
        try:
            with patch.object(module,'_probe_node_runtimes',return_value=[{'command':'node','path':'/fixture/node','version':'Original 原文'}]) as node, patch.object(module,'_probe_python_packages',return_value=([{'name':'fixture','version':'1'}],1)) as packages:
                first=module.collect_workspace_dependencies()
                first['node'][0]['version']='modified'
                first['python']['packages'][0]['name']='modified'
                second=module.collect_workspace_dependencies()
                self.assertEqual(second['node'][0]['version'],'Original 原文')
                self.assertEqual(second['python']['packages'][0]['name'],'fixture')
                node.assert_called_once()
                packages.assert_called_once()
        finally:
            with module._PROBE_LOCK:
                module._PROBE_CACHE.clear()
                module._PROBE_CACHE.update(original)
