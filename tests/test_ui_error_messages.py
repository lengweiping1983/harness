"""验证界面错误的可操作文案与服务端异常隔离。"""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from harness.core.language import current_language
from harness.core.errors import HarnessError, PersistenceError, SessionNotFoundError
from harness.agents.personality_library import PersonalityLibrary
from harness.app.mcp_service import McpService
from harness.web.tool_results import read_result
from harness.i18n import tr
from harness.web.api.interaction import build_router as interaction_router
from harness.web.api.v1 import build_router as v1_router
from harness.web.api.directories import build_router
from harness.web.api.plugin_packages import build_router as plugin_router
from harness.web.api.mcp_service import build_router as mcp_router
from harness.web.errors import ApiError, BadRequest
from harness.web.router import Request
from harness.web.server import Handler
from harness.web.responses import error_response
from harness.web.error_messages import ERROR_MESSAGE_KEYS, public_error_message
from harness.web.protocol import openapi


class UiErrorMessageTests(unittest.TestCase):
    def test_plugin_validation_is_localized_before_any_operation(self):
        """无效目录与未预览安装应先拒绝，不触发插件业务写入。"""
        service, call = Mock(), Mock()
        router = plugin_router(SimpleNamespace(harness=SimpleNamespace(plugin_service=service), call=call))
        messages = {}
        cases = (
            ('/api/v1/plugin-marketplaces', {'path': ' '}, 'plugin-package/bad-body'),
            ('/api/v1/plugin-marketplaces/preview', {'path': '/fixture', 'name': 'plugin', 'fingerprint': 'invalid'}, 'plugin-package/bad-body'),
            ('/api/v1/plugin-packages/install', {'path': '/fixture'}, 'plugin-package/preview-required'),
        )
        for language in ('zh-CN', 'en'):
            with current_language.set(language):
                for path, body, code in cases:
                    handler, params = router.resolve('POST', path)
                    with self.assertRaises(BadRequest) as caught:
                        handler(Request('POST', path, {}, {}, json.dumps(body).encode()), **params)
                    self.assertEqual(caught.exception.code, code)
                    messages[language, path] = str(caught.exception)
        for path, _, _ in cases:
            self.assertNotEqual(messages['zh-CN', path], messages['en', path])
        self.assertEqual(service.mock_calls, [])
        call.assert_not_called()

    def test_local_mcp_boundary_keeps_rejecting_remote_requests(self):
        """提示翻译不能放宽本机管理边界，也不能访问服务状态。"""
        service = Mock()
        router = mcp_router(SimpleNamespace(mcp_service=service))
        path = '/api/v1/mcp-service'
        handler, params = router.resolve('GET', path)
        messages = []
        for language in ('zh-CN', 'en'):
            with current_language.set(language):
                request = Request('GET', path, {}, {'Host': 'localhost'}, b'', client_ip='203.0.113.9')
                with self.assertRaises(ApiError) as caught:
                    handler(request, **params)
                self.assertEqual(caught.exception.code, 'mcp/local-only')
                self.assertEqual(caught.exception.status, 403)
                messages.append(str(caught.exception))
        self.assertNotEqual(*messages)
        self.assertEqual(service.mock_calls, [])

    def test_registered_codes_share_browser_resource_identity(self):
        from harness.i18n import resources
        root = Path(__file__).resolve().parents[1]
        for language in ('en', 'zh-CN', 'en'):
            browser = json.loads((root / 'web/src/i18n/locales' / f'{language}.json').read_text(encoding='utf-8'))
            missing = [code for code in ERROR_MESSAGE_KEYS if 'ui.collaboration.error.' + code not in browser]
            self.assertEqual(missing, [])
            with current_language.set(language):
                for code, key in ERROR_MESSAGE_KEYS.items():
                    self.assertEqual(key, 'ui.collaboration.error.' + code)
                    self.assertEqual(browser[key], resources(language)[key])
                    self.assertEqual(public_error_message(code, code), tr(key, code=code))

    def test_registered_code_messages_are_localized_without_changing_contract(self):
        translated = []
        for language in ('zh-CN', 'en'):
            with current_language.set(language):
                for code in ERROR_MESSAGE_KEYS:
                    response = error_response(code, status=403, code=code, details={'revision': 7})
                    data = json.loads(response.body)
                    self.assertEqual(response.status, 403)
                    self.assertEqual(data['code'], code)
                    self.assertEqual(data['details'], {'revision': 7})
                    self.assertNotEqual(data['error'], code)
                    self.assertEqual(data['error'], public_error_message(code, code))
                translated.append(public_error_message('access/denied', 'access/denied'))
                for message, code in (('specific diagnostic', 'access/denied'), ('future/error', 'future/error')):
                    data = json.loads(error_response(message, code=code).body)
                    self.assertEqual(data['error'], message)
        self.assertNotEqual(*translated)

    def test_tool_result_parameters_are_unique_and_keep_read_limits(self):
        path = '/api/v1/sessions/{session_id}/tool-results/{source_seq}'
        parameters = openapi(['GET ' + path])['paths'][path]['get']['parameters']
        identities = [(item['in'], item['name']) for item in parameters]
        self.assertEqual(len(identities), len(set(identities)))
        limits = next(item['schema'] for item in parameters if item['name'] == 'limit')
        self.assertEqual(limits['minimum'], 1)
        self.assertEqual(limits['maximum'], 8192)
        self.assertIn(('query', 'agent_id'), identities)
        self.assertIn(('query', 'before'), identities)
        self.assertIn(('query', 'through'), identities)

    def test_unexpected_exception_details_stay_on_server(self):
        for language in ('zh-CN', 'en'):
            with current_language.set(language):
                handler = SimpleNamespace(_accept_encoding=lambda: '', _send=Mock(), _apply_cors=lambda response: response)
                with self.assertLogs('harness.web.server', level='ERROR') as logs:
                    try:
                        raise RuntimeError('private-database-path-and-key')
                    except RuntimeError as error:
                        Handler._error(handler, error)
                response = handler._send.call_args.args[0]
                data = json.loads(response.body)
                self.assertEqual(data['code'], 'internal/error')
                self.assertNotIn('private-database-path-and-key', data['error'])
                self.assertIn('private-database-path-and-key', '\n'.join(logs.output))

    def test_directory_errors_distinguish_missing_from_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            file = root / 'file.txt'
            file.write_text('fixture', encoding='utf-8')
            router = build_router(SimpleNamespace())
            handler, params = router.resolve('GET', '/api/v1/directories')
            for language in ('en', 'zh-CN', 'en'):
                messages = []
                with current_language.set(language):
                    for path, code in ((root / 'missing', 'dir/not-found'), (file, 'dir/not-a-directory'),
                                       ('\0', 'dir/invalid-path'), (str(root) + '\0missing', 'dir/invalid-path')):
                        request = Request('GET', '/api/v1/directories', {'path': [str(path)]}, {}, b'')
                        with self.assertRaises(BadRequest) as caught:
                            handler(request, **params)
                        self.assertEqual(caught.exception.code, code)
                        messages.append(str(caught.exception))
                self.assertEqual(len(set(messages)), 3)

    def test_domain_http_status_keeps_failure_identity(self):
        """持久化失败不是客户端输入错误，不存在会话也不是参数错误。"""
        for error, status in ((PersistenceError('storage unavailable', revision=7), 503),
                              (SessionNotFoundError('missing session', session_id='s'), 404)):
            handler = SimpleNamespace(_accept_encoding=lambda: '', _send=Mock(), _apply_cors=lambda response: response)
            Handler._error(handler, error)
            response = handler._send.call_args.args[0]
            data = json.loads(response.body)
            self.assertEqual(response.status, status)
            self.assertEqual(data['code'], error.code)
            self.assertEqual(data['details'], error.details)

    def test_question_service_bug_is_not_a_missing_question(self):
        """未知服务错误交给统一500出口，不伪装404或泄露异常文本。"""
        service = Mock()
        service.pending.side_effect = RuntimeError('private service diagnostic')
        router = interaction_router(SimpleNamespace(harness=SimpleNamespace(questions=service)))
        handler, params = router.resolve('GET', '/api/v1/questions')
        with self.assertRaisesRegex(RuntimeError, 'private service diagnostic'):
            handler(Request('GET', '/api/v1/questions', {}, {}, b''), **params)

    def test_personality_invalid_input_uses_current_language_without_writing(self):
        """人格校验保持原有拒绝条件，错误随当前语言显示。"""
        with tempfile.TemporaryDirectory() as home:
            library = PersonalityLibrary(home)
            messages = []
            for language in ('zh-CN', 'en'):
                with current_language.set(language), self.assertRaises(HarnessError) as caught:
                    library.save({'name': '', 'description': '', 'prompt': 'test'})
                self.assertEqual(caught.exception.code, 'personality/invalid')
                messages.append(caught.exception.message)
                self.assertFalse(library.path.exists())
            self.assertNotEqual(*messages)

    def test_mcp_snapshot_localizes_known_background_error_without_mutating_state(self):
        """后台已登记错误按读取时语言投影，不能改写保存状态。"""
        from harness.app.mcp_errors import McpServiceError
        original = McpServiceError('ui.mcpService.error.startTimeout')
        state = {'status': 'failed', 'error': original}
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        manager = McpService(directory.name)
        manager._state = state
        with current_language.set('en'):
            self.assertEqual(McpService.snapshot(manager)['error'], tr('ui.mcpService.error.startTimeout'))
        self.assertEqual(state['error'], original)
        state['error'] = tr('ui.mcpService.error.startTimeout')
        with current_language.set('en'):
            self.assertEqual(McpService.snapshot(manager)['error'], state['error'])

    def test_tool_evidence_errors_keep_codes_and_read_boundaries(self):
        """原文读取仍先校验范围，未找到证据时也不猜测其他结果。"""
        session = Mock()
        session.event_at.return_value = None
        messages = []
        for language in ('zh-CN', 'en'):
            with current_language.set(language):
                with self.assertRaises(BadRequest) as caught:
                    read_result(session, 1, limit=8193)
                self.assertEqual(caught.exception.code, 'tool-result/invalid-range')
                messages.append(caught.exception.message)
                session.event_at.assert_not_called()
                from harness.web.errors import NotFound
                with self.assertRaises(NotFound) as missing:
                    read_result(session, 1)
                self.assertEqual(missing.exception.code, 'tool-result/missing')
                session.event_at.assert_called_once_with(1)
                session.reset_mock()
        self.assertNotEqual(*messages)

    def test_settings_integer_messages_match_nullable_and_zero_constraints(self):
        """拒绝条件不变，但提示必须准确说明每类字段是否允许零和空值。"""
        ctx = SimpleNamespace(call=Mock())
        router = v1_router(ctx)
        handler, params = router.resolve('PATCH', '/api/v1/settings')
        cases = (
            ('output_reserve', None, 'python.settings.nonnegativeInteger'),
            ('output_reserve', -1, 'python.settings.nonnegativeInteger'),
            ('network_timeout', None, 'python.settings.positiveInteger'),
            ('context_working_budget', 0, 'python.settings.positiveInteger'),
            ('max_steps', 0, 'python.settings.nullablePositiveInteger'),
            ('model_max_output_tokens', True, 'python.settings.nullablePositiveInteger'),
        )
        for language in ('zh-CN', 'en'):
            with current_language.set(language):
                for field, value, message_key in cases:
                    with self.subTest(language=language, field=field, value=value), self.assertRaises(BadRequest) as caught:
                        handler(Request('PATCH', '/api/v1/settings', {}, {}, json.dumps({field: value}).encode()), **params)
                    self.assertEqual(caught.exception.code, 'settings/invalid')
                    self.assertEqual(caught.exception.message, tr(message_key, field=field))
                # max_tokens 是读取时的能力投影，不是当前可编辑字段，不能恢复旧写入别名。
                with self.assertRaises(BadRequest) as unsupported:
                    handler(Request('PATCH', '/api/v1/settings', {}, {}, json.dumps({'max_tokens': True}).encode()), **params)
                self.assertEqual(unsupported.exception.code, 'settings/invalid')
                self.assertEqual(unsupported.exception.message, tr('python.v1.5fefdc38f00c'))
        ctx.call.assert_not_called()
