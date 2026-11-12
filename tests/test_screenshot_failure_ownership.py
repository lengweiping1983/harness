"""原生截图捕获与窗口恢复双失败须保留原件并释放准入。"""
import subprocess
import unittest
from unittest.mock import patch

from harness.core.language import current_language
from harness.web import screenshot
from harness.web.errors import ServiceUnavailable


class ScreenshotFailureOwnershipTests(unittest.TestCase):
    def test_capture_and_restore_failures_remain_separate_and_lock_is_released(self):
        for language in ('en', 'zh-CN'):
            for capture_fails, restore_fails in ((True, True), (True, False), (False, True)):
                with self.subTest(language=language, capture=capture_fails, restore=restore_fails):
                    capture_error = subprocess.TimeoutExpired('capture-fixture', 120)
                    restore_error = OSError('restore-fixture')
                    calls = []

                    def script(source, *args):
                        calls.append(args)
                        if source == screenshot._FIND_WINDOW:
                            return '123'
                        if args[-1] == 'false' and restore_fails:
                            raise restore_error
                        return ''

                    result = subprocess.CompletedProcess([], 0, stdout=b'', stderr=b'')
                    with current_language.set(language), patch.object(screenshot, 'native_available', return_value=True), \
                            patch.object(screenshot, '_ensure_capture_permissions'), \
                            patch.object(screenshot, '_script', side_effect=script), \
                            patch.object(screenshot.time, 'sleep'), \
                            patch.object(screenshot.subprocess, 'run', side_effect=capture_error if capture_fails else None, return_value=result) as capture:
                        with self.assertRaises(Exception) as caught:
                            screenshot.capture_region(hide_window=True, window_token='harness-capture-' + 'a' * 32)
                        error = caught.exception
                        if capture_fails and restore_fails:
                            self.assertIsInstance(error, ServiceUnavailable)
                            self.assertEqual(error.code, 'screenshot/restore-failed')
                            causes = error.__cause__
                            self.assertIsInstance(causes, ExceptionGroup)
                            self.assertEqual(causes.exceptions[0].code, 'screenshot/timeout')
                            self.assertIs(causes.exceptions[0].__cause__, capture_error)
                            self.assertIs(causes.exceptions[1], restore_error)
                        else:
                            self.assertIsInstance(error, ServiceUnavailable)
                            self.assertEqual(error.code, 'screenshot/timeout' if capture_fails else 'screenshot/restore-failed')
                            self.assertIs(error.__cause__, capture_error if capture_fails else restore_error)
                        capture.assert_called_once()
                        self.assertEqual(calls[-1], ('123', 'harness-capture-' + 'a' * 32, 'false'))
                        self.assertTrue(screenshot._capture_lock.acquire(blocking=False))
                        screenshot._capture_lock.release()
