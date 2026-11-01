"""隔离 Git 仓库上的自动创建、资源回查、权限继承及关闭边界。"""
import asyncio
import json
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from dataclasses import fields
from pathlib import Path
from unittest.mock import patch

from harness import Harness, HarnessConfig
from harness.app.git_process import run_git
from harness.app.superagent_dispatch import SuperagentDispatcher
from harness.app.superagent_inbox import SuperagentInbox
from harness.app.superagent_router import SuperagentRouter
from harness.app.superagent_workers import SuperagentWorkerProfile, SuperagentWorkers
from harness.core.errors import HarnessError
from harness.llm.types import ToolSchema, ToolUseBlock
from harness.tools.registry import ToolDefinition, ToolRunContext
from tests.agent_configuration_fixture import RecordingTransport
from tests.test_superagent_router import RouterTransport, batch_content


class SuperagentWorkersTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / 'source'
        self.repo.mkdir()
        self.git('init', '--initial-branch=main')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        (self.repo / 'main.txt').write_text('committed\n', encoding='utf-8')
        self.git('add', 'main.txt')
        self.git('commit', '-m', 'fixture')
        self.commit = self.git('rev-parse', 'HEAD')
        (self.repo / 'main.txt').write_text('user changes\n', encoding='utf-8')
        (self.repo / 'private.txt').write_text('local only\n', encoding='utf-8')
        self.transport, self.router_transport = RecordingTransport(), RouterTransport()
        self.host = self.new_host('workers', self.transport, worktrees=True)
        self.router_host = self.new_host('router', self.router_transport)
        self.router_session = self.router_host.create_session()
        self.inbox = SuperagentInbox(self.root / 'inbox.sqlite3', instance_id='team')
        self.inbox.configure_source('github', mode='requests', actors=['alice', 'bot'], threads=['issue/1', 'pr/2'])
        self.executed = []
        self.register_tools(self.host)
        self.factory = self.new_factory()
        self.dispatcher = SuperagentDispatcher(self.host, self.inbox)

    def git(self, *args):
        result = run_git(self.repo, *args)
        if result.returncode:
            raise RuntimeError(result.stderr.decode('utf-8'))
        return result.stdout.decode('utf-8').strip()

    def new_host(self, name, transport, *, worktrees=False):
        flags = {item.name: False for item in fields(HarnessConfig) if item.name.startswith('enable_')}
        flags['enable_worktrees'] = worktrees
        host = Harness(HarnessConfig(api_key='fixture-only', home=self.root / name, cwd=self.repo,
            model='fixture-model', language='en', max_steps=2, max_retries=0, tool_schema_mode='all', **flags),
            transport=transport, codex_gateway_home=self.root / (name + '-gateway'))
        self.addAsyncCleanup(host.close)
        return host

    def register_tools(self, host):
        async def inspect(args, ctx):
            self.executed.append(ctx.session_id)
            return 'inspected'
        for name in ('inspect', 'execute'):
            host.tools.register(ToolDefinition(ToolSchema(name=name, description=name,
                parameters={'type': 'object', 'properties': {}, 'additionalProperties': False}), inspect, effect='read'))

    def new_factory(self, **kwargs):
        profile = SuperagentWorkerProfile(self.repo, 'Fix project regressions', ('github',), ('inspect',), model='worker-model')
        return SuperagentWorkers(self.host, self.inbox, owner_session_id=self.router_session.id,
                                 profiles={'coding': profile}, **kwargs)

    def receive(self, event='one', **overrides):
        receipt = self.inbox.receive(**dict(dict(source_id='github', event_id=event, actor_id='alice',
            text='/goal source $skill', thread_ids=['issue/1'], human=True, explicit=True), **overrides))
        return {**{key: receipt[key] for key in ('id', 'source_id', 'event_id')}, 'revision': receipt['policy_revision']}

    async def ensure(self, item=None, *, batch='batch-one'):
        return await self.factory.ensure(batch_id=batch, item=item or self.item, profile_id='coding')

    async def finish(self, host):
        await asyncio.wait_for(asyncio.gather(*(record.task for record in tuple(host.runtime.records.active.values())
                                               if record.task is not None)), 5)

    def managed_router(self):
        return SuperagentRouter(self.dispatcher, self.router_host, session_id=self.router_session.id,
                                workers={}, worker_factory=self.factory)

    async def route_one(self, router, item, target):
        self.router_transport.answer = json.dumps({'assignments': [{'trigger_id': item['id'], 'task_id': target}]})
        await router.tick()
        await self.finish(self.router_host)
        status = await router.tick()
        await self.finish(self.host)
        return status

    async def initial_task(self):
        router = self.managed_router()
        item = self.receive(text='Investigate the parser regression using solution A.')
        await self.route_one(router, item, 'new:coding')
        task_id = next(iter(self.factory.ready_catalog(['github'])))
        return router, self.factory.worker(task_id)

    async def test_workspace_repository_identity_accepts_equivalent_paths_and_rejects_other_roots(self):
        _, row = await self.initial_task()
        record = self.factory._workspace(row)
        equivalent = {**record, 'repo': str(self.repo.parent) + '/./' + self.repo.name}
        with patch.object(self.host.worktrees, 'list', return_value=[equivalent]):
            self.assertEqual(self.factory._workspace(row)['id'], record['id'])
        other = self.root / 'different-repository'
        other.mkdir()
        with patch.object(self.host.worktrees, 'list', return_value=[{**record, 'repo': str(other)}]):
            with self.assertRaises(HarnessError) as failure:
                self.factory._workspace(row)
            self.assertEqual(failure.exception.code, 'superagent/conflict')

    async def test_router_reuses_created_worker_and_subscribes_new_thread_for_direct_followups(self):
        router, row = await self.initial_task()
        second = self.receive('review', thread_ids=['pr/2'], text='Continue the parser fix here; use solution B.')
        with patch.object(self.host.worktrees, 'create_team', side_effect=AssertionError('重复创建工作树')):
            status = await self.route_one(router, second, row['task_id'])
            followup = self.receive('followup', thread_ids=['pr/2'], explicit=False, text='Please include the empty input case.')
            await router.tick()
            await self.finish(self.host)
        self.assertEqual(status['inputs'], {'settled': 2})
        self.assertEqual(self.factory.status()['states'], {'ready': 1})
        self.assertEqual(len(self.host.sessions.list_sessions()), 1)
        self.assertEqual(len(self.router_transport.requests), 2)
        self.assertEqual(len(self.transport.requests), 3)
        self.assertEqual(self.inbox.event(source_id='github', event_id='followup')['routes'][0]['task_id'], row['task_id'])
        self.assertEqual(self.inbox.event(source_id='github', event_id='review')['routes'][0]['kind'], 'router')
        self.assertEqual(self.dispatcher.delivery(followup['id'], row['task_id'])['session_id'], row['session_id'])
        frame = json.loads(batch_content(self.router_transport.requests[1]).rsplit('\n\n', 1)[1])
        card = json.loads(frame['workers'][row['task_id']])
        self.assertIn('solution A', card['request_preview'])
        self.assertEqual(card['thread_ids'], ['issue/1'])
        self.assertIn('solution B', json.dumps(self.transport.requests[-1]['messages']))

    async def test_cross_source_continuation_keeps_original_session_workspace_and_scope(self):
        self.inbox.configure_source('im', mode='requests', actors=['alice'], threads=['topic/7'])
        await self.factory.aclose()
        profile = SuperagentWorkerProfile(self.repo, 'Fix project regressions', ('github', 'im'), ('inspect',), model='worker-model')
        self.factory = SuperagentWorkers(self.host, self.inbox, owner_session_id=self.router_session.id,
            profiles={'coding': profile}, path=self.root / 'cross-source.sqlite3')
        router, row = await self.initial_task()
        item = self.receive('im-change', source_id='im', thread_ids=['topic/7'], text='Continue that parser regression; use solution B.')
        await self.route_one(router, item, row['task_id'])
        next_item = self.receive('im-followup', source_id='im', thread_ids=['topic/7'], explicit=False)
        await router.tick()
        await self.finish(self.host)
        self.assertEqual(self.dispatcher.delivery(item['id'], row['task_id'])['session_id'], row['session_id'])
        self.assertEqual(self.dispatcher.delivery(next_item['id'], row['task_id'])['session_id'], row['session_id'])
        self.assertEqual(self.factory.worker(row['task_id']), row)
        self.assertEqual(len(self.host.worktrees.list()), 1)
        self.assertEqual(self.host._agent_for(self.host.get_session(row['session_id'])).tools.names(), ['inspect'])

    async def test_roster_scope_cannot_be_used_to_attach_another_events_disallowed_source(self):
        router, row = await self.initial_task()
        self.inbox.configure_source('im', mode='requests', actors=['alice'], threads=['topic/7'])
        allowed = self.receive('review', thread_ids=['pr/2'])
        denied = self.receive('im-change', source_id='im', thread_ids=['topic/7'])
        self.router_transport.answer = json.dumps({'assignments': [
            {'trigger_id': item['id'], 'task_id': row['task_id']} for item in (allowed, denied)]})
        await router.tick()
        await self.finish(self.router_host)
        status = await router.tick()
        await self.finish(self.host)
        self.assertEqual(status['inputs'], {'settled': 2, 'skipped': 1})
        self.assertFalse(self.inbox.subscribed(row['task_id'], source_id='im', thread_ids=['topic/7']))
        self.assertEqual(len(self.transport.requests), 2)

    async def test_later_worker_creation_does_not_change_frozen_roster(self):
        router, row = await self.initial_task()
        self.inbox.configure_source('github', mode='requests', actors=['alice'],
            threads=['issue/1', 'pr/2', 'issue/3'], expected_revision=1)
        item = self.receive('review', thread_ids=['pr/2'])
        self.router_transport.answer = json.dumps({'assignments': [{'trigger_id': item['id'], 'task_id': row['task_id']}]})
        self.router_transport.entered.clear()
        self.router_transport.release.clear()
        await router.tick()
        await asyncio.wait_for(self.router_transport.entered.wait(), 2)
        late = self.receive('late', thread_ids=['issue/3'])
        created = await self.ensure(late, batch='late-batch')
        self.router_transport.release.set()
        await self.finish(self.router_host)
        await router._poll()
        await router._drain()
        await self.finish(self.host)
        self.assertEqual(router.status()['inputs'], {'settled': 2})
        frozen = json.loads(batch_content(self.router_transport.requests[-1]).rsplit('\n\n', 1)[1])
        self.assertNotIn(created['task_id'], frozen['workers'])
        self.assertEqual(self.dispatcher.delivery(item['id'], row['task_id'])['session_id'], row['session_id'])

    async def test_worker_created_after_collection_cannot_be_selected_by_that_batch(self):
        router, _ = await self.initial_task()
        self.inbox.configure_source('github', mode='requests', actors=['alice'],
            threads=['issue/1', 'pr/2', 'issue/3'], expected_revision=1)
        item = self.receive('review', thread_ids=['pr/2'])
        late = self.receive('late', thread_ids=['issue/3'])
        # 仅收集 review；late 留在后续游标，不能反向扩展已提交名单。
        router.batch_size = 1
        self.router_transport.answer = json.dumps({'assignments': [
            {'trigger_id': item['id'], 'task_id': self.factory.task_id('late-batch', late['id'])}]})
        self.router_transport.release.clear()
        self.router_transport.entered.clear()
        await router.tick()
        await asyncio.wait_for(self.router_transport.entered.wait(), 2)
        await self.ensure(late, batch='late-batch')
        self.router_transport.release.set()
        await self.finish(self.router_host)
        await router._poll()
        self.assertEqual(router.status()['inputs'], {'settled': 1, 'rejected': 1})
        self.assertEqual(len(self.transport.requests), 1)

    async def test_link_lost_return_reconciles_without_reasserting_later_unsubscribe(self):
        _, row = await self.initial_task()
        item = self.receive('review', thread_ids=['pr/2'])
        subscribe = self.inbox.subscribe_assigned
        original = OSError('subscription return lost')
        def lost(*args, **kwargs):
            subscribe(*args, **kwargs)
            raise original
        args = dict(source_id='github', event_id=item['event_id'], revision=item['revision'])
        with patch.object(self.inbox, 'subscribe_assigned', side_effect=lost) as attempted:
            with self.assertRaises(OSError) as caught:
                self.factory.attach(row['task_id'], **args)
            self.assertIs(caught.exception, original)
            self.inbox.unsubscribe(row['task_id'], source_id='github', thread_id='pr/2')
            self.assertEqual(self.factory.attach(row['task_id'], **args), 'applied')
            self.assertEqual(attempted.call_count, 1)
        self.assertEqual(self.receive('later', thread_ids=['pr/2'], explicit=False)['revision'], 1)
        self.assertEqual(self.inbox.event(source_id='github', event_id='later')['disposition'], 'request_required')

    async def test_unknown_link_with_removed_subscription_is_never_replayed_after_reopen(self):
        _, row = await self.initial_task()
        item = self.receive('review', thread_ids=['pr/2'])
        subscribe = self.inbox.subscribe_assigned
        def lost_and_removed(*args, **kwargs):
            subscribe(*args, **kwargs)
            self.inbox.unsubscribe(row['task_id'], source_id='github', thread_id='pr/2')
            raise OSError('unconfirmed subscription')
        args = dict(source_id='github', event_id=item['event_id'], revision=item['revision'])
        with patch.object(self.inbox, 'subscribe_assigned', side_effect=lost_and_removed):
            with self.assertRaises(OSError):
                self.factory.attach(row['task_id'], **args)
        await self.factory.aclose()
        self.factory = self.new_factory()
        with patch.object(self.inbox, 'subscribe_assigned', side_effect=AssertionError('重放订阅')):
            self.assertEqual(self.factory.attach(row['task_id'], **args), 'unknown')
        self.assertFalse(self.inbox.subscribed(row['task_id'], source_id='github', thread_ids=['pr/2']))

    async def test_link_local_receipt_failure_retains_error_and_confirms_original_subscription(self):
        _, row = await self.initial_task()
        item = self.receive('review', thread_ids=['pr/2'])
        settle = self.factory.bindings._settle
        original = OSError('binding receipt failed')
        failed = False
        def fail_once(key, state):
            nonlocal failed
            if state == 'applied' and not failed:
                failed = True
                raise original
            return settle(key, state)
        args = dict(source_id='github', event_id='review', revision=item['revision'])
        with patch.object(self.factory.bindings, '_settle', side_effect=fail_once):
            with self.assertRaises(OSError) as caught:
                self.factory.attach(row['task_id'], **args)
            self.assertIs(caught.exception, original)
        await self.factory.aclose()
        self.factory = self.new_factory()
        with patch.object(self.inbox, 'subscribe_assigned', side_effect=AssertionError('重放订阅')):
            self.assertEqual(self.factory.attach(row['task_id'], **args), 'applied')
        self.assertTrue(self.inbox.subscribed(row['task_id'], source_id='github', thread_ids=['pr/2']))

    async def test_claimed_link_without_subscription_evidence_never_retries_write(self):
        _, row = await self.initial_task()
        item = self.receive('review', thread_ids=['pr/2'])
        args = dict(source_id='github', event_id='review', revision=item['revision'])
        with patch.object(self.inbox, 'subscribe_assigned', side_effect=OSError('outcome unknown')) as attempted:
            with self.assertRaises(OSError):
                self.factory.attach(row['task_id'], **args)
            self.assertEqual(self.factory.attach(row['task_id'], **args), 'unknown')
            self.assertEqual(attempted.call_count, 1)
        self.assertFalse(self.inbox.subscribed(row['task_id'], source_id='github', thread_ids=['pr/2']))

    async def test_corrupt_binding_is_preserved_and_does_not_repeat_subscription(self):
        _, row = await self.initial_task()
        item = self.receive('review', thread_ids=['pr/2'])
        args = dict(source_id='github', event_id='review', revision=item['revision'])
        self.assertEqual(self.factory.attach(row['task_id'], **args), 'applied')
        with closing(sqlite3.connect(self.factory.bindings.path)) as db, db:
            db.execute('UPDATE bindings SET revision=0')
        with patch.object(self.inbox, 'subscribe_assigned', side_effect=AssertionError('重放订阅')):
            with self.assertRaises(HarnessError) as caught:
                self.factory.attach(row['task_id'], **args)
        self.assertEqual(caught.exception.code, 'superagent/corrupt')
        with closing(sqlite3.connect(self.factory.bindings.path)) as db:
            self.assertEqual(db.execute('SELECT revision FROM bindings').fetchone()[0], 0)

    async def test_roster_hides_revoked_origins_and_stopped_tasks_with_bounded_source_preview(self):
        self.item = self.receive(text='界' * 600)
        row = await self.ensure()
        card = json.loads(self.factory.ready_catalog(['github'])[row['task_id']])
        self.assertEqual(len(card['request_preview']), 512)
        self.assertTrue(card['request_truncated'])
        self.assertEqual(card['request_chars'], 600)
        self.assertEqual(self.factory.ready_catalog(['unrelated']), {})
        self.inbox.configure_source('github', mode='ignore', actors=['alice'], threads=['issue/1', 'pr/2'], expected_revision=1)
        self.assertEqual(self.factory.ready_catalog(['github']), {})
        self.inbox.configure_source('github', mode='requests', actors=['alice'], threads=['issue/1', 'pr/2'], expected_revision=2)
        self.inbox.stop_task(row['task_id'])
        self.assertEqual(self.factory.ready_catalog(['github']), {})
        second = self.receive('review', thread_ids=['pr/2'])
        self.assertEqual(self.factory.attach(row['task_id'], source_id='github', event_id='review', revision=second['revision']), 'skipped')

    async def test_link_transaction_rechecks_policy_after_session_resource_validation(self):
        _, row = await self.initial_task()
        item = self.receive('review', thread_ids=['pr/2'])
        session = self.factory._session
        def revoke(value):
            result = session(value)
            self.inbox.configure_source('github', mode='ignore', actors=['alice'],
                threads=['issue/1', 'pr/2'], expected_revision=1)
            return result
        with patch.object(self.factory, '_session', side_effect=revoke):
            state = self.factory.attach(row['task_id'], source_id='github', event_id='review', revision=item['revision'])
        self.assertEqual(state, 'skipped')
        self.assertFalse(self.inbox.subscribed(row['task_id'], source_id='github', thread_ids=['pr/2']))

    async def test_reuse_prompt_is_consumed_in_english_chinese_english(self):
        router, row = await self.initial_task()
        self.inbox.configure_source('github', mode='requests', actors=['alice'],
            threads=['issue/1', 'pr/2', 'issue/3', 'issue/4'], expected_revision=1)
        for index, (language, phrase) in enumerate((('en', 'Reuse an existing task'), ('zh-CN', '优先复用已有任务'), ('en', 'Reuse an existing task'))):
            self.router_host.config.language = language
            item = self.receive('language-' + str(index), thread_ids=[['pr/2', 'issue/3', 'issue/4'][index]])
            await self.route_one(router, item, row['task_id'])
            self.assertIn(phrase, batch_content(self.router_transport.requests[-1]))
        self.assertEqual(self.factory.status()['states'], {'ready': 1})

    async def test_creates_independent_committed_worktree_conversation_and_subscription(self):
        self.item = self.receive(thread_ids=['issue/1', 'pr/2'])
        row = await self.ensure()
        self.assertEqual(row['state'], 'ready')
        session = self.host.get_session(row['session_id'])
        directory = Path(session.header.cwd)
        self.assertNotEqual(directory, self.repo)
        self.assertEqual((directory / 'main.txt').read_text(encoding='utf-8'), 'committed\n')
        self.assertFalse((directory / 'private.txt').exists())
        self.assertEqual((self.repo / 'main.txt').read_text(encoding='utf-8'), 'user changes\n')
        self.assertEqual((self.repo / 'private.txt').read_text(encoding='utf-8'), 'local only\n')
        self.assertEqual(session.header.metadata['model'], 'worker-model')
        self.assertEqual(self.host._agent_for(session).tools.names(), ['inspect'])
        followup = self.inbox.receive(source_id='github', event_id='two', actor_id='alice', text='follow up',
            thread_ids=['issue/1', 'pr/2'], human=True, explicit=False)
        self.assertEqual([route['task_id'] for route in followup['routes']], [row['task_id']])
        await self.dispatcher.dispatch(source_id='github', event_id='two')
        await self.finish(self.host)
        self.assertEqual(len(self.transport.requests), 1)
        self.assertEqual(self.transport.requests[0]['model'], 'worker-model')
        self.assertEqual([tool['function']['name'] for tool in self.transport.requests[0]['tools']], ['inspect'])

    async def test_router_creates_workers_for_two_events_and_followups_bypass_inference(self):
        first = self.receive()
        second = self.receive('two')
        self.router_transport.answer = json.dumps({'assignments': [
            {'trigger_id': item['id'], 'task_id': 'new:coding'} for item in (first, second)]})
        router = SuperagentRouter(self.dispatcher, self.router_host, session_id=self.router_session.id,
                                 workers={}, worker_factory=self.factory)
        await router.tick()
        await self.finish(self.router_host)
        status = await router.tick()
        await self.finish(self.host)
        self.assertEqual(status['inputs'], {'settled': 2})
        rows = [self.factory._get(self.factory.task_id(status['batches'][0]['id'], item['id'])) for item in (first, second)]
        self.assertNotEqual(rows[0]['session_id'], rows[1]['session_id'])
        self.assertNotEqual(rows[0]['workspace_id'], rows[1]['workspace_id'])
        self.inbox.receive(source_id='github', event_id='three', actor_id='alice', text='recheck',
            thread_ids=['issue/1'], human=True, explicit=False)
        await router.tick()
        await self.finish(self.host)
        self.assertEqual(len(self.router_transport.requests), 1)
        self.assertEqual(len(self.transport.requests), 4)
        self.assertEqual(len(self.host.worktrees.list()), 2)

    async def test_duplicate_and_concurrent_requests_create_once(self):
        self.item = self.receive()
        with patch.object(self.host.worktrees, 'create_team', wraps=self.host.worktrees.create_team) as create:
            one, two = await asyncio.gather(self.ensure(), self.ensure())
            self.assertEqual(one, two)
            self.assertEqual(await self.ensure(), one)
            self.assertEqual(create.call_count, 1)
        self.assertEqual(len(self.host.sessions.list_sessions()), 1)

    async def test_workspace_return_lost_recovers_exact_resource_without_recreation(self):
        self.item = self.receive()
        create = self.host.worktrees.create_team
        original = OSError('workspace return lost')
        def lost(*args, **kwargs):
            create(*args, **kwargs)
            raise original
        with patch.object(self.host.worktrees, 'create_team', side_effect=lost) as attempted:
            with self.assertRaises(OSError) as caught:
                await self.ensure()
            self.assertIs(caught.exception, original)
            self.assertEqual((await self.ensure())['state'], 'ready')
            self.assertEqual(attempted.call_count, 1)

    async def test_unknown_workspace_creation_is_not_replayed(self):
        self.item = self.receive()
        with patch.object(self.host.worktrees, 'create_team', side_effect=OSError('unknown creation')) as create:
            with self.assertRaises(OSError):
                await self.ensure()
            self.assertEqual((await self.ensure())['state'], 'unknown')
            self.assertEqual(create.call_count, 1)
        self.assertEqual(self.host.sessions.list_sessions(), [])

    async def test_session_return_lost_is_recovered_by_initial_metadata(self):
        self.item = self.receive()
        create = self.host.create_session
        original = OSError('session return lost')
        def lost(*args, **kwargs):
            create(*args, **kwargs)
            raise original
        with patch.object(self.host, 'create_session', side_effect=lost) as attempted:
            with self.assertRaises(OSError) as caught:
                await self.ensure()
            self.assertIs(caught.exception, original)
            self.assertEqual((await self.ensure())['state'], 'ready')
            self.assertEqual(attempted.call_count, 1)
        self.assertEqual(len(self.host.sessions.list_sessions()), 1)

    async def test_unknown_session_creation_does_not_create_second_conversation(self):
        self.item = self.receive()
        with patch.object(self.host, 'create_session', side_effect=OSError('unknown conversation')) as create:
            with self.assertRaises(OSError):
                await self.ensure()
            self.assertEqual((await self.ensure())['state'], 'unknown')
            self.assertEqual(create.call_count, 1)
        self.assertEqual(len(self.host.worktrees.list()), 1)

    async def test_restart_without_factory_fails_closed_and_rebinding_restores_same_scope(self):
        self.item = self.receive()
        row = await self.ensure()
        await self.host.close()
        self.host = self.new_host('workers', self.transport, worktrees=True)
        self.register_tools(self.host)
        session = self.host.get_session(row['session_id'])
        with self.assertRaises(HarnessError):
            self.host._agent_for(session)
        self.factory = self.new_factory()
        with patch.object(self.host.worktrees, 'create_team', side_effect=AssertionError('重复创建工作树')):
            self.assertEqual(await self.ensure(), row)
        agent = self.host._agent_for(session)
        result = await agent.tools.execute_call(ToolUseBlock(id='forbidden', name='execute', input={}),
            ToolRunContext('forbidden', session.id, session.header.cwd, 1, 1))
        self.assertTrue(result.is_error)
        self.assertEqual(self.executed, [])
        self.assertEqual(agent.tools.names(), ['inspect'])
        ordinary = self.host.create_session()
        self.assertIn('execute', self.host._agent_for(ordinary).tools.names())

    async def test_revoked_source_during_workspace_creation_retains_resource_without_conversation(self):
        self.item = self.receive()
        create = self.host.worktrees.create_team
        def revoked(*args, **kwargs):
            result = create(*args, **kwargs)
            self.inbox.configure_source('github', mode='ignore', actors=['alice'], threads=['issue/1'], expected_revision=1)
            return result
        with patch.object(self.host.worktrees, 'create_team', side_effect=revoked):
            row = await self.ensure()
        self.assertEqual(row['state'], 'skipped')
        self.assertEqual(len(self.host.worktrees.list()), 1)
        self.assertEqual(self.host.sessions.list_sessions(), [])

    async def test_close_waits_for_owned_git_write_and_prevents_late_conversation(self):
        self.item = self.receive()
        entered, release = threading.Event(), threading.Event()
        create = self.host.worktrees.create_team
        def gated(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise TimeoutError('创建屏障未释放。')
            return create(*args, **kwargs)
        with patch.object(self.host.worktrees, 'create_team', side_effect=gated):
            operation = asyncio.create_task(self.ensure())
            await asyncio.wait_for(asyncio.to_thread(entered.wait), 2)
            closing_host = asyncio.create_task(self.host.close())
            await asyncio.sleep(0)
            self.assertFalse(closing_host.done())
            release.set()
            await asyncio.wait_for(asyncio.gather(operation, closing_host), 5)
        self.assertEqual(len(self.host.worktrees.list()), 1)
        self.assertEqual(self.host.sessions.list_sessions(), [])

    async def test_cancellation_does_not_abandon_creation_or_replay(self):
        self.item = self.receive()
        entered, release = threading.Event(), threading.Event()
        create = self.host.worktrees.create_team
        def gated(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise TimeoutError('创建屏障未释放。')
            return create(*args, **kwargs)
        with patch.object(self.host.worktrees, 'create_team', side_effect=gated) as attempted:
            operation = asyncio.create_task(self.ensure())
            await asyncio.wait_for(asyncio.to_thread(entered.wait), 2)
            operation.cancel()
            await asyncio.sleep(0)
            self.assertFalse(operation.done())
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(operation, 5)
            self.assertEqual((await self.ensure())['state'], 'ready')
            self.assertEqual(attempted.call_count, 1)

    async def test_bad_profile_and_nonrequest_events_do_not_create_resources(self):
        self.item = self.receive(actor_id='bot', human=False)
        self.assertEqual((await self.ensure())['state'], 'skipped')
        self.assertEqual(self.host.worktrees.list(), [])
        await self.factory.aclose()
        self.host.config.enable_subagent = True
        with self.assertRaises(HarnessError):
            self.new_factory()

    async def test_managed_conversation_rejects_host_commands_before_external_routing(self):
        self.item = self.receive()
        row = await self.ensure()
        from unittest.mock import AsyncMock
        with patch.object(self.host, '_apply_slash_command', new=AsyncMock(side_effect=AssertionError('宿主命令被执行'))) as slash:
            for command in ('/runner remote', '/agent builder', '/skill coding', '/goal goal', '/plan task'):
                with self.subTest(command=command):
                    with self.assertRaises(HarnessError) as caught:
                        await self.host.run_async(row['session_id'], command)
                    self.assertEqual(caught.exception.code, 'superagent/worker-host-command')
            slash.assert_not_awaited()
        self.assertEqual(self.transport.requests, [])

    async def test_creation_limit_retains_existing_worker_and_allows_duplicate_read(self):
        await self.factory.aclose()
        self.factory = self.new_factory(max_workers=1, path=self.root / 'limited-workers.sqlite3')
        self.item = self.receive()
        first = await self.ensure()
        self.assertEqual(await self.ensure(), first)
        self.inbox.configure_source('github', mode='requests', actors=['alice'], threads=['issue/1', 'pr/2'], expected_revision=1)
        second = self.receive('two-new', thread_ids=['pr/2'])
        with self.assertRaises(HarnessError) as caught:
            await self.ensure(second, batch='batch-two')
        self.assertEqual(caught.exception.code, 'superagent/worker-capacity')
        self.assertEqual(self.factory.worker(first['task_id']), first)
        self.assertEqual(self.factory.status()['states'], {'ready': 1})
        self.assertEqual(len(self.host.worktrees.list()), 1)

    async def test_same_profile_new_tasks_preserve_english_chinese_english_titles(self):
        titles = []
        threads = ['issue/1', 'pr/2', 'issue/3']
        self.inbox.configure_source('github', mode='requests', actors=['alice'], threads=threads, expected_revision=1)
        for index, language in enumerate(('en', 'zh-CN', 'en')):
            self.host.config.language = language
            item = self.receive(str(index), thread_ids=[threads[index]])
            row = await self.ensure(item, batch='batch-' + str(index))
            titles.append(self.host.get_session(row['session_id']).header.title)
        self.assertTrue(titles[0].startswith('Superagent task '))
        self.assertTrue(titles[1].startswith('Superagent 任务 '))
        self.assertTrue(titles[2].startswith('Superagent task '))

    async def test_corrupt_or_reassigned_metadata_does_not_broaden_tools(self):
        self.item = self.receive()
        row = await self.ensure()
        self.host.sessions.update_header(row['session_id'], metadata={'superagent_worker': {'task_id': row['task_id']}})
        with self.assertRaises(HarnessError):
            await self.host.run_async(row['session_id'], 'do work')
        self.assertEqual(self.transport.requests, [])

    async def test_missing_workspace_or_changed_model_blocks_managed_execution(self):
        self.item = self.receive()
        row = await self.ensure()
        session = self.host.get_session(row['session_id'])
        self.host.sessions.update_header(session.id, metadata={'model': 'other-model'})
        with self.assertRaises(HarnessError):
            await self.host.run_async(session.id, 'do work')
        self.host.sessions.update_header(session.id, metadata={'model': 'worker-model'})
        with patch.object(self.host.worktrees, 'list', return_value=[]):
            with self.assertRaises(HarnessError):
                await self.host.run_async(session.id, 'do work')
        self.assertEqual(self.transport.requests, [])

    async def test_actual_worker_rejects_hallucinated_tool_outside_profile(self):
        self.item = self.receive()
        row = await self.ensure()
        stream = self.transport.post_stream
        first = True
        async def hallucinate(url, *, headers, body, timeout):
            nonlocal first
            if first:
                first = False
                self.transport.requests.append(json.loads(body))
                call = {'tool_calls': [{'index': 0, 'id': 'forbidden', 'type': 'function',
                        'function': {'name': 'execute', 'arguments': '{}'}}]}
                yield ('data: ' + json.dumps({'choices': [{'delta': call, 'finish_reason': 'tool_calls'}]}) + '\n\n').encode('utf-8')
                yield b'data: [DONE]\n\n'
            else:
                async for chunk in stream(url, headers=headers, body=body, timeout=timeout):
                    yield chunk
        with patch.object(self.transport, 'post_stream', new=hallucinate):
            result = await self.host.run_async(row['session_id'], 'use the available tools')
        self.assertEqual(result.reason, 'completed')
        self.assertEqual(self.executed, [])
        self.assertEqual(len(self.transport.requests), 2)
        for request in self.transport.requests:
            self.assertEqual([tool['function']['name'] for tool in request['tools']], ['inspect'])

    async def test_resource_stage_recovery_uses_exact_existing_identities(self):
        self.item = self.receive()
        row = await self.ensure()
        with closing(sqlite3.connect(self.factory.path)) as db, db:
            db.execute("UPDATE workers SET state='unknown',stage='workspace',workspace_id=NULL,session_id=NULL")
        with patch.object(self.host.worktrees, 'create_team', side_effect=AssertionError('重复创建工作树')), \
                patch.object(self.host, 'create_session', side_effect=AssertionError('重复创建对话')):
            # 已有工作树可回查；原创建对话也已有初始元数据，不应再创建。
            self.assertEqual(await self.ensure(), row)

    async def test_interrupted_session_creation_recovers_without_new_resources(self):
        self.item = self.receive()
        row = await self.ensure()
        with closing(sqlite3.connect(self.factory.path)) as db, db:
            db.execute("UPDATE workers SET state='unknown',stage='session',session_id=NULL")
        with patch.object(self.host.worktrees, 'create_team', side_effect=AssertionError('重复创建工作树')), \
                patch.object(self.host, 'create_session', side_effect=AssertionError('重复创建对话')):
            recovered = await self.ensure()
        self.assertEqual(recovered, row)

    async def test_local_workspace_ack_failure_does_not_repeat_git_creation(self):
        self.item = self.receive()
        mark = self.factory._mark
        original = OSError('workspace receipt failed')
        failed = False
        def fail_once(*args, **kwargs):
            nonlocal failed
            if not failed and args[1] == 'workspace_ready':
                failed = True
                raise original
            return mark(*args, **kwargs)
        with patch.object(self.host.worktrees, 'create_team', wraps=self.host.worktrees.create_team) as create, \
                patch.object(self.factory, '_mark', side_effect=fail_once):
            with self.assertRaises(OSError) as caught:
                await self.ensure()
            self.assertIs(caught.exception, original)
            self.assertEqual((await self.ensure())['state'], 'ready')
            self.assertEqual(create.call_count, 1)

    async def test_returned_profile_and_worker_snapshots_cannot_expand_permissions(self):
        profiles = self.factory.profiles
        profiles['coding']['tools'].append('execute')
        profiles['coding']['sources'].append('unregistered')
        self.item = self.receive()
        row = await self.ensure()
        row['session_id'] = 'foreign'
        stored = self.factory.worker(row['task_id'])
        self.assertNotEqual(stored['session_id'], 'foreign')
        self.assertEqual(self.host._agent_for(self.host.get_session(stored['session_id'])).tools.names(), ['inspect'])
        self.assertEqual(self.factory.profiles['coding']['sources'], ['github'])

    async def test_invalid_resource_stage_is_not_treated_as_unknown_creation(self):
        self.item = self.receive()
        row = await self.ensure()
        with closing(sqlite3.connect(self.factory.path)) as db, db:
            db.execute("UPDATE workers SET state='unknown',stage='session',workspace_id=NULL,session_id=NULL")
        with self.assertRaises(HarnessError) as caught:
            self.factory.worker(row['task_id'])
        self.assertEqual(caught.exception.code, 'superagent/corrupt')
        self.assertEqual(len(self.host.worktrees.list()), 1)

    async def test_corrupt_creation_record_is_preserved_without_replay(self):
        self.item = self.receive()
        await self.ensure()
        with closing(sqlite3.connect(self.factory.path)) as db, db:
            db.execute("UPDATE workers SET commit_id='invalid'")
        with self.assertRaises(HarnessError) as caught:
            await self.ensure()
        self.assertEqual(caught.exception.code, 'superagent/corrupt')
        with closing(sqlite3.connect(self.factory.path)) as db:
            self.assertEqual(db.execute('SELECT commit_id FROM workers').fetchone()[0], 'invalid')
