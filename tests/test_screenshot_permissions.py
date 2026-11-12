"""截图权限失败只引导授权，不隐藏窗口或执行捕获。"""
from types import SimpleNamespace
import subprocess
import unittest
from unittest.mock import Mock, patch

from harness.core.language import current_language
from harness.i18n import tr
from harness.web import screenshot
from harness.web.errors import ServiceUnavailable


class ScreenshotPermissionTests(unittest.TestCase):
    def test_denied_screen_opens_settings_on_every_explicit_retry(self):
        graphics = SimpleNamespace(CGPreflightScreenCaptureAccess=Mock(return_value=False),
                                   CGRequestScreenCaptureAccess=Mock(return_value=False))
        with patch.object(screenshot, 'native_available', return_value=True), \
                patch.object(screenshot.ctypes, 'CDLL', return_value=graphics), \
                patch.object(screenshot.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as run, \
                patch.object(screenshot, '_script') as script:
            for language in ('en', 'zh-CN', 'en'):
                with self.subTest(language=language), current_language.set(language):
                    with self.assertRaises(ServiceUnavailable) as caught:
                        screenshot.capture_region(hide_window=True, window_token='harness-capture-' + 'a' * 32)
                    self.assertEqual(caught.exception.code, 'screenshot/permission-required')
                    self.assertEqual(caught.exception.status, 403)
                    self.assertEqual(str(caught.exception), tr('ui.collaboration.error.screenshot/permission-required'))
                    self.assertTrue(screenshot._capture_lock.acquire(blocking=False))
                    screenshot._capture_lock.release()
            self.assertEqual(run.call_count, 3)
            self.assertTrue(all(call.args[0] == ['/usr/bin/open', 'x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture'] for call in run.call_args_list))
            script.assert_not_called()

    def test_granted_screen_does_not_prompt_and_hidden_capture_checks_accessibility(self):
        graphics = SimpleNamespace(CGPreflightScreenCaptureAccess=Mock(return_value=True),
                                   CGRequestScreenCaptureAccess=Mock())
        services = SimpleNamespace(AXIsProcessTrusted=Mock(return_value=False))
        with patch.object(screenshot.ctypes, 'CDLL', side_effect=lambda name: services if 'ApplicationServices' in name else graphics), \
                patch.object(screenshot.subprocess, 'run') as run:
            screenshot._ensure_capture_permissions(False)
            run.assert_not_called()
            with self.assertRaises(ServiceUnavailable) as caught:
                screenshot._ensure_capture_permissions(True)
            self.assertEqual(caught.exception.code, 'screenshot/accessibility-required')
            self.assertIn('Privacy_Accessibility', run.call_args.args[0][1])
            graphics.CGRequestScreenCaptureAccess.assert_not_called()
            services.AXIsProcessTrusted.return_value = True
            screenshot._ensure_capture_permissions(True)
            self.assertEqual(run.call_count, 1)

    def test_first_grant_continues_and_failed_settings_preserve_original_cause(self):
        graphics = SimpleNamespace(CGPreflightScreenCaptureAccess=Mock(return_value=False),
                                   CGRequestScreenCaptureAccess=Mock(return_value=True))
        failure = OSError('original settings {diagnostic}')
        with patch.object(screenshot.ctypes, 'CDLL', return_value=graphics), \
                patch.object(screenshot.subprocess, 'run', side_effect=failure) as run:
            screenshot._ensure_capture_permissions(False)
            run.assert_not_called()
            graphics.CGRequestScreenCaptureAccess.return_value = False
            with self.assertRaises(ServiceUnavailable) as caught:
                screenshot._ensure_capture_permissions(False)
            self.assertEqual(caught.exception.code, 'screenshot/permission-settings-failed')
            self.assertIs(caught.exception.__cause__, failure)
