"""用隔离 SQLite 验证来源准入、事务去重及多对多事件归属。"""
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from harness.app.superagent_inbox import SuperagentInbox
from harness.core.errors import HarnessError
from harness.core.language import current_language
from harness.i18n import tr


class SuperagentInboxTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / 'inbox.sqlite3'
        self.inbox = SuperagentInbox(self.path, instance_id='team')
        self.policy = dict(mode='requests', actors=['alice', 'bot'], threads=['issue/1', 'pr/2'])
        self.inbox.configure_source('github', **self.policy)
        self.body = dict(source_id='github', event_id='remote-1', actor_id='alice',
                         text='Original 原文 😀 /goal {"scope":"all"}', thread_ids=['issue/1'],
                         human=True, explicit=True)

    def receive(self, **changes):
        return self.inbox.receive(**{**self.body, **changes})

    def worker(self, task='fix', thread='issue/1', *, context=False, source='github'):
        self.inbox.register_task(task, session_id='conversation-' + task)
        self.inbox.subscribe(task, source_id=source, thread_id=thread, context_only=context)

    def assert_code(self, code, operation):
        with self.assertRaises(HarnessError) as error:
            operation()
        self.assertEqual(error.exception.code, code)
        return error.exception

    def test_unsubscribed_event_goes_to_router_without_executing_original_text(self):
        result = self.receive()
        self.assertEqual(result['disposition'], 'accepted')
        self.assertEqual(result['routes'], [{'kind': 'router', 'task_id': None, 'session_id': None, 'context_only': False}])
        event = self.inbox.event(source_id='github', event_id='remote-1')
        self.assertEqual(event['text'], self.body['text'])
        self.assertEqual(event['id'], result['id'])
        self.assertFalse(event['duplicate'])

    def test_multi_thread_fanout_deduplicates_workers_and_normal_subscription_wins(self):
        self.worker(context=True)
        self.inbox.subscribe('fix', source_id='github', thread_id='pr/2')
        self.worker('review', 'pr/2')
        result = self.receive(thread_ids=['pr/2', 'issue/1'])
        self.assertEqual([route['task_id'] for route in result['routes']], ['fix', 'review'])
        self.assertTrue(all(not route['context_only'] for route in result['routes']))
        self.assertTrue(all(route['session_id'] == 'conversation-' + route['task_id'] for route in result['routes']))
        duplicate = self.receive(thread_ids=['issue/1', 'pr/2'])
        self.assertTrue(duplicate['duplicate'])
        self.assertEqual(duplicate['routes'], result['routes'])

    def test_context_only_does_not_own_request_or_admit_unaddressed_followup(self):
        self.worker(context=True)
        result = self.receive()
        self.assertEqual([route['kind'] for route in result['routes']], ['router', 'worker'])
        self.assertTrue(result['routes'][1]['context_only'])
        ignored = self.receive(event_id='unaddressed', explicit=False)
        self.assertEqual(ignored['disposition'], 'request_required')
        self.assertEqual(ignored['routes'], [])

    def test_authorized_followup_reaches_existing_worker_without_another_mention(self):
        self.worker()
        result = self.receive(explicit=False)
        self.assertEqual(result['disposition'], 'accepted')
        self.assertEqual(result['routes'][0]['task_id'], 'fix')
        bot = self.receive(event_id='bot-event', actor_id='bot', human=False)
        self.assertEqual(bot['disposition'], 'request_required')

    def test_assigned_subscription_links_all_threads_without_changing_original_route(self):
        self.inbox.register_task('fix', session_id='conversation-fix')
        self.inbox.subscribe('fix', source_id='github', thread_id='issue/1', context_only=True)
        original = self.receive(thread_ids=['issue/1', 'pr/2'])
        self.assertTrue(self.inbox.subscribe_assigned('fix', source_id='github', event_id='remote-1', expected_revision=1))
        self.assertTrue(self.inbox.subscribed('fix', source_id='github', thread_ids=['issue/1', 'pr/2']))
        stored = self.inbox.event(source_id='github', event_id='remote-1')
        self.assertEqual(stored['routes'], original['routes'])
        followup = self.receive(event_id='followup', thread_ids=['pr/2'], explicit=False)
        self.assertEqual(followup['routes'][0]['task_id'], 'fix')

    def test_assigned_subscription_rechecks_revision_actor_scope_and_task_liveness(self):
        self.inbox.register_task('fix', session_id='conversation-fix')
        self.receive(thread_ids=['issue/1', 'pr/2'])
        self.inbox.configure_source('github', **{**self.policy, 'threads': ['issue/1']}, expected_revision=1)
        self.assertFalse(self.inbox.subscribe_assigned('fix', source_id='github', event_id='remote-1', expected_revision=1))
        self.assertFalse(self.inbox.subscribe_assigned('fix', source_id='github', event_id='remote-1', expected_revision=2))
        self.assertFalse(self.inbox.subscribed('fix', source_id='github', thread_ids=['issue/1']))
        self.inbox.configure_source('github', **self.policy, expected_revision=2)
        self.inbox.stop_task('fix')
        self.assertFalse(self.inbox.subscribe_assigned('fix', source_id='github', event_id='remote-1', expected_revision=3))
        self.assert_code('superagent/invalid', lambda: self.inbox.subscribe_assigned(
            'fix', source_id='github', event_id='remote-1', expected_revision=True))

    def test_candidates_and_context_followups_cannot_authorize_assigned_subscriptions(self):
        self.inbox.register_task('fix', session_id='conversation-fix')
        self.inbox.configure_source('github', **{**self.policy, 'mode': 'candidates'}, expected_revision=1)
        for index, changes in enumerate(({'human': False, 'actor_id': 'bot'}, {'explicit': False})):
            event_id = 'candidate-' + str(index)
            self.receive(event_id=event_id, **changes)
            self.assertFalse(self.inbox.subscribe_assigned('fix', source_id='github', event_id=event_id, expected_revision=2))
        self.assertFalse(self.inbox.subscribed('fix', source_id='github', thread_ids=['issue/1']))

    def test_candidate_policy_retains_bot_facts_without_turning_them_into_human_requests(self):
        self.inbox.configure_source('github', **{**self.policy, 'mode': 'candidates'}, expected_revision=1)
        result = self.receive(actor_id='bot', human=False, explicit=False)
        self.assertEqual(result['disposition'], 'accepted')
        self.assertEqual(result['policy_revision'], 2)
        event = self.inbox.events()['events'][0]
        self.assertFalse(event['human'])
        self.assertFalse(event['explicit'])

    def test_source_actor_and_every_associated_thread_are_checked_before_fanout(self):
        self.worker()
        denied_actor = self.receive(actor_id='outsider')
        denied_thread = self.receive(event_id='outside-thread', thread_ids=['issue/1', 'private'])
        self.assertEqual(denied_actor['disposition'], 'actor_denied')
        self.assertEqual(denied_thread['disposition'], 'thread_denied')
        self.assertEqual(denied_actor['routes'], denied_thread['routes'])
        self.assertEqual(denied_actor['routes'], [])
        self.inbox.configure_source('github', **{**self.policy, 'mode': 'ignore'}, expected_revision=1)
        self.assertEqual(self.receive(event_id='ignored')['disposition'], 'source_ignored')

    def test_policy_changes_do_not_replay_previously_filtered_events(self):
        first = self.receive(explicit=False)
        self.inbox.configure_source('github', **{**self.policy, 'mode': 'candidates'}, expected_revision=1)
        duplicate = self.receive(explicit=False)
        self.assertEqual(duplicate, {**first, 'duplicate': True})
        self.assertEqual(len(self.inbox.events()['events']), 1)

    def test_restart_returns_original_receipt_after_subscriptions_change(self):
        self.worker()
        original = self.receive()
        self.inbox.stop_task('fix')
        restarted = SuperagentInbox(self.path, instance_id='team')
        self.assertEqual(restarted.receive(**self.body), {**original, 'duplicate': True})
        self.assertEqual(self.receive(event_id='later')['routes'][0]['kind'], 'router')
        self.assertFalse(restarted.task('fix')['active'])
        self.assert_code('superagent/conflict', lambda: restarted.register_task('fix', session_id='conversation-fix'))

    def test_same_event_identity_with_different_content_never_overwrites_original(self):
        self.receive()
        before = self.inbox.events()
        for changes in ({'text': 'changed'}, {'actor_id': 'bot'}, {'thread_ids': ['pr/2']},
                        {'human': False}, {'explicit': False}):
            self.assert_code('superagent/event-conflict', lambda: self.receive(**changes))
        self.assertEqual(self.inbox.events(), before)

    def test_instance_and_source_namespaces_do_not_share_events_or_tasks(self):
        other = SuperagentInbox(self.path, instance_id='other-team')
        other.configure_source('github', **self.policy)
        self.inbox.configure_source('feishu', **self.policy)
        self.worker()
        one = self.receive()
        two = other.receive(**self.body)
        three = self.receive(source_id='feishu')
        self.assertEqual(len({one['id'], two['id'], three['id']}), 3)
        self.assertEqual(two['routes'][0]['kind'], 'router')
        self.assertEqual(three['routes'][0]['kind'], 'router')
        self.assertEqual(len(other.events()['events']), 1)
        self.assert_code('superagent/task-unavailable', lambda: other.task('fix'))

    def test_concurrent_reception_publishes_one_event_and_one_fanout_per_worker(self):
        self.worker()
        gate = threading.Barrier(4)

        def receive():
            inbox = SuperagentInbox(self.path, instance_id='team')
            gate.wait(timeout=5)
            return inbox.receive(**self.body)

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: receive(), range(4)))
        self.assertEqual(sum(not result['duplicate'] for result in results), 1)
        self.assertEqual(len({result['id'] for result in results}), 1)
        self.assertEqual(len(self.inbox.events()['events']), 1)
        self.assertTrue(all(len(result['routes']) == 1 for result in results))

    def test_concurrent_policy_edits_require_current_revision(self):
        gate = threading.Barrier(2)

        def save(mode):
            gate.wait(timeout=5)
            try:
                return self.inbox.configure_source('github', **{**self.policy, 'mode': mode}, expected_revision=1)
            except HarnessError as error:
                return error

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(save, ['ignore', 'candidates']))
        self.assertEqual(sum(isinstance(result, HarnessError) for result in results), 1)
        self.assertEqual(self.inbox.source('github')['revision'], 2)

    def test_unsubscribe_only_changes_its_own_task_and_keeps_history(self):
        self.worker()
        self.worker('review')
        old = self.receive()
        self.inbox.unsubscribe('fix', source_id='github', thread_id='issue/1')
        new = self.receive(event_id='next')
        self.assertEqual([route['task_id'] for route in new['routes']], ['review'])
        self.assertEqual(self.inbox.event(source_id='github', event_id='remote-1')['routes'], old['routes'])

    def test_task_binding_cannot_be_replaced_or_shared_with_another_task(self):
        self.inbox.register_task('fix', session_id='worker-context')
        self.assertEqual(self.inbox.register_task('fix', session_id='worker-context')['id'], 'fix')
        self.assert_code('superagent/conflict', lambda: self.inbox.register_task('fix', session_id='different'))
        self.assert_code('superagent/conflict', lambda: self.inbox.register_task('other', session_id='worker-context'))
        self.assert_code('superagent/thread-denied', lambda: self.inbox.subscribe('fix', source_id='github', thread_id='private'))

    def test_fanout_and_event_insert_roll_back_together(self):
        self.worker()
        failure = RuntimeError('receipt construction failed')
        with patch.object(self.inbox, '_receipt', side_effect=failure):
            with self.assertRaises(RuntimeError) as caught:
                self.receive()
            self.assertIs(caught.exception, failure)
        self.assertEqual(self.inbox.events()['events'], [])
        self.assertFalse(self.receive()['duplicate'])

    def test_pagination_and_returned_snapshots_are_isolated(self):
        for number in range(5):
            self.receive(event_id=f'event-{number}')
        after, identifiers = 0, []
        while True:
            page = self.inbox.events(after=after, limit=2)
            identifiers.extend(event['event_id'] for event in page['events'])
            if page['next_after'] is None:
                break
            self.assertGreater(page['next_after'], after)
            after = page['next_after']
        self.assertEqual(identifiers, [f'event-{number}' for number in range(5)])
        page['events'][0]['thread_ids'].append('injected')
        page['events'][0]['routes'].clear()
        policy = self.inbox.source('github')
        policy['actors'].append('injected')
        self.assertNotIn('injected', self.inbox.source('github')['actors'])
        self.assertEqual(self.inbox.events()['events'][0]['thread_ids'], ['issue/1'])
        self.assertEqual(len(self.inbox.events()['events'][0]['routes']), 1)

    def test_invalid_inputs_fail_before_acceptance_and_errors_follow_current_language(self):
        messages = []
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                for changes in ({'event_id': True}, {'text': '\ud800'}, {'text': '😀' * 16385},
                                {'thread_ids': []}, {'thread_ids': ['issue/1', 'issue/1']},
                                {'human': 1}, {'explicit': 'true'}, {'actor_id': ''}):
                    error = self.assert_code('superagent/invalid', lambda: self.receive(**changes))
                    self.assertIn(error.message, [tr('superagent.invalid', field=field)
                                                 for field in ('event_id', 'text', 'thread_ids', 'human/explicit', 'actor_id')])
                error = self.assert_code('superagent/source-unavailable', lambda: self.receive(source_id='missing'))
                self.assertEqual(error.message, tr('superagent.source-unavailable'))
                messages.append(error.message)
                self.assertEqual(self.inbox.events()['events'], [])
                for after, limit in ((True, 1), (0, True), (-1, 1), (0, 101), (0, 0)):
                    self.assert_code('superagent/invalid', lambda: self.inbox.events(after=after, limit=limit))
        self.assertEqual(messages[0], messages[2])
        self.assertNotEqual(messages[0], messages[1])

    def test_corrupt_policy_and_event_are_rejected_without_repair(self):
        self.receive()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('UPDATE sources SET actors=?', ('{"bad":"shape"}',))
            db.execute('UPDATE triggers SET threads=?', ('["issue/1","issue/1"]',))
        original = self.path.read_bytes()
        self.assert_code('superagent/corrupt', lambda: self.inbox.source('github'))
        self.assert_code('superagent/corrupt', lambda: self.inbox.configure_source(
            'github', **self.policy, expected_revision=1))
        self.assert_code('superagent/corrupt', lambda: self.inbox.events())
        self.assert_code('superagent/corrupt', lambda: self.receive())
        self.assertEqual(self.path.read_bytes(), original)

    def test_unknown_schema_is_preserved_without_adding_tables(self):
        path = self.path.with_name('different.sqlite3')
        with closing(sqlite3.connect(path)) as db, db:
            db.execute('CREATE TABLE user_data(value TEXT)')
            db.execute('INSERT INTO user_data VALUES(?)', ('original 原文',))
        original = path.read_bytes()
        self.assert_code('storage/schema', lambda: SuperagentInbox(path, instance_id='team'))
        self.assertEqual(path.read_bytes(), original)

    def test_empty_whitelists_deny_instead_of_meaning_allow_everyone(self):
        self.inbox.configure_source('github', mode='candidates', actors=[], threads=[], expected_revision=1)
        self.assertEqual(self.receive()['disposition'], 'actor_denied')

    def test_limit_rejection_is_atomic_and_can_be_explicitly_resubmitted(self):
        for number in range(129):
            self.worker(f'worker-{number}')
        self.assert_code('superagent/capacity', lambda: self.receive())
        self.assertEqual(self.inbox.events()['events'], [])
        self.inbox.stop_task('worker-128')
        self.assertEqual(len(self.receive()['routes']), 128)

    def test_filtered_noise_does_not_enumerate_or_hit_subscription_capacity(self):
        self.worker(context=True)
        with patch.object(self.inbox, '_targets', side_effect=AssertionError('filtered event enumerated workers')):
            for changes in ({'explicit': False}, {'human': False}):
                result = self.receive(event_id=str(changes), **changes)
                self.assertEqual(result['disposition'], 'request_required')
                self.assertEqual(result['routes'], [])

    def test_deep_corrupt_thread_json_returns_product_error_and_preserves_original(self):
        self.receive()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('UPDATE triggers SET threads=?', ('[' * 1500 + '0' + ']' * 1500,))
        original = self.path.read_bytes()
        self.assert_code('superagent/corrupt', self.inbox.events)
        self.assertEqual(self.path.read_bytes(), original)
