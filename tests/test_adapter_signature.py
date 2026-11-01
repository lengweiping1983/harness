"""第三方适配器签名降级的诊断边界回归。"""
import unittest
from unittest.mock import patch

from harness.core.agent import _adapter_parameters


class AdapterSignatureTests(unittest.TestCase):
    def test_supported_callbacks_remain_available(self):
        async def complete(messages, *, on_request=None, on_event=None):
            return messages

        with patch('harness.core.agent.logger.warning') as warning:
            parameters = _adapter_parameters(complete)
        self.assertIn('on_request', parameters)
        self.assertIn('on_event', parameters)
        warning.assert_not_called()

    def test_uninspectable_adapter_reports_reason_and_uses_base_protocol(self):
        for error in (ValueError('signature unavailable'), TypeError('unsupported callable')):
            with self.subTest(error=type(error).__name__):
                with patch('harness.core.agent.inspect.signature', side_effect=error):
                    with self.assertLogs('harness.core.agent', level='WARNING') as logs:
                        self.assertEqual(_adapter_parameters(object()), {})
                self.assertIn(str(error), logs.output[0])
                self.assertIn('跳过可选回调和缓存参数', logs.output[0])

    def test_unexpected_failure_is_propagated(self):
        with patch('harness.core.agent.inspect.signature', side_effect=RuntimeError('broken adapter')):
            with self.assertRaisesRegex(RuntimeError, 'broken adapter'):
                _adapter_parameters(object())
