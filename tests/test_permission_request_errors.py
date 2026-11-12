"""权限请求明确区分缺失字段、非法版本和并发冲突。"""
import asyncio
import json
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from harness import Harness, HarnessConfig
from harness.core.errors import HarnessError
from harness.core.language import current_language
from harness.i18n import tr
from harness.web.api.v1 import build_router
from harness.web.context import WebContext
from harness.web.errors import BadRequest, Conflict
from harness.web.router import Request


class PermissionRequestErrorTests(unittest.TestCase):
    def test_field_errors_and_conflict_remain_distinct(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Harness(HarnessConfig(home=directory, cwd=directory, api_key='offline',
                                        enable_mcp=False, enable_automations=False))
            loop = asyncio.new_event_loop()
            try:
                ctx = WebContext(app, loop)
                path = '/api/v1/sessions/fixture/permissions'
                handler, params = build_router(ctx).resolve('PATCH', path)

                def invoke(body):
                    return handler(Request('PATCH', path, {}, {}, json.dumps(body).encode()), **params)

                for language in ('en', 'zh-CN'):
                    with self.subTest(language=language), current_language.set(language):
                        cases = [
                            ({'permission_policy': 'full_access'}, ['expected_version'], []),
                            ({'expected_version': 'a' * 64}, ['permission_policy'], []),
                            ({}, ['permission_policy', 'expected_version'], []),
                            ({'permission_policy': 'full_access', 'expected_version': 'a' * 64, 'extra': 1}, [], ['extra']),
                        ]
                        with patch.object(app.permissions, 'set_policy', new_callable=AsyncMock) as update:
                            for body, missing, unexpected in cases:
                                with self.subTest(body=body), self.assertRaises(BadRequest) as caught:
                                    invoke(body)
                                error = caught.exception
                                self.assertEqual(error.status, 400)
                                self.assertEqual(error.code, 'permissions/invalid')
                                self.assertEqual(error.details, {'missing': missing, 'unexpected': unexpected})
                                key = 'permissions.missing_field' if missing else 'permissions.unexpected_field'
                                self.assertEqual(error.message, tr(key, field=(missing or unexpected)[0]))
                            for version in (None, 1, '', 'short'):
                                with self.assertRaises(BadRequest) as caught:
                                    invoke({'permission_policy': 'full_access', 'expected_version': version})
                                self.assertEqual(caught.exception.code, 'permissions/invalid-version')
                                self.assertEqual(caught.exception.details, {'field': 'expected_version'})
                            update.assert_not_called()
                        conflict = HarnessError('fixture conflict', code='permissions/changed', version='b' * 64)
                        with patch.object(ctx, 'call', side_effect=loop.run_until_complete), patch.object(
                                app.permissions, 'set_policy', new_callable=AsyncMock, side_effect=conflict) as update:
                            with self.assertRaises(Conflict) as caught:
                                invoke({'permission_policy': 'full_access', 'expected_version': 'a' * 64})
                            self.assertEqual(caught.exception.status, 409)
                            self.assertEqual(caught.exception.code, 'permissions/changed')
                            self.assertEqual(caught.exception.details, conflict.details)
                            update.assert_awaited_once_with('fixture', 'full_access', expected_version='a' * 64)
            finally:
                loop.run_until_complete(app.close())
                loop.close()
