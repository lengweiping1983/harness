"""LSP 响应的布尔ID不能冒充本客户端的整数请求。"""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from harness.core.language import current_language
from harness.i18n import tr
from harness.tools.builtin.lsp_tools import LspClient, _RpcError


class LspResponseIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_boolean_and_noninteger_response_ids_are_rejected(self):
        for language in ('en', 'zh-CN', 'en'):
            for identity in (True, False, None, '1', 1.0):
                with self.subTest(language=language, identity=identity), current_language.set(language):
                    client = LspClient(SimpleNamespace())
                    with patch.object(client, '_send', new_callable=AsyncMock) as send, patch.object(client, '_read_message', new_callable=AsyncMock, side_effect=[{'id': identity, 'result': 'Wrong identity'}, {'id': 1, 'result': 'Original'}]):
                        with self.assertRaises(_RpcError) as caught:
                            await client.request('fixture', {}, timeout=1)
                        self.assertEqual(str(caught.exception), tr('python.lsp_tools.2ea88da8f9c6'))
                        send.assert_awaited_once()

    async def test_unknown_integer_response_and_notifications_do_not_replace_original_result(self):
        client = LspClient(SimpleNamespace())
        with patch.object(client, '_send', new_callable=AsyncMock) as send, patch.object(client, '_read_message', new_callable=AsyncMock, side_effect=[{'method': 'original/notification', 'params': {}}, {'id': 2, 'result': 'Other request'}, {'id': 1, 'result': {'original': '原文'}}]):
            self.assertEqual(await client.request('fixture', {}, timeout=1), {'original': '原文'})
            send.assert_awaited_once()
