"""安装参数必须在解析作用域和写入文件前保持严格类型。"""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from harness.core.language import current_language
from harness.i18n import tr
from harness.web.api.plugins import _install_payload, build_router
from harness.web.errors import BadRequest
from harness.web.router import Request


class PluginInstallPayloadTypesTests(unittest.TestCase):
    def test_both_install_routes_reject_invalid_fields_before_scope_or_write(self):
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                for kind in ('skills', 'agents'):
                    for field, values, key, code in [
                        ('path', [None, True, False, 1, [], {}, '', ' ', 'x' * 1025], 'python.plugins.04625d617c0f', 'plugin/bad-path'),
                        ('scope', [None, True, False, 0, [], {}, '', 'other'], 'python.plugins.7e17a180a7cf', 'plugin/bad-scope'),
                    ]:
                        for value in values:
                            with self.subTest(language=language, kind=kind, field=field, value=value):
                                harness = Mock()
                                router = build_router(SimpleNamespace(harness=harness))
                                path = f'/api/v1/plugins/{kind}/install'
                                body = {'path': '/Original 路径', 'scope': 'project', 'session_id': 'fixture', field: value}
                                request = Request('POST', path, {}, {}, json.dumps(body).encode())
                                handler, params = router.resolve('POST', path)
                                with patch('harness.web.api.plugins.install_from_directory') as install:
                                    with self.assertRaises(BadRequest) as error:
                                        handler(request, **params)
                                self.assertEqual(error.exception.code, code)
                                self.assertEqual(error.exception.message, tr(key))
                                install.assert_not_called()
                                harness.get_session.assert_not_called()
                                harness.refresh_skills.assert_not_called()
                                harness.refresh_agents.assert_not_called()

    def test_valid_string_parameters_keep_original_path_and_default_scope(self):
        for scope, expected in [(' Project ', 'project'), ('USER', 'user')]:
            request = Mock()
            request.json.return_value = {'path': ' /Original 原文/{name} ', 'scope': scope}
            self.assertEqual(_install_payload(request), ('/Original 原文/{name}', expected))
        request.json.return_value = {'path': 'x' * 1024}
        self.assertEqual(_install_payload(request), ('x' * 1024, 'user'))
