"""插件目录不将损坏连接器身份列表解释为可用应用。"""
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from harness.core.errors import ConnectorError
from harness.core.language import current_language
from harness.i18n import tr
from harness.plugins.service import PackageService, _app_identities


class PluginCatalogAppIdTests(unittest.TestCase):
    def test_invalid_identities_are_rejected_before_package_projection_without_mutation(self):
        for language in ('en', 'zh-CN', 'en'):
            for value in ('prefix-app-suffix', None, True, 1, {}, ['app', 1]):
                with self.subTest(language=language, value=value), current_language.set(language):
                    row = {'name':'fixture', 'metadata':{'app_ids':value}}
                    original = copy.deepcopy(row)
                    service = PackageService.__new__(PackageService)
                    service.harness = SimpleNamespace(connectors=SimpleNamespace(catalog=lambda:[row]))
                    service.store = SimpleNamespace(entries=Mock())
                    with self.assertRaises(ConnectorError) as caught:
                        service.catalog()
                    self.assertEqual(caught.exception.message, tr('plugin.validation.e02c59d38c60'))
                    service.store.entries.assert_not_called()
                    self.assertEqual(row, original)

    def test_current_list_uses_exact_membership_and_absent_list_is_empty(self):
        self.assertEqual(_app_identities({}), [])
        identities = _app_identities({'app_ids':['prefix-app-suffix', 'Original 原文']})
        self.assertNotIn('app', identities)
        self.assertIn('Original 原文', identities)
