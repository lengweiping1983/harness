"""工作树交付与环境复制公开入口在同一索引事务锁内发布。"""
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from pathlib import Path
from harness.app.worktrees import WorktreeService


class WorktreePublicationSerializationTests(unittest.TestCase):
    def test_capture_and_environment_publication_keep_both_index_updates(self):
        with tempfile.TemporaryDirectory() as directory:
            service = WorktreeService(Path(directory))
            service._save([{'id':'tree','repo':directory,'path':directory,'base_commit':'a'*40,'status':'active'}])
            entered, release, copying = threading.Event(), threading.Event(), threading.Event()
            def capture(owner, row):
                self.assertIs(owner,service)
                entered.set()
                if not release.wait(2):
                    raise TimeoutError('fixture release')
                owner._save([{**item,'delivery':{'commit':'original'}} for item in owner.list()])
                return {'commit':'original'}
            def copy(owner, row, **kwargs):
                copying.set()
                self.assertEqual(row['delivery'],{'commit':'original'})
                owner._save([{**item,'environment':{'receipt_id':'original'}} for item in owner.list()])
                return {'receipt_id':'original'}
            with patch('harness.app.worktree_delivery.capture',side_effect=capture), patch('harness.app.worktree_environment_copy.copy_environment',side_effect=copy), ThreadPoolExecutor(max_workers=2) as workers:
                first=workers.submit(service.capture_delivery,'tree')
                try:
                    self.assertTrue(entered.wait(2))
                    second=workers.submit(service.copy_environment,'tree',expected_fingerprint='a'*64)
                    self.assertFalse(copying.wait(.02),'Environment publication must not enter during capture')
                finally:
                    release.set()
                self.assertEqual(first.result(timeout=2),{'commit':'original'})
                self.assertEqual(second.result(timeout=2),{'receipt_id':'original'})
            row=service.get('tree')
            self.assertEqual(row['delivery'],{'commit':'original'})
            self.assertEqual(row['environment'],{'receipt_id':'original'})
