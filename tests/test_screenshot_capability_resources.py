"""截图能力返回资源身份，不把调用语言固定到浏览器状态。"""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from harness.core.language import current_language
from harness.i18n import resources
from harness.web.api.screenshot import build_router
from harness.web.router import Request
from harness.web.protocol import SCHEMAS
from harness.tools.schema import validate


class ScreenshotCapabilityResourceTests(unittest.TestCase):
    def test_capability_reason_uses_browser_resource_and_no_capture(self):
        router = build_router(SimpleNamespace())
        path = '/api/v1/screenshot/capabilities'
        handler, params = router.resolve('GET', path)
        for language in ('en', 'zh-CN', 'en'):
            for local, available in ((True, True), (True, False), (False, True)):
                with self.subTest(language=language, local=local, available=available), current_language.set(language):
                    with patch('harness.web.api.screenshot.screenshot.native_available', return_value=available) as native, \
                         patch('harness.web.api.screenshot.screenshot.capture_region') as capture:
                        request = Request('GET', path, {}, {'Host': 'localhost'}, b'',
                            client_ip='127.0.0.1' if local else '203.0.113.9')
                        data = json.loads(handler(request, **params).body)
                    enabled = local and available
                    self.assertEqual(data, {'native': enabled, 'hideWindow': enabled,
                        'reason': None if enabled else {'key': 'ui.screenshot.nativeUnavailable'}})
                    reason = data['reason']
                    if reason is not None:
                        self.assertEqual(validate(reason, SCHEMAS['ScreenshotCapabilityReason']), [])
                    if not enabled:
                        self.assertTrue(resources(language)[data['reason']['key']])
                    self.assertEqual(native.call_count, int(local))
                    capture.assert_not_called()
