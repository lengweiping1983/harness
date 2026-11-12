"""诊断入口与解析器公开导出的回归。"""
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from harness.app.browser import BrowserService
from harness.app.browser_diagnostics import PageDiagnostics


class BrowserDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_and_selected_page_limits(self):
        browser = BrowserService(Path("."))
        self.assertEqual((await browser.diagnostics("session"))["records"], [])
        page = Mock()
        page.is_closed.return_value = False
        browser.contexts["session"] = SimpleNamespace(pages=[page])
        browser._active["session"] = page
        diagnostics = browser._diagnostics[page] = PageDiagnostics()
        for i in range(3):
            diagnostics.add("console", str(i))
        result = await browser._execute("session", {"action": "diagnostics", "limit": 2}, actor="agent")
        self.assertEqual(len(result["records"]), 2)
        self.assertEqual(result["records_omitted"], 1)
        cleared = await browser.diagnostics("session", expected_revision=diagnostics.revision)
        self.assertEqual(cleared["records"], [])

    def test_parser_public_exports_resolve(self):
        namespace = {}
        exec("from harness.agents.parser import *", namespace)
        self.assertIn("parse_agent_text", namespace)
