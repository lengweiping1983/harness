"""验证实时错误文案与提交回执字段保持一致。"""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from harness.core.errors import HarnessError
from harness.core.language import current_language
from harness.web.server import Handler
from harness.web.error_messages import public_error_message


class WsErrorMessageTests(unittest.TestCase):
    def test_known_errors_keep_receipts_and_localize_messages(self):
        """失败只回告一次，不重发写入且不把诊断拼进文案。"""
        for language in ('zh-CN', 'en'):
            for action in ('send', 'steer', 'cancel', 'plan_mode'):
                with self.subTest(language=language, action=action), current_language.set(language):
                    failure = HarnessError('access/denied', code='access/denied', revision=7)
                    runtime = SimpleNamespace(submit=Mock(side_effect=failure))
                    harness = SimpleNamespace(runtime=runtime, steer=AsyncMock(side_effect=failure), cancel=AsyncMock(side_effect=failure), set_plan_mode=AsyncMock(side_effect=failure))
                    handler = SimpleNamespace(ctx=SimpleNamespace(shared_access=None), harness=harness, _prepare_turn_input=Mock(return_value=('text', {})))
                    conn = SimpleNamespace(send_json=AsyncMock())
                    asyncio.run(Handler._handle_ws_payload_in_language(handler, conn, 's', {'action': action, 'text': 'text', 'request_id': 'request', 'active': True}))
                    conn.send_json.assert_awaited_once()
                    result = conn.send_json.call_args.args[0]
                    self.assertEqual(result['message'], public_error_message('access/denied', 'access/denied'))
                    self.assertEqual(result['code'], 'access/denied')
                    if action != 'steer':
                        self.assertEqual(result['details'], {'revision': 7})
                    if action in ('send', 'steer'):
                        self.assertFalse(result['submitted'])
                        self.assertEqual(result['request_id'], 'request')
                        self.assertEqual(result['action'], action)

    def test_specific_diagnostics_remain_structured(self):
        """具体诊断不替换为泛化文案，也不把详情拼进消息。"""
        for message in ('access/denied with a specific reason', 'custom/failure'):
            failure = HarnessError(message, code='access/denied', revision=9)
            handler = SimpleNamespace(ctx=SimpleNamespace(shared_access=None), harness=SimpleNamespace(cancel=AsyncMock(side_effect=failure)))
            conn = SimpleNamespace(send_json=AsyncMock())
            asyncio.run(Handler._handle_ws_payload_in_language(handler, conn, 's', {'action': 'cancel'}))
            self.assertEqual(conn.send_json.call_args.args[0], {'type': 'error', 'code': 'access/denied', 'message': message, 'details': {'revision': 9}})

    def test_plan_mode_requires_an_explicit_boolean_before_write(self):
        """非法JSON值不能通过真值转换改变计划模式。"""
        from harness.i18n import tr
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                for supplied in ({}, *({'active': value} for value in (None, 0, 1, 'false', 'true', [], {}))):
                    with self.subTest(language=language, supplied=supplied):
                        harness = SimpleNamespace(set_plan_mode=AsyncMock(return_value={}))
                        handler = SimpleNamespace(ctx=SimpleNamespace(shared_access=None), harness=harness)
                        conn = SimpleNamespace(send_json=AsyncMock())
                        asyncio.run(Handler._handle_ws_payload_in_language(handler, conn, 's', {'action':'plan_mode', **supplied}))
                        harness.set_plan_mode.assert_not_awaited()
                        conn.send_json.assert_awaited_once()
                        result = conn.send_json.call_args.args[0]
                        self.assertEqual(result['type'], 'error')
                        self.assertEqual(result['code'], 'plan-mode/bad-active')
                        self.assertEqual(result['message'], tr('python.interaction.1f2b7a2fd995'))
                for active in (False, True):
                    state = {'active': active, 'session_id':'s'}
                    harness = SimpleNamespace(set_plan_mode=AsyncMock(return_value=state))
                    handler = SimpleNamespace(ctx=SimpleNamespace(shared_access=None), harness=harness)
                    conn = SimpleNamespace(send_json=AsyncMock())
                    asyncio.run(Handler._handle_ws_payload_in_language(handler, conn, 's', {'action':'plan_mode', 'active':active}))
                    harness.set_plan_mode.assert_awaited_once_with('s', active)
                    conn.send_json.assert_awaited_once_with({'type':'plan_mode', **state})
