"""自动化创建身份在事务中唯一，重复创建不覆盖已受理记录。"""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from harness.app.automations import AutomationService
from harness.core.errors import HarnessError
from harness.core.language import current_language
from harness.i18n import tr


class AutomationCreationIdentityTests(unittest.TestCase):
    def setUp(self):
        root = tempfile.TemporaryDirectory();self.addCleanup(root.cleanup)
        self.path = Path(root.name)/'automations.db'
        self.harness = SimpleNamespace(get_session=Mock())
        self.service = AutomationService(self.path, self.harness)
        self.body = {'creation_id': 'a'*32, 'name': 'Original 原文', 'prompt': 'Original prompt 原文',
                     'session_id': 'fixture', 'timezone': 'UTC', 'mode': 'session', 'enabled': True,
                     'schedule': {'kind': 'interval', 'seconds': 60}}

    def test_creation_requires_current_explicit_identity_before_any_write(self):
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                for invalid in (None, '', True, 'bad', 'A'*32, '../'+'a'*32):
                    body = {**self.body, 'creation_id': invalid}
                    if invalid is None:body.pop('creation_id')
                    with self.assertRaises(HarnessError) as error:self.service.save(body)
                    self.assertEqual(error.exception.code, 'automation/invalid')
                    self.assertEqual(error.exception.message, tr('ui.automations.creationIdentityInvalid'))
                self.assertEqual(self.service.list(), [])

    def test_duplicate_identity_after_restart_does_not_update_due_or_running_state(self):
        first = self.service.save(self.body)
        with self.service.db() as db:
            db.execute('UPDATE automations SET running=1,failures=2,last_error=? WHERE id=?', ('Original failure', first['id']))
        service = AutomationService(self.path, self.harness)
        before = service.list()
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                with self.assertRaises(HarnessError) as error:service.save({**self.body, 'name': 'Replacement'})
                self.assertEqual(error.exception.code, 'automation/create-exists')
                self.assertEqual(error.exception.message, tr('ui.automations.creationExists'))
                self.assertEqual(service.list(), before)
        self.assertEqual(first['id'], 'auto_'+'a'*32)

    def test_creation_identity_cannot_be_combined_with_update_identity(self):
        saved = self.service.save(self.body)
        with self.assertRaises(HarnessError):self.service.save({**self.body, 'id': saved['id']})
        updated = self.service.save({**{key:value for key,value in self.body.items() if key != 'creation_id'},
                                     'id': saved['id'], 'name': 'Updated 原文'})
        self.assertEqual(updated['name'], 'Updated 原文')
        self.assertEqual(len(self.service.list()), 1)

    def test_independent_creations_keep_distinct_identities(self):
        first = self.service.save(self.body)
        second = self.service.save({**self.body, 'creation_id': 'b'*32})
        self.assertNotEqual(first['id'], second['id'])
        self.assertEqual(len(self.service.list()), 2)
