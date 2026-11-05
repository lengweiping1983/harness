"""计划版本摘要与提交失败边界。"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness.app import plans
from harness.session.store import SessionStore


class PlanIntegrityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = SessionStore(Path(self.directory.name))
        self.session = self.store.create()

    async def test_submission_records_hash_and_revision_payload(self):
        body = '# 标题\n逐条核验。'
        plan_id = await plans.submit(self.session, body, 'call-1')
        record = plans.versions(self.session)[0]
        self.assertEqual(record['id'], plan_id)
        self.assertEqual(record['content_hash'], plans.content_hash(body))
        self.assertEqual(len(record['content_hash']), 64)

    async def test_failed_new_submission_keeps_previous_pending_review(self):
        old_id = await plans.submit(self.session, '# 旧计划\n等待审批。', 'old')
        append = self.session.append
        async def fail_new(event_type, data, **kwargs):
            if data.get('kind') == 'plan_submission':
                raise OSError('fixture append failure')
            return await append(event_type, data, **kwargs)
        with patch.object(self.session, 'append', side_effect=fail_new):
            with self.assertRaises(OSError):
                await plans.submit(self.session, '# 新计划\n提交失败。', 'new')
        current = plans.versions(self.session)
        self.assertEqual([(item['id'], item['status']) for item in current], [(old_id, 'pending')])
