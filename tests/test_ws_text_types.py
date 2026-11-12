"""实时发送与引导不能把JSON非文本转换为可提交正文。"""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from harness.core.errors import HarnessError
from harness.core.language import current_language
from harness.i18n import tr
from harness.web.server import Handler


class WsTextTypesTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_text_is_rejected_with_receipt_before_materialization_or_submit(self):
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                for action in ('send', 'steer'):
                    for text in (None, True, False, 7, [], {}):
                        with self.subTest(language=language, action=action, text=text):
                            runtime = SimpleNamespace(submit=Mock())
                            harness = SimpleNamespace(runtime=runtime, steer=AsyncMock())
                            prepare = Mock(side_effect=AssertionError('Invalid input reached attachment materialization'))
                            handler = SimpleNamespace(ctx=SimpleNamespace(shared_access=None), harness=harness, _prepare_turn_input=prepare)
                            conn = SimpleNamespace(send_json=AsyncMock())
                            await Handler._handle_ws_payload_in_language(handler, conn, 'fixture', {'action': action, 'text': text, 'attachments': ['ref'], 'request_id': 'receipt'})
                            conn.send_json.assert_awaited_once()
                            result = conn.send_json.call_args.args[0]
                            self.assertEqual(result['code'], 'run/invalid')
                            self.assertEqual(result['message'], tr('web.input.invalid_text'))
                            self.assertEqual((result['action'], result['request_id'], result['submitted']), (action, 'receipt', False))
                            prepare.assert_not_called();runtime.submit.assert_not_called();harness.steer.assert_not_awaited()

    async def test_empty_attachment_text_and_valid_original_text_still_reach_current_path(self):
        for action in ('send', 'steer'):
            for text, refs in [('', ['ref']), (' Original 原文 {name} ', None)]:
                failure = HarnessError('Fixture rejection', code='fixture/rejected')
                runtime = SimpleNamespace(submit=Mock(side_effect=failure))
                harness = SimpleNamespace(runtime=runtime, steer=AsyncMock(side_effect=failure))
                prepare = Mock(return_value=('preview', {'attachment_refs': refs or []}))
                handler = SimpleNamespace(ctx=SimpleNamespace(shared_access=None), harness=harness, _prepare_turn_input=prepare)
                conn = SimpleNamespace(send_json=AsyncMock())
                await Handler._handle_ws_payload_in_language(handler, conn, 'fixture', {'action': action, 'text': text, 'attachments': refs, 'request_id': 'receipt'})
                prepare.assert_called_once_with('fixture', text.strip() if action == 'send' else text, refs)
                self.assertEqual(conn.send_json.call_args.args[0]['code'], failure.code)
