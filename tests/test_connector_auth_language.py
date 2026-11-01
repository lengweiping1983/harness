"""连接器自有鉴权错误保持资源身份，目录重读不绑定旧语言。"""
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from harness.connectors.manager import ConnectorManager
from harness.connectors.store import ConnectorRecord
from harness.core.errors import ConnectorError
from harness.core.language import current_language
from harness.i18n import tr


class ConnectorAuthLanguageTests(unittest.TestCase):
    def test_current_catalog_resolves_original_parameters_without_revalidation(self):
        for env in (False, True):
            for oauth in (False, True):
                manager = object.__new__(ConnectorManager)
                record = ConnectorRecord('original', transport='streamable-http', headers={'Authorization':'Bearer ${SECOND_TOKEN}${ORIGINAL_TOKEN}'} if env else {}, metadata={'oauth':{'scopes':[]}} if oauth else {})
                manager.store = Mock()
                manager.store.list.return_value = [record]
                manager.mcp = SimpleNamespace(supervisors=[])
                manager.credentials = Mock()
                manager.credentials.get.return_value = None
                manager._auth_errors = {}
                manager._oauth_lock = threading.RLock()
                manager._oauth_pending = {}
                manager._oauth_error = {}
                key = 'connector.auth.required' + ('Env' if env else '') + ('OAuth' if oauth else '')
                with current_language.set('en'), self.assertRaises(ConnectorError) as failure:
                    manager._validate_auth_header(record, SimpleNamespace(headers={'Authorization':'Bearer '}))
                self.assertEqual(failure.exception.code, 'connector/auth-required')
                before = record.to_dict()
                saved = manager._auth_errors.copy()
                for language in ('en','zh-CN','en'):
                    with current_language.set(language):
                        self.assertEqual(manager.catalog()[0]['last_error'], tr(key, name='original', variables=tr('ui.common.list_separator').join(('ORIGINAL_TOKEN', 'SECOND_TOKEN')) if env else ''))
                self.assertEqual(record.to_dict(), before)
                self.assertEqual(manager._auth_errors, saved)
                manager._validate_auth_header(record, SimpleNamespace(headers={'Authorization': 'Bearer valid'}))
                self.assertEqual(manager._auth_errors, {})
                self.assertEqual(manager.catalog()[0]['last_error'], '')
