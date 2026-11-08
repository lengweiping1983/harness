"""Phase 5 只读查询的完整分页、范围、原件和生命周期回归。"""
from __future__ import annotations

import base64
import io
import json
from pathlib import Path
import tempfile
from threading import Event, Thread
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch

from harness.app.superagent_config import read_config
from harness.app.superagent_coordinator_host import CoordinatorHost
from harness.app.superagent_coordinator_store import CoordinatorStore
from harness.app.superagent_local import submit_local, query_local, mission_local
from harness.app.superagent_queries import CoordinatorQueries
from harness.app.superagent_state import open_state
from harness.cli import main
from harness.core.errors import HarnessError
from harness.core.language import current_language
from tests.superagent_product_fixture import product_config


class CoordinatorQueryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config, _ = product_config(self.root)
        submit_local(self.config, 'Original request', request_id='original')
        self.spec = read_config(self.config)
        self.writer = open_state(self.spec)
        self.reader = CoordinatorQueries(open_state(self.spec, read_only=True))

    def create_mission(self, name, *, plan=False):
        with self.writer.unit_of_work(actor={'kind':'test'}) as uow:
            ticket = uow.tickets.create({'ticket_id':'ticket-'+name, 'mandate_id':'mandate-'+name,
                'origin_trigger_id':'trigger-'+name, 'title':'Goal '+name})
            mission = uow.missions.create({'mission_id':'mission-'+name, 'ticket_id':ticket['ticket_id'],
                'sequence':1, 'repo':self.spec['profiles']['work']['repo'], 'base_commit':'a'*40})
            if plan:
                uow.plans.create(mission['mission_id'], expected_mission_revision=1,
                    plan={'nodes':[{'node_id':node, 'role':'work','profile_id':'work','dependencies':[]} for node in ('first','second')]},
                    acceptance={'kind':'artifact_review','criteria':[{'id':'files','kind':'inspection'}]},
                    system_profiles={'integrate':'integrator','review':'reviewer'}, created_by_kind='host',created_by_ref='fixture')
        return mission['mission_id']

    def test_pages_cover_history_once_and_exclude_insertions_after_the_first_watermark(self):
        expected = {self.create_mission(str(index)) for index in range(41)}
        page = self.reader.page(limit=7)
        seen = []
        self.create_mission('later')
        while True:
            self.assertEqual(page['total'], 41)
            self.assertEqual(page['instance_id'], self.spec['instance_id'])
            self.assertLessEqual(len(page['items']), 7)
            seen.extend(item['mission_id'] for item in page['items'])
            if not page['has_more']:
                self.assertIsNone(page['next_cursor'])
                break
            self.assertTrue(page['items'])
            page = self.reader.page(limit=7,cursor=page['next_cursor'])
        self.assertEqual(set(seen), expected)
        self.assertEqual(len(seen), 41)
        self.assertEqual(self.reader.page(limit=100)['total'], 42)

    def test_scope_and_projection_keep_other_missions_and_private_reports_out(self):
        target = self.create_mission('target',plan=True)
        other = self.create_mission('other',plan=True)
        page = self.reader.page('jobs',mission_id=target,limit=1)
        with self.writer.unit_of_work(actor={}) as uow:
            job = page['items'][0]
            uow.assignments.create({'assignment_id':'private-attempt','job_id':job['job_id'],
                'attempt_no':1,'submission_key':'private-key','report_json':{'private':'tool-secret-sentinel'}})
        assignments = self.reader.page('assignments',mission_id=target)
        self.assertEqual(len(assignments['items']), 1)
        self.assertEqual(assignments['items'][0]['mission_id'], target)
        self.assertNotIn('tool-secret-sentinel', json.dumps(assignments))
        self.assertNotIn('report_json', assignments['items'][0])
        detail = self.reader.mission(target)
        self.assertEqual(detail['counts'], {'jobs':4,'assignments':1,'artifacts':0,'reviews':0})
        self.assertEqual(detail['plan']['acceptance_json']['kind'], 'artifact_review')
        self.assertEqual(detail['control']['mode'], 'running')
        self.assertIsNone(detail['watchdog'])
        self.assertEqual(self.reader.page('assignments',mission_id=other)['items'], [])
        with self.assertRaises(HarnessError) as caught:
            self.reader.page('jobs',mission_id=other,cursor=page['next_cursor'])
        self.assertEqual(caught.exception.code, 'superagent/query.cursor')

    def test_cursors_reject_other_ledgers_resources_and_malformed_shapes(self):
        for index in range(3):
            self.create_mission(str(index),plan=True)
        token = self.reader.page(limit=1)['next_cursor']
        other_store = CoordinatorStore(self.root/'another.sqlite3', instance_id=self.writer.instance_id,
            identity=json.loads(self.writer.identity_json))
        with self.assertRaises(HarnessError):
            CoordinatorQueries(other_store).page(cursor=token)
        with self.assertRaises(HarnessError):
            self.reader.page('jobs',mission_id='mission-0',cursor=token)
        raw = json.loads(base64.urlsafe_b64decode(token))
        for value in ('?', '', 'x'*2049, base64.urlsafe_b64encode(b'{}').decode(),
                base64.urlsafe_b64encode(json.dumps({**raw,'fence':True}).encode()).decode()):
            with self.subTest(cursor=value[:30]), self.assertRaises(HarnessError):
                self.reader.page(cursor=value)
        for limit in (0,101,True,1.5):
            with self.subTest(limit=limit), self.assertRaises(HarnessError):
                self.reader.page(limit=limit)
        with self.assertRaises(HarnessError):
            self.reader.page('assignments')
        with self.assertRaises(HarnessError):
            self.reader.page('arbitrary-table')

    def test_detail_and_counts_keep_one_snapshot_while_another_connection_freezes_a_plan(self):
        target = self.create_mission('concurrent')
        store = open_state(self.spec,read_only=True)
        connect, events, errors = store._connect, [], []
        def connected():
            db = connect()
            def trace(sql):
                if not sql.startswith('SELECT count(*) FROM jobs'):
                    return
                db.set_trace_callback(None)
                try:
                    with self.writer.unit_of_work(actor={}) as uow:
                        uow.plans.create(target,expected_mission_revision=1,
                            plan={'nodes':[{'node_id':'read','role':'work','profile_id':'work','dependencies':[]}]},
                            acceptance={'kind':'single_worker_report','schema_version':1},created_by_kind='host',created_by_ref='fixture')
                    events.append(True)
                except BaseException as exc:
                    errors.append(exc)
            db.set_trace_callback(trace)
            return db
        store._connect = connected
        detail = CoordinatorQueries(store).mission(target)
        self.assertEqual(errors, [])
        self.assertEqual(events, [True])
        self.assertIsNone(detail['plan'])
        self.assertEqual(detail['counts']['jobs'], 0)
        self.assertEqual(self.reader.mission(target)['counts']['jobs'], 1)

    def test_corrupt_frozen_plan_is_refused_without_rewriting_its_original(self):
        target = self.create_mission('corrupt',plan=True)
        with self.writer.transaction(write=True) as db:
            db.execute('DROP TRIGGER mission_plans_immutable_update')
            db.execute("UPDATE mission_plans SET plan_json='{}' WHERE mission_id=?", (target,))
        with self.assertRaises(HarnessError) as caught:
            self.reader.mission(target)
        self.assertEqual(caught.exception.code, 'superagent/plan-corrupt')
        with self.writer.transaction() as db:
            self.assertEqual(db.execute('SELECT plan_json FROM mission_plans WHERE mission_id=?', (target,)).fetchone()[0], '{}')

    def test_new_cli_help_is_consumed_in_english_chinese_english(self):
        outputs = []
        for language in ('en','zh-CN','en'):
            output = io.StringIO()
            with redirect_stdout(output), self.assertRaises(SystemExit):
                main(['--language',language,'--help'])
            outputs.append(output.getvalue())
        self.assertIn('Read a page of Mission',outputs[0])
        self.assertIn('分页读取 Mission',outputs[1])
        self.assertEqual(outputs[0],outputs[2])

    def test_cli_queries_are_readonly_without_credentials_hosts_or_runtime_projection(self):
        target = self.create_mission('cli',plan=True)
        statements = []
        connect = CoordinatorStore._connect
        def traced(store):
            db = connect(store)
            db.set_trace_callback(statements.append)
            return db
        outputs = []
        with patch('harness.app.superagent_bootstrap.Harness',side_effect=AssertionError('查询不得创建宿主')), patch.object(CoordinatorStore,'_connect',traced):
            for args in (['--superagent-list','missions','--superagent-limit','1'], ['--superagent-show',target],
                    ['--superagent-list','jobs','--superagent-mission',target]):
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(main(['--superagent',str(self.config),*args]),0)
                outputs.append(json.loads(output.getvalue()))
        self.assertEqual(outputs[0]['items'][0]['mission_id'], target)
        self.assertEqual(outputs[1]['counts']['jobs'], 4)
        self.assertEqual(len(outputs[2]['items']), 4)
        self.assertFalse(any(sql.upper().startswith(('INSERT','UPDATE','DELETE','CREATE','ALTER','BEGIN IMMEDIATE')) for sql in statements))
        self.assertFalse((self.root/'state'/'runtime').exists())
        self.assertFalse((self.root/'state'/'runtime-status.json').exists())

    def test_missing_state_and_conflicting_cli_selectors_do_not_create_or_repair_data(self):
        empty = self.root/'empty.json'
        spec = json.loads(self.config.read_text()); spec['state_dir']=str(self.root/'missing')
        empty.write_text(json.dumps(spec))
        for query in (lambda:query_local(empty),lambda:mission_local(empty,'missing')):
            with self.assertRaises(HarnessError):
                query()
        for args in (['--superagent-list','jobs'], ['--superagent-cursor','unknown'],
                ['--superagent-show','missing','--superagent-once'], ['--superagent-list','missions','--superagent-limit','101'],
                ['--superagent-list','missions','--superagent-source','local']):
            with self.subTest(args=args), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                main(['--superagent',str(empty),*args])
        self.assertFalse((self.root/'missing').exists())

    def test_cached_host_queries_reject_after_close_and_hold_the_operation_lease(self):
        host = CoordinatorHost.open(self.root/'query-host',instance_id='queries',identity={})
        self.addCleanup(host.close)
        queries = host.queries
        self.assertEqual(queries.page()['items'], [])
        entered,release = Event(),Event()
        original = CoordinatorQueries.page
        def blocked(service,*args,**kwargs):
            entered.set()
            if not release.wait(5):
                raise TimeoutError('query fixture release')
            return original(service,*args,**kwargs)
        failures = []
        def read():
            try:
                queries.page()
            except BaseException as exc:
                failures.append(exc)
        with patch.object(CoordinatorQueries,'page',blocked):
            thread = Thread(target=read); thread.start()
            try:
                self.assertTrue(entered.wait(5))
                with self.assertRaises(HarnessError):
                    host.close()
            finally:
                release.set(); thread.join(5)
            self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])
        host.close()
        with self.assertRaises(HarnessError):
            queries.page()

    def test_same_query_instance_consumes_error_language_english_chinese_english(self):
        errors = []
        for language in ('en','zh-CN','en'):
            with current_language.set(language), self.assertRaises(HarnessError) as caught:
                self.reader.mission('absent')
            errors.append(str(caught.exception))
        self.assertEqual(errors[0],errors[2])
        self.assertNotEqual(errors[0],errors[1])
