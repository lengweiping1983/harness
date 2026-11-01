"""验证运行记录损坏时明确拒绝，不用构造默认值补成排队任务。"""
import json
import tempfile
import unittest
from pathlib import Path

from harness.core.run_storage import RunStorage
from harness.core.runs import ACTIVE_STATES, TERMINAL_STATES, AgentRun
from harness.subagent.archive import subagent_summary


class RunStorageValidationTests(unittest.TestCase):
    def test_missing_status_is_not_interpreted_as_a_new_queued_run(self):
        data = AgentRun(id='run', session_id='session').storage_dict()
        del data['status']
        with self.assertRaisesRegex(ValueError, 'run/invalid-status'):
            RunStorage.decode(json.dumps(data), expected_id='run')

    def test_unknown_or_malformed_status_is_rejected_in_full_record(self):
        for status in ('done', '', None, True, 1, [], {}):
            with self.subTest(status=status):
                data = AgentRun(id='run', session_id='session').storage_dict()
                data['status'] = status
                with self.assertRaisesRegex(ValueError, 'run/invalid-status'):
                    RunStorage.decode(json.dumps(data), expected_id='run')

    def test_summary_obeys_the_same_status_vocabulary(self):
        summary = subagent_summary(AgentRun(id='member', parent_session_id='parent'))
        for status in ('done', None, [], {}):
            with self.subTest(status=status):
                with self.assertRaisesRegex(ValueError, 'run/invalid-status'):
                    RunStorage.decode_summary(json.dumps({**summary, 'status': status}), expected_id='member')
        del summary['status']
        with self.assertRaisesRegex(ValueError, 'run/invalid-status'):
            RunStorage.decode_summary(json.dumps(summary), expected_id='member')

    def test_all_current_states_roundtrip_for_main_and_member(self):
        for status in ACTIVE_STATES | TERMINAL_STATES:
            with self.subTest(status=status):
                main = AgentRun(id='run', session_id='session', status=status)
                self.assertEqual(RunStorage.decode(json.dumps(main.storage_dict()), expected_id='run').status, status)
                member = AgentRun(id='member', parent_session_id='parent', status=status)
                summary = subagent_summary(member)
                self.assertEqual(RunStorage.decode_summary(json.dumps(summary), expected_id='member')['status'], status)

    def test_rejecting_corrupt_record_preserves_stored_data(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = RunStorage(Path(directory)/'runs.sqlite3')
            data = AgentRun(id='run', session_id='session').storage_dict()
            del data['status']
            raw = json.dumps(data)
            with storage.db() as db:
                db.execute('INSERT INTO runs VALUES(?,?,?)', ('run', raw, None))
            with storage.db() as db:
                original = db.execute('SELECT data FROM runs WHERE id=?', ('run',)).fetchone()[0]
                with self.assertRaisesRegex(ValueError, 'run/invalid-status'):
                    storage.decode(original, expected_id='run')
            with storage.db() as db:
                self.assertEqual(db.execute('SELECT data FROM runs WHERE id=?', ('run',)).fetchone()[0], raw)

    def test_duplicate_fields_never_select_the_last_value_as_authoritative(self):
        for decode, identity in ((RunStorage.decode, 'id'), (RunStorage.decode_summary, 'agent_id')):
            cases = [
                '{"'+identity+'":"foreign","'+identity+'":"run","status":"queued"}',
                '{"'+identity+'":"run","status":"failed","status":"queued"}',
                '{"'+identity+'":"run","status":"queued","prompt_input":{"member":"first","member":"second"}}',
            ]
            for raw in cases:
                with self.subTest(identity=identity, raw=raw):
                    with self.assertRaisesRegex(ValueError, 'run/duplicate-field'):
                        decode(raw, expected_id='run')

    def test_duplicate_fields_during_member_recovery_roll_back_all_writes(self):
        from harness.subagent.archive import SubagentArchive
        for column in ('data', 'summary'):
            with self.subTest(column=column), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'runs.sqlite3'
                storage = RunStorage(path)
                first = AgentRun(id='first', parent_session_id='parent', status='running')
                second = AgentRun(id='second', parent_session_id='parent', status='running')
                with storage.db() as db:
                    storage.write(db, first, summary=subagent_summary(first))
                    storage.write(db, second, summary=subagent_summary(second))
                    original_value = second.storage_dict() if column == 'data' else subagent_summary(second)
                    raw = json.dumps(original_value)[:-1] + ',"duplicate":{"member":"first","member":"second"}}'
                    db.execute(f'UPDATE runs SET {column}=? WHERE id=?', (raw, second.id))
                    original = db.execute('SELECT * FROM runs ORDER BY id').fetchall()
                with self.assertRaisesRegex(ValueError, 'run/duplicate-field'):
                    SubagentArchive(path)
                with storage.db() as db:
                    self.assertEqual(db.execute('SELECT * FROM runs ORDER BY id').fetchall(), original)
