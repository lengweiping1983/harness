"""设备分配、迁移保留和租约 SQL 围栏的定向回归。"""
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from harness.app.superagent_coordinator_store import CoordinatorStore,MIGRATIONS
from harness.app.superagent_remote_leases import RemoteLeases
from harness.core.errors import HarnessError


class RemoteMigrationTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.path=Path(temporary.name)/'coordinator.sqlite3'

    def previous(self):
        from tests.test_superagent_coordinator_store import CoordinatorStoreTests
        return CoordinatorStoreTests._create_legacy_schema_with_effect(self,self.path,22)

    def test_migration_preserves_v22_effects_and_creates_no_fake_devices_or_leases(self):
        instance,identity,effect=self.previous()
        store=CoordinatorStore(self.path,instance_id=instance,identity=identity)
        with store.transaction() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],len(MIGRATIONS))
            self.assertEqual(tuple(db.execute('SELECT payload_json,state,generation FROM effects WHERE effect_id=?',(effect,)).fetchone()),('{"legacy":true}','pending',0))
            self.assertEqual(db.execute('SELECT count(*) FROM remote_devices').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM remote_mission_leases').fetchone()[0],0)

    def test_conflicting_lease_table_preserves_original_and_rolls_back_device_migration(self):
        instance,identity,effect=self.previous()
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('CREATE TABLE remote_mission_leases(original TEXT)');db.execute("INSERT INTO remote_mission_leases VALUES('preserve')");db.commit()
        with self.assertRaises(Exception):CoordinatorStore(self.path,instance_id=instance,identity=identity)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],22)
            self.assertEqual(db.execute('SELECT original FROM remote_mission_leases').fetchone()[0],'preserve')
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='remote_devices'").fetchone())
            self.assertEqual(db.execute('SELECT state FROM effects WHERE effect_id=?',(effect,)).fetchone()[0],'pending')


class DeviceLeaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from tests.test_superagent_execution import SuperagentExecutionTests
        self.fixture=SuperagentExecutionTests();await self.fixture.asyncSetUp();self.addAsyncCleanup(self.fixture.asyncTearDown)
        self.devices=[{'device_id':key,'url':'http://127.0.0.1:'+str(port),'capacity':1} for key,port in [('a',18001),('b',18002)]]
        self.leases=RemoteLeases(self.fixture.host._store,self.devices,'a'*64,30)

    async def mission(self,name):
        trigger,accepted,_=self.fixture.accept_event(event_id=name,thread_key=name)
        result=await self.fixture.host.execution.route_trigger(accepted['ticket_id'],trigger['trigger_id'])
        return result['mission_id']

    async def test_two_devices_share_capacity_but_missions_never_move_after_expiry_or_restart(self):
        first,second=await self.mission('first'),await self.mission('second')
        one=self.leases.for_mission(first,allocate=True);two=self.leases.for_mission(second,allocate=True)
        self.assertEqual({one['device_id'],two['device_id']},{'a','b'})
        reopened=RemoteLeases(self.fixture.host._store,self.devices,'a'*64,30)
        self.assertEqual(reopened.for_mission(first),one)
        self.assertEqual(reopened.for_mission(second),two)
        from unittest.mock import patch
        with patch('harness.app.superagent_remote_leases.time.time',return_value=one['expires_at']+10):
            renewed=reopened.for_mission(first,renew=True)
        self.assertEqual(renewed['device_id'],one['device_id']);self.assertEqual(renewed['generation'],one['generation'])
        third=await self.mission('third')
        with self.assertRaises(HarnessError):self.leases.for_mission(third,allocate=True)
        with self.fixture.host._store.transaction(write=True) as db:
            for sql in ("UPDATE remote_mission_leases SET device_id='b' WHERE mission_id=?",'UPDATE remote_mission_leases SET generation=generation+1 WHERE mission_id=?','DELETE FROM remote_mission_leases WHERE mission_id=?'):
                with self.assertRaises(sqlite3.IntegrityError):db.execute(sql,(first,))
        self.leases.revoke(first)
        with self.assertRaises(HarnessError):reopened.for_mission(first,allocate=True,renew=True)
        self.assertEqual(reopened.for_mission(first,original_stop=True)['device_id'],one['device_id'])
        with self.assertRaises(HarnessError):RemoteLeases(self.fixture.host._store,self.devices,'b'*64,30)
