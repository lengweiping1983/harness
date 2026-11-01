"""HTTP重放读取与循环快照清空共享锁，不在遍历期间修改缓冲。"""
import threading
import unittest
from collections import deque

from harness.web.hub import ConnectionHub


class HubSnapshotConcurrencyTests(unittest.TestCase):
    def test_replay_finishes_owned_snapshot_before_concurrent_clear(self):
        hub = ConnectionHub()
        hub.stamp('fixture', {'type': 'note', 'nested': {'text': 'Original 原文'}})
        entered, release, cleared = threading.Event(), threading.Event(), threading.Event()
        errors, values = [], []
        class HeldReplay(deque):
            def __iter__(self):
                iterator = super().__iter__()
                entered.set()
                if not release.wait(2):
                    raise TimeoutError('fixture replay release')
                yield from iterator
        hub.room('fixture').replay = HeldReplay(hub.room('fixture').replay)
        def read():
            try:
                values.append(hub.replay('fixture', 0))
            except BaseException as error:
                errors.append(error)
        def clear():
            hub.close_all()
            cleared.set()
        reader, cleaner = threading.Thread(target=read), threading.Thread(target=clear)
        reader.start()
        try:
            self.assertTrue(entered.wait(2))
            cleaner.start()
            self.assertFalse(cleared.wait(.02))
        finally:
            release.set();reader.join(2)
            if cleaner.ident is not None:
                cleaner.join(2)
        self.assertFalse(reader.is_alive());self.assertFalse(cleaner.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(values[0][0]['nested']['text'], 'Original 原文')
        self.assertTrue(cleared.is_set())
        self.assertEqual(tuple(hub.rooms()), ())
