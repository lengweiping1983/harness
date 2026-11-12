"""检查所有 API Key 配置使用相同脱敏表示，不返回明文。"""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from harness.llm.routes import ModelRoutes, mask_api_key
from harness.app.mcp_service import McpService
from harness.app.mcp_tunnel import DEFAULT_TUNNEL, public_tunnel
from harness.web.api.settings import build_router
from harness.web.router import Request


class ApiKeyMaskTests(unittest.TestCase):
    def test_mcp_credentials_share_mask_without_exposing_secrets(self):
        token = 'fixture-tunnel-Xy12'
        tunnel = public_tunnel({**DEFAULT_TUNNEL, 'token': token})
        self.assertEqual(tunnel['token_mask'], '******Xy12')
        self.assertNotIn(token, json.dumps(tunnel))
        with tempfile.TemporaryDirectory() as folder:
            service = McpService(folder)
            service.home.mkdir()
            secret = 'fixture-password-Ab34'
            (service.home / 'password.json').write_text(json.dumps({'password': secret}), encoding='utf-8')
            service._process = service._tunnel = Mock(poll=Mock(return_value=None))
            service._state['status'] = 'running'
            snapshot = service.snapshot()
            self.assertEqual(snapshot['password_mask'], '******Ab34')
            self.assertNotIn(secret, json.dumps(snapshot))
            self.assertEqual(service.password(), secret)
            service._state['status'] = 'stopped'
            self.assertEqual(service.snapshot()['password_mask'], '')

    def test_short_keys_are_not_exposed(self):
        for value in ("", "a", "abc", "abcd", "abcde", "secret-Ed8R", "nv-secret-t3blr"):
            with self.subTest(value=value):
                mask = mask_api_key(value)
                self.assertEqual(mask, "" if not value else "******" + (value[-4:] if len(value) > 4 else ""))

    def test_model_snapshot_uses_same_mask(self):
        pool = ModelRoutes([{"id": "model", "model": "fixture", "base_url": "https://example.test/v1",
                             "keys": [{"id": "key", "value": "nv-secret-t3blr"}]}])
        snapshot = pool.snapshot()
        self.assertEqual(snapshot["models"][0]["keys"][0]["mask"], "******3blr")
        self.assertNotIn("nv-secret", json.dumps(snapshot))
        lease = asyncio.run(pool.acquire())
        try:
            pool.update([{"id": "model", "model": "fixture", "base_url": "https://example.test/v1",
                          "keys": [{"id": "new-key", "value": "replacement-Xy12"}]}])
            snapshot = pool.snapshot()
            self.assertEqual(snapshot["models"][0]["keys"][0]["mask"], "******Xy12")
            self.assertEqual(snapshot["retiring"][0]["mask"], "******3blr")
            self.assertNotIn("nv-secret", json.dumps(snapshot))
            self.assertNotIn("replacement-", json.dumps(snapshot))
        finally:
            pool.release(lease[2])

    def test_settings_masks_follow_saved_keys_and_preserve_omitted_keys(self):
        with tempfile.TemporaryDirectory() as folder:
            config = SimpleNamespace(home=Path(folder), deepseek_web_api_key="env-deep-Ed8R",
                                     brave_web_api_key="", language="auto")
            router = build_router(SimpleNamespace(harness=SimpleNamespace(config=config)))

            def call(method, body=None):
                handler, params = router.resolve(method, "/api/v1/ui-settings")
                response = handler(Request(method, "/api/v1/ui-settings", {}, {}, json.dumps(body).encode() if body else b""), **params)
                return json.loads(response.body)["settings"]

            first = call("GET")
            self.assertEqual(first["deepseek_web_api_key_mask"], "******Ed8R")
            self.assertEqual(first["brave_web_api_key_mask"], "")
            saved = call("PATCH", {"brave_web_api_key": "brave-key-Xy12", "brave_web_enabled": True})
            self.assertEqual(saved["brave_web_api_key_mask"], "******Xy12")
            kept = call("PATCH", {"brave_web_enabled": False})
            self.assertEqual(kept["brave_web_api_key_mask"], saved["brave_web_api_key_mask"])
            self.assertEqual(call("GET")["brave_web_api_key_mask"], saved["brave_web_api_key_mask"])
            self.assertNotIn("brave_web_api_key", kept)
            self.assertNotIn("deepseek_web_api_key", kept)
            self.assertNotIn("brave-key-Xy12", json.dumps(kept))
