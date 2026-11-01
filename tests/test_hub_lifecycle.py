"""连接中枢的快照释放、订阅归属及有界发送回归。"""
import asyncio
import unittest

from harness.web.hub import ConnectionHub, SEND_BATCH_EVENTS


class RecordingConnection:
    def __init__(self):
        self.closed = False
        self.messages = []
        self.delivered = asyncio.Event()

    async def send_json(self, event):
        self.messages.append(event)
        self.delivered.set()


class HubLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_close_all_releases_every_temporary_snapshot(self):
        hub = ConnectionHub()
        hub.stamp('owner', {'type': 'request/start', 'request_id': 'original', 'generation': {'phase': 'waiting'}})
        hub.stamp('owner', {'type': 'assistant/delta', 'request_id': 'original', 'delta': 'Original draft 原文'})
        hub.stamp('owner', {'type': 'agent/member-stream', 'run_id': 'run', 'member': 'member', 'agent_id': 'agent', 'version': 1, 'status': 'streaming', 'text': 'Original preview 原文'})
        self.assertEqual(hub.draft('owner')['text'], 'Original draft 原文')
        self.assertIsNotNone(hub.generation('owner'))
        self.assertEqual(len(hub.member_previews('owner')), 1)
        hub.close_all()
        self.assertEqual(tuple(hub.rooms()), ())
        self.assertEqual(hub.draft('owner'), {})
        self.assertIsNone(hub.generation('owner'))
        self.assertEqual(hub.member_previews('owner'), [])
        self.assertEqual(hub._member_closed_runs, {})
        self.assertEqual(hub.replay('owner', 0), [])
        hub.stamp('owner', {'type': 'note'})
        self.assertEqual(hub.last_seq('owner'), 1)
        self.assertEqual(hub.draft('owner'), {})
        hub.close_all()

    async def test_old_writer_cannot_remove_replacement_subscription(self):
        entered, cleaning, release = (asyncio.Event() for _ in range(3))
        connection = RecordingConnection()
        first = True

        async def send(event):
            nonlocal first
            if first:
                first = False
                entered.set()
                try:
                    await asyncio.Future()
                except asyncio.CancelledError:
                    cleaning.set()
                    await release.wait()
                    raise
            connection.messages.append(event)
            connection.delivered.set()

        connection.send_json = send
        hub = ConnectionHub()
        old = hub.subscribe('owner', connection)
        new = None
        try:
            hub.publish('owner', {'seq': 1})
            await asyncio.wait_for(entered.wait(), 2)
            new = hub.subscribe('owner', connection)
            if not old.writer.cancelling():
                old.writer.cancel()
            await asyncio.wait_for(cleaning.wait(), 2)
            hub.publish('owner', {'seq': 2})
            await asyncio.wait_for(connection.delivered.wait(), 2)
            release.set()
            await asyncio.gather(old.writer, return_exceptions=True)
            self.assertIs(hub.room('owner').subscribers[id(connection)], new)
            connection.delivered.clear()
            hub.publish('owner', {'seq': 3})
            await asyncio.wait_for(connection.delivered.wait(), 2)
            self.assertEqual(connection.messages, [{'seq': 2}, {'seq': 3}])
            self.assertEqual(hub.observer_count(), 1)
        finally:
            release.set()
            hub.close_all()
            tasks = [row.writer for row in [old, new] if row is not None]
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def test_batched_delivery_is_ordered_and_bounded(self):
        hub = ConnectionHub(outbox_size=100)
        connection = RecordingConnection()
        batches, complete = [], asyncio.Event()

        async def batch(events):
            batches.append(events)
            if sum(map(len, batches)) == 65:
                complete.set()

        connection.send_json_batch = batch
        subscriber = hub.subscribe('owner', connection)
        try:
            for seq in range(65):
                hub.publish('owner', {'seq': seq})
            await asyncio.wait_for(complete.wait(), 2)
            self.assertTrue(all(len(events) <= SEND_BATCH_EVENTS for events in batches))
            self.assertEqual([event['seq'] for events in batches for event in events], list(range(65)))
        finally:
            hub.close_all()
            await asyncio.gather(subscriber.writer, return_exceptions=True)

    async def test_slow_subscriber_disconnects_without_losing_replay(self):
        hub = ConnectionHub(outbox_size=2, max_drops=2)
        connection = RecordingConnection()
        closed = asyncio.Event()
        closes = []

        async def close(code, reason):
            closes.append((code, reason))
            closed.set()

        connection.send_close = close
        subscriber = hub.subscribe('owner', connection)
        try:
            for index in range(4):
                event = hub.stamp('owner', {'type': 'note', 'index': index})
                hub.publish('owner', event)
            await asyncio.wait_for(closed.wait(), 2)
            self.assertEqual(hub.observer_count(), 0)
            self.assertEqual(subscriber.dropped, 2)
            self.assertEqual(closes, [(1013, 'client too slow; recover snapshot')])
            self.assertEqual([event['index'] for event in hub.replay('owner', 0)], list(range(4)))
        finally:
            hub.close_all()
            await asyncio.gather(subscriber.writer, return_exceptions=True)

    async def test_failed_writer_does_not_remove_another_connection(self):
        hub = ConnectionHub()
        failed, healthy = RecordingConnection(), RecordingConnection()

        async def fail(event):
            raise OSError('Original connection diagnostic 原文')

        failed.send_json = fail
        first, second = hub.subscribe('owner', failed), hub.subscribe('owner', healthy)
        try:
            hub.publish('owner', {'seq': 1})
            await asyncio.wait_for(healthy.delivered.wait(), 2)
            await asyncio.wait_for(first.writer, 2)
            self.assertEqual(healthy.messages, [{'seq': 1}])
            self.assertEqual(hub.observer_count(), 1)
        finally:
            hub.close_all()
            await asyncio.gather(first.writer, second.writer, return_exceptions=True)
