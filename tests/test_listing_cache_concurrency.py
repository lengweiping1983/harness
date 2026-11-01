"""列表缓存命中与清空共用锁，不在并发失效后移动已删除键。"""
import tempfile
import threading
import unittest
from collections import OrderedDict
from pathlib import Path
from unittest.mock import patch

from harness.web import views


class ListingCacheConcurrencyTests(unittest.TestCase):
    def test_clear_waits_for_owned_cache_hit_and_next_read_rescans(self):
        entered, release, cleared = threading.Event(), threading.Event(), threading.Event()
        errors, results = [], []

        class HeldCache(OrderedDict):
            def get(self, key, default=None):
                value = super().get(key, default)
                entered.set()
                if not release.wait(2):
                    raise TimeoutError('fixture cache release')
                return value

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'events.jsonl'
            path.write_text('', encoding='utf-8')
            stat = path.stat()
            cache = HeldCache({(str(path), stat.st_mtime_ns, stat.st_size): (3, 42, 'Original 原文')})

            def read():
                try:
                    results.append(views._list_stats_for_disk_session(path))
                except BaseException as error:
                    errors.append(error)

            def clear():
                views.clear_listing_cache()
                cleared.set()

            reader, cleaner = threading.Thread(target=read), threading.Thread(target=clear)
            with patch.object(views, '_LISTING_CACHE', cache):
                reader.start()
                try:
                    self.assertTrue(entered.wait(2))
                    cleaner.start()
                    self.assertFalse(cleared.wait(.02), 'Cache clear must wait for the hit/move transaction')
                finally:
                    release.set()
                    reader.join(2)
                    if cleaner.ident is not None:
                        cleaner.join(2)
                self.assertFalse(reader.is_alive())
                self.assertFalse(cleaner.is_alive())
                self.assertEqual(errors, [])
                self.assertEqual(results, [(3, 42, 'Original 原文')])
                self.assertEqual(cache, {})
                self.assertEqual(views._list_stats_for_disk_session(path), (0, 0, None))
