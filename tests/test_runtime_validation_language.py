"""运行与工具声明拒绝按语言解析，保持目标、取消和输入边界。"""
import inspect
import threading
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from harness.core.language import current_language
from harness.i18n import tr
from harness.core.errors import ToolExecutionError
from harness.tools.write_scope import declared_targets
from harness.tools.command_output import _preview
from harness.tools.builtin.workflow_tools import validate_meta
from harness.net.request_control import RequestControl, create_socket
from harness.app.run_store import RunRecords
from harness.session.turn_scope import turn_events


class RuntimeValidationLanguageTests(unittest.TestCase):
    def test_rejections_keep_types_and_have_current_language(self):
        cancellation = threading.Event()
        cancellation.set()
        control = RequestControl(cancellation)
        cache = RunRecords(lambda: self.fail('Unexpected database access'))
        calls = [(lambda: declared_targets(SimpleNamespace(write_paths=lambda *_: 'relative', execution_cwd=None), {}, None), ValueError),
            (lambda: declared_targets(SimpleNamespace(write_paths=lambda *_: ['relative'], execution_cwd=None), {}, None), ValueError),
            (lambda: _preview('original', True), ValueError), (control.check, InterruptedError),
            (lambda: cache.__delitem__('original'), TypeError),
            (lambda: cache.__setitem__('different', SimpleNamespace(id='original')), ValueError),
            (lambda: turn_events([]), ValueError), (lambda: validate_meta([]), ToolExecutionError)]
        for language in ('en', 'zh-CN', 'en'):
            for operation, kind in calls:
                with current_language.set(language), self.assertRaises(kind) as caught:
                    operation()
                self.assertEqual(any('\u4e00' <= c <= '\u9fff' for c in str(caught.exception)), language == 'zh-CN')
            self.assertEqual(cache.active, {})
            self.assertEqual(cache.recent, {})
            self.assertTrue(cancellation.is_set())
            self.assertEqual(control.sockets, set())

    def test_workflow_meta_reports_fields_without_rewriting_input(self):
        original = {'name': '', 'description': '', 'unknown': 'Original', 'phases': [7, {'title': '', 'provider': 42}]}
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language), self.assertRaises(ToolExecutionError) as caught:
                validate_meta(original)
            text = caught.exception.message
            for field in ('meta.name', 'meta.description', 'meta.unknown', 'meta.phases[0]', 'meta.phases[1].title', 'meta.phases[1].provider'):
                self.assertIn(field, text)
            self.assertEqual(any('\u4e00' <= c <= '\u9fff' for c in text), language == 'zh-CN')
            self.assertEqual(original['phases'], [7, {'title': '', 'provider': 42}])
        valid = {'name': 'Original', 'description': 'Original 原文', 'phases': [{'title': 'Original 标题'}]}
        with current_language.set('en'):
            self.assertEqual(validate_meta(valid), valid)

    def test_empty_dns_results_are_localized_without_opening_a_socket(self):
        control = RequestControl(threading.Event())
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language), patch('harness.net.request_control.socket.getaddrinfo', return_value=[]), patch('harness.net.request_control.socket.socket') as socket_factory:
                with self.assertRaises(OSError) as caught:
                    create_socket(('fixture.invalid', 443), 1, None, control)
                self.assertEqual(str(caught.exception), tr('network.addresses.empty'))
                socket_factory.assert_not_called()
            self.assertEqual(control.sockets, set())
            self.assertFalse(control.cancelled.is_set())

    def test_dns_system_errors_keep_the_original_exception(self):
        original = OSError('Original DNS diagnostic 原文')
        for language in ('en', 'zh-CN', 'en'):
            control = RequestControl(threading.Event())
            with current_language.set(language), patch('harness.net.request_control.socket.getaddrinfo', side_effect=original):
                with self.assertRaises(OSError) as caught:
                    create_socket(('fixture.invalid', 443), 1, None, control)
                self.assertIs(caught.exception, original)

    def test_async_target_declaration_reports_synchronous_requirement_and_closes_coroutine(self):
        async def targets(*_):
            self.fail('异步目标声明不应被执行')
        for language in ('en', 'zh-CN', 'en'):
            value = targets()
            try:
                definition = SimpleNamespace(write_paths=lambda *_: value, execution_cwd=None)
                with current_language.set(language), self.assertRaises(ValueError) as caught:
                    declared_targets(definition, {}, None)
                expected = 'must be synchronous' if language == 'en' else '不能是异步调用'
                self.assertIn(expected, str(caught.exception))
                self.assertEqual(inspect.getcoroutinestate(value), inspect.CORO_CLOSED)
            finally:
                value.close()
