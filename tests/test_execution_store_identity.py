"""执行器损坏正文不能覆盖权威身份，读取拒绝不修改原件。"""
import json
import tempfile
import unittest
from pathlib import Path

from harness.core.errors import HarnessError
from harness.core.language import current_language
from harness.execution.store import ExecutionStore
from harness.i18n import tr


class ExecutionStoreIdentityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = ExecutionStore(Path(temporary.name))
        self.addCleanup(self.store.close)
        self.session = self.store.open_session('principal', 'project', '用户标题', temporary.name)
        self.sid = self.session['session_id']
        self.original = self.store.db.execute('SELECT data FROM sessions WHERE id=?', (self.sid,)).fetchone()['data']

    def test_current_record_checkpoint_and_principal_scope(self):
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                self.assertEqual(self.store.session(self.sid, 'principal'), self.session)
                with self.assertRaises(HarnessError) as error:
                    self.store.session(self.sid, 'other')
                self.assertEqual(error.exception.code, 'execution/session-not-found')
        updated = self.store.checkpoint(self.sid, 'principal', 0, '用户摘要', ['下一步'])
        self.assertEqual(updated['revision'], 1)
        self.assertEqual(updated['session_id'], self.sid)
        self.assertEqual(updated['external_summary'], '用户摘要')
        self.assertEqual(updated['next_steps'], ['下一步'])

    def test_invalid_session_is_rejected_in_each_language_without_rewrite(self):
        base = json.loads(self.original)
        cases = ['{', 'null', '[]', json.dumps({})]
        for change in ({'session_id': 'other'}, {'project_id': 'other'}, {'revision': 99},
                       {'created_at': True}, {'created_at': float('nan')}, {'title': 3},
                       {'next_steps': [False]}, {'project_root': None}):
            cases.append(json.dumps({**base, **change}))
        for raw in cases:
            for language in ('en', 'zh-CN', 'en'):
                with self.subTest(raw=raw, language=language), current_language.set(language):
                    with self.store.db:
                        self.store.db.execute('UPDATE sessions SET data=? WHERE id=?', (raw, self.sid))
                    with self.assertRaises(HarnessError) as error:
                        self.store.session(self.sid, 'principal')
                    self.assertEqual(error.exception.code, 'execution/store-invalid')
                    self.assertEqual(error.exception.message, tr('execution.store.invalid_record'))
                    self.assertEqual(self.store.db.execute('SELECT data FROM sessions WHERE id=?', (self.sid,)).fetchone()['data'], raw)
                    with self.assertRaises(HarnessError):
                        self.store.checkpoint(self.sid, 'principal', 0, '禁止写入', [])
                    self.assertEqual(self.store.db.execute('SELECT revision FROM sessions WHERE id=?', (self.sid,)).fetchone()['revision'], 0)

    def test_evidence_identity_kind_and_scope_cannot_be_overridden(self):
        eid = self.store.evidence(self.sid, 'verification', {'passed': True, 'verification_id': 'check'})
        self.assertEqual(self.store.get_evidence(self.sid, eid), {'id': eid, 'kind': 'verification', 'passed': True, 'verification_id': 'check'})
        with self.assertRaises(HarnessError) as error:
            self.store.get_evidence('other', eid)
        self.assertEqual(error.exception.code, 'execution/evidence-not-found')
        for value in (None, [], {'id': 'other'}, {'kind': 'verification'}, {'session_id': 'other'}):
            raw = json.dumps(value)
            with self.store.db:
                self.store.db.execute('UPDATE evidence SET data=? WHERE id=?', (raw, eid))
            for language in ('en', 'zh-CN', 'en'):
                with self.subTest(value=value, language=language), current_language.set(language):
                    with self.assertRaises(HarnessError) as error:
                        self.store.get_evidence(self.sid, eid)
                    self.assertEqual(error.exception.code, 'execution/store-invalid')
                    self.assertEqual(error.exception.message, tr('execution.store.invalid_record'))
            self.assertEqual(self.store.db.execute('SELECT data FROM evidence WHERE id=?', (eid,)).fetchone()['data'], raw)
