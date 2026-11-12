"""在全新解释器中验证统一服务与认证传输不装载已退役编排。"""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import textwrap
import unittest


class UnifiedLoadingTests(unittest.TestCase):
    def test_unified_tick_and_authenticated_clients_do_not_import_legacy_orchestrators(self):
        script = textwrap.dedent('''
            import asyncio
            import importlib.abc
            from pathlib import Path
            import sys
            import tempfile
            from types import SimpleNamespace

            forbidden = {
                "harness.app.superagent_" + suffix for suffix in (
                    "inbox", "dispatch", "router", "workers", "results",
                    "mission_store", "missions", "guidance", "guidance_files",
                    "control", "mission_control", "mission_status", "bindings", "github", "feishu", "shadow",
                )
            }

            class RejectLegacy(importlib.abc.MetaPathFinder):
                def find_spec(self, fullname, path=None, target=None):
                    if fullname in forbidden:
                        raise AssertionError(fullname)

            sys.meta_path.insert(0, RejectLegacy())
            from harness.app.superagent_service import SuperagentService
            from harness.app.superagent_executor import WorkerProfile
            from harness.app.superagent_feishu_http import FeishuClient
            from harness.app.superagent_github_http import GitHubClient

            async def run():
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    profile = WorkerProfile("work", str(root), "fixture", ("local",), ("read",), ())
                    closed = []
                    executor = SimpleNamespace(
                        instance_id="isolated", profiles={"work": profile},
                        set_worker_lookup=lambda lookup: None,
                        close=lambda: closed.append(True),
                    )
                    service = await SuperagentService.open_coordinator(
                        root / "state", instance_id="isolated", identity={"suite": "loading"},
                        executor_adapter=executor,
                    )
                    try:
                        status = await service.tick()
                        assert status["runtime_state"] == "running", status
                        assert status["active_assignments"] == 0, status
                        assert type(FeishuClient({"api_url": "https://open.feishu.cn", "app_id": "cli_fixture"}, "fixture")).__module__ == "harness.app.superagent_feishu_http"
                        assert GitHubClient("https://api.github.com", "fixture").token == "fixture"
                    finally:
                        await service.aclose()
                    assert closed == [True], closed
                    assert {path.name for path in (root / "state").rglob("*.sqlite3")} == {"superagent.sqlite3"}
                    assert not forbidden.intersection(sys.modules), forbidden.intersection(sys.modules)

            asyncio.run(run())
        ''')
        result = subprocess.run(
            [sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
            text=True, capture_output=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
