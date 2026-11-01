"""依赖探测参数、稳定包选择及取消时工作线程归属。"""
import asyncio
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from harness.core.errors import ToolExecutionError
from harness.core.language import current_language
from harness.i18n import tr
from harness.tools.builtin import workspace_tools as module


class WorkspaceProbeBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_package_flag_never_starts_a_probe(self):
        tool=module.build_workspace_dependency_tools()[0]
        for language in ('en','zh-CN','en'):
            for value in (None,1,0,'false',[],{}):
                with self.subTest(value=value,language=language), current_language.set(language), patch.object(module,'collect_workspace_dependencies') as probe:
                    with self.assertRaises(ToolExecutionError) as error:
                        await tool.execute({'include_packages':value},None)
                    self.assertEqual(error.exception.message,tr('tool.arguments.boolean',argument='include_packages'))
                    probe.assert_not_called()

    async def test_cancelled_tool_joins_original_probe_thread(self):
        entered,release=threading.Event(),threading.Event()
        def probe(**kwargs):
            entered.set();release.wait(5)
            return {}
        tool=module.build_workspace_dependency_tools()[0]
        with patch.object(module,'collect_workspace_dependencies',side_effect=probe) as collect:
            task=asyncio.create_task(tool.execute({'include_packages':False},None))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait,1))
                task.cancel();await asyncio.sleep(0);task.cancel();await asyncio.sleep(0)
                self.assertFalse(task.done())
            finally:
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            collect.assert_called_once_with(include_packages=False)

    async def test_package_limit_selects_same_sorted_rows_in_any_enumeration_order(self):
        rows=[SimpleNamespace(metadata={'Name':name},version='1') for name in ('Zebra','alpha','Beta','delta')]
        for order in (rows,list(reversed(rows))):
            with patch.object(module,'MAX_PACKAGES',2), patch.object(module.importlib_metadata,'distributions',return_value=order):
                selected,total=module._probe_python_packages()
            self.assertEqual(selected,[{'name':'alpha','version':'1'},{'name':'Beta','version':'1'}])
            self.assertEqual(total,4)
