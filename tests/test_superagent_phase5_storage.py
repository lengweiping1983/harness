"""工作台迁移保留原账本，失败回滚并强制固定操作回执。"""
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from harness.app.superagent_coordinator_store import CoordinatorStore,MIGRATIONS
from harness.app.superagent_operator import OperatorRequests


class Phase5StorageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name)/'superagent.sqlite3'

    def previous(self):
        from tests.test_superagent_coordinator_store import CoordinatorStoreTests
        return CoordinatorStoreTests._create_legacy_schema_with_effect(self,self.path,21)

    def test_v21_upgrade_preserves_original_effect_and_adds_empty_operator_media_tables(self):
        instance,identity,effect = self.previous()
        store = CoordinatorStore(self.path,instance_id=instance,identity=identity)
        with store.transaction() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],len(MIGRATIONS))
            self.assertEqual(db.execute('SELECT payload_json,state,generation FROM effects WHERE effect_id=?',(effect,)).fetchone()[:],
                ('{"legacy":true}','pending',0))
            for table in ('source_media','coordinator_operator_requests'):
                self.assertEqual(db.execute('SELECT count(*) FROM '+table).fetchone()[0],0)

    def test_conflicting_media_table_rolls_back_entire_new_migration_and_retains_original(self):
        instance,identity,effect = self.previous()
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('CREATE TABLE source_media(original TEXT)')
            db.execute("INSERT INTO source_media VALUES('preserve')"); db.commit()
        with self.assertRaises(Exception): CoordinatorStore(self.path,instance_id=instance,identity=identity)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],21)
            self.assertEqual(db.execute('SELECT original FROM source_media').fetchone()[0],'preserve')
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='coordinator_operator_requests'").fetchone())
            self.assertEqual(db.execute('SELECT state FROM effects WHERE effect_id=?',(effect,)).fetchone()[0],'pending')

    def test_completed_receipt_payload_and_history_cannot_be_overwritten_or_deleted(self):
        store=CoordinatorStore(self.path,instance_id='fixture',identity={})
        with store.transaction(write=True) as db:
            OperatorRequests.prepare(db,'request','policy',{'expected_revision':1})
            OperatorRequests.finish(db,'request',{'revision':2})
        for sql in ("UPDATE coordinator_operator_requests SET payload_json='{}'", "UPDATE coordinator_operator_requests SET state='pending'",
                "UPDATE coordinator_operator_requests SET receipt_json='{}'",'DELETE FROM coordinator_operator_requests'):
            with self.subTest(sql=sql),self.assertRaises(sqlite3.IntegrityError),store.transaction(write=True) as db:
                db.execute(sql)
        self.assertEqual(OperatorRequests.receipt(store,'request')['receipt'],{'revision':2})
