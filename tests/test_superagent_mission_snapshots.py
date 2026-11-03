"""目标账本批量快照的校验、隔离、并发一致性及读取开销回归。"""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from harness.app.superagent_inbox import SuperagentInbox
from harness.app.superagent_mission_store import MissionStore, digest, packed
from harness.core.errors import HarnessError


class MissionSnapshotTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.inbox = SuperagentInbox(self.root / 'inbox.sqlite3', instance_id='team')
        self.inbox.configure_source('local', mode='requests', actors=['operator'], threads=['project'])
        profiles = {'inspect': {'repo': str(self.root), 'description': 'Inspect source files.', 'sources': ['local'], 'tools': ['read']}}
        limits = {'max_steps': 8, 'max_attempts': 3, 'max_missions': 128, 'integrator_profile': 'inspect', 'reviewer_profile': 'inspect'}
        self.store = MissionStore(self.root / 'missions.sqlite3', self.inbox, identity='fixture', profiles=profiles, limits=limits)
        self.plan = {'steps': [{'id': 'step-' + str(index), 'profile_id': 'inspect', 'instruction': 'Inspect the input file.',
                    'depends_on': ['step-' + str(index - 1)] if index else []} for index in range(8)],
                     'acceptance': [{'id': 'inspection', 'criterion': 'Every file was inspected.', 'kind': 'inspection'}]}
        self.identity = self.add_mission('original')
        mission = self.store.mission(self.identity)
        self.jobs = [self.store.add_job(mission, role='work', node=step['id'], profile='inspect', dependencies=step['depends_on'])
                     for step in self.plan['steps']]

    def add_mission(self, event_id, *, state='running'):
        event = self.inbox.receive(source_id='local', event_id=event_id, actor_id='operator', text='Inspect all input files.',
                                   thread_ids=['project'], human=True, explicit=True)
        identity = self.store.mission_id(event['id'])
        with self.store.db(write=True) as db:
            db.execute('''INSERT INTO missions(id,instance,source_id,event_id,trigger_id,revision,state,round,plan,plan_hash,base_commit,repo,reply)
                VALUES(?,?,?,?,?,1,?,1,?,?,?,?,?)''', (identity, self.inbox.instance_id, 'local', event_id, event['id'], state,
                packed(self.plan), digest(packed(self.plan)), 'a' * 40, str(self.root), 'Recorded candidate.' if state == 'completed' else None))
        return identity

    def test_batch_read_checks_one_goal_and_original_event_without_repeating_connections(self):
        with patch.object(self.store, 'db', wraps=self.store.db) as database, \
                patch.object(self.inbox, 'event', wraps=self.inbox.event) as event:
            rows = self.store.jobs(self.identity)
        self.assertEqual(len(rows), 8)
        self.assertEqual(database.call_count, 1)
        self.assertEqual(event.call_count, 1)
        with patch.object(self.store, 'db', wraps=self.store.db) as database, \
                patch.object(self.inbox, 'event', wraps=self.inbox.event) as event:
            self.assertEqual(self.store.scope(self.jobs[0]['id'], 'inspect'), (self.identity, 'a' * 40))
        self.assertEqual((database.call_count, event.call_count), (1, 1))

    def test_snapshot_returns_independent_nested_objects_and_does_not_write(self):
        before = {path.name: path.read_bytes() for path in self.root.iterdir() if path.is_file()}
        mission, jobs = self.store.snapshot(self.identity)
        mission['plan']['steps'][0]['instruction'] = 'Changed only in the caller.'
        jobs[1]['dependencies'].clear()
        jobs[0]['data']['nested'] = {'changed': True}
        fresh_mission, fresh_jobs = self.store.snapshot(self.identity)
        self.assertEqual(fresh_mission['plan'], self.plan)
        self.assertEqual(fresh_jobs[1]['dependencies'], ['step-0'])
        self.assertEqual(fresh_jobs[0]['data'], {})
        self.store.status_snapshots()
        self.assertEqual({path.name: path.read_bytes() for path in self.root.iterdir() if path.is_file()}, before)

    def test_missing_goal_keeps_empty_job_list_but_snapshot_reports_unavailable(self):
        self.assertEqual(self.store.jobs('missing'), [])
        with self.assertRaises(HarnessError) as caught:
            self.store.snapshot('missing')
        self.assertEqual(caught.exception.code, 'superagent/event-unavailable')

    def test_each_batch_entry_retains_hash_json_profile_dependency_and_resource_validation(self):
        job_id = self.jobs[3]['id']
        with self.store.db() as db:
            original = dict(db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone())
        variants = []
        for field, value in (('profile', 'missing'), ('dependencies', []), ('attempt', True), ('resource', self.jobs[0]['id'])):
            spec = {**json.loads(original['spec']), field: value}
            variants.append({'spec': packed(spec), 'spec_hash': digest(packed(spec))})
        duplicate = original['spec'][:-1] + ',"role":"work"}'
        variants.extend([{'spec_hash': '0' * 64}, {'data_hash': '0' * 64},
                         {'spec': duplicate, 'spec_hash': digest(duplicate)}])
        for variant in variants:
            with self.subTest(variant=variant):
                with self.store.db(write=True) as db:
                    db.execute('UPDATE jobs SET ' + ','.join(key + '=?' for key in variant) + ' WHERE id=?', (*variant.values(), job_id))
                with self.assertRaises(HarnessError) as caught:
                    self.store.jobs(self.identity)
                self.assertEqual(caught.exception.code, 'superagent/corrupt')
                with self.store.db(write=True) as db:
                    db.execute('UPDATE jobs SET spec=?,spec_hash=?,data=?,data_hash=? WHERE id=?',
                        (original['spec'], original['spec_hash'], original['data'], original['data_hash'], job_id))

    def test_plan_and_original_event_are_revalidated_on_each_snapshot(self):
        changed = json.loads(packed(self.plan))
        changed['steps'][0]['depends_on'] = ['step-7']
        with self.store.db(write=True) as db:
            db.execute('UPDATE missions SET plan=?,plan_hash=? WHERE id=?', (packed(changed), digest(packed(changed)), self.identity))
        with self.assertRaises(HarnessError):
            self.store.snapshot(self.identity)
        with self.store.db(write=True) as db:
            db.execute('UPDATE missions SET plan=?,plan_hash=? WHERE id=?', (packed(self.plan), digest(packed(self.plan)), self.identity))
        self.store.snapshot(self.identity)
        with self.inbox._db(write=True) as db:
            db.execute('UPDATE triggers SET policy_revision=2 WHERE remote_id=?', ('original',))
        with self.assertRaises(HarnessError):
            self.store.snapshot(self.identity)

    def test_goal_and_jobs_share_one_read_transaction_during_concurrent_update(self):
        updated = threading.Event()
        errors = []
        writer = None
        original = self.store._decode_mission
        def write():
            try:
                with self.store.db(write=True) as db:
                    db.execute("UPDATE missions SET state='blocked',reason='fixture/changed' WHERE id=?", (self.identity,))
                    db.execute("UPDATE jobs SET state='blocked' WHERE mission=?", (self.identity,))
                    updated.set()
            except BaseException as exc:
                errors.append(exc)
                updated.set()
        def decode(row):
            nonlocal writer
            value = original(row)
            writer = threading.Thread(target=write)
            writer.start()
            if not updated.wait(3):
                raise TimeoutError('快照写入屏障未到达。')
            return value
        try:
            with patch.object(self.store, '_decode_mission', side_effect=decode):
                mission, jobs = self.store.snapshot(self.identity)
        finally:
            if writer is not None:
                writer.join(5)
                self.assertFalse(writer.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(mission['state'], 'running')
        self.assertTrue(all(job['state'] == 'pending' for job in jobs))
        mission, jobs = self.store.snapshot(self.identity)
        self.assertEqual(mission['state'], 'blocked')
        self.assertTrue(all(job['state'] == 'blocked' for job in jobs))

    def test_older_active_and_attention_goals_are_visible_ahead_of_new_completions(self):
        blocked = self.add_mission('blocked', state='blocked')
        for index in range(20):
            self.add_mission('completed-' + str(index), state='completed')
        _, counts, snapshots = self.store.status_snapshots()
        self.assertEqual(sum(counts.values()), 22)
        self.assertEqual(len(snapshots), 16)
        self.assertEqual([mission['id'] for mission, _ in snapshots[:2]], [self.identity, blocked])
        for invalid in (True, 0, 101, '16'):
            with self.assertRaises(HarnessError):
                self.store.status_snapshots(limit=invalid)

    def test_full_active_inventory_and_display_counts_share_one_snapshot(self):
        writing = threading.Event()
        errors = []
        writer = None
        original = self.store._read_snapshot
        def update():
            try:
                with self.store.db(write=True) as db:
                    db.execute("UPDATE missions SET state='blocked' WHERE id=?", (self.identity,))
                    writing.set()
            except BaseException as exc:
                errors.append(exc)
                writing.set()
        def snapshot(*args, **kwargs):
            nonlocal writer
            result = original(*args, **kwargs)
            writer = threading.Thread(target=update)
            writer.start()
            if not writing.wait(3):
                raise TimeoutError('活动目标快照屏障未到达。')
            return result
        try:
            with patch.object(self.store, '_read_snapshot', side_effect=snapshot):
                _, counts, visible, active = self.store.status_snapshots(include_active=True)
        finally:
            if writer is not None:
                writer.join(5)
                self.assertFalse(writer.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(counts, {'running': 1})
        self.assertEqual([mission['id'] for mission in active], [self.identity])
        self.assertEqual(visible[0][0]['state'], 'running')
        self.assertEqual(self.store.active_missions(), [])
        self.assertEqual(self.store.status_snapshots(include_active=True)[1], {'blocked': 1})
