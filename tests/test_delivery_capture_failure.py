"""交付记录与引用清理同时失败时保留原始原因和现场身份。"""
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from harness.app.worktree_delivery import capture
from harness.core.errors import HarnessError
from harness.core.language import current_language
from harness.i18n import tr


class DeliveryCaptureFailureTests(unittest.TestCase):
    def test_failed_reference_cleanup_does_not_mask_persistence_failure(self):
        row = {'id': 'fixture', 'base_commit': 'a' * 40}
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                original = OSError('Original storage 原文')
                cleanup = HarnessError('Original Git 原文', code='fixture/git')
                service = SimpleNamespace(_target=Mock(return_value=Path('/fixture')),
                    git=Mock(side_effect=['', cleanup]), list=Mock(return_value=[row]),
                    _save=Mock(side_effect=original))
                with patch('harness.app.worktree_delivery.snapshot_tree', return_value=('a' * 40, 'b' * 40)), \
                     patch('harness.app.worktree_delivery.commit_tree', return_value='c' * 40):
                    with self.assertRaises(HarnessError) as error:
                        capture(service, row)
                self.assertIs(error.exception.__cause__, original)
                self.assertEqual(error.exception.message, tr('git.delivery_uncertain'))
                self.assertEqual(error.exception.details['cleanup_error'], cleanup.to_dict())
                self.assertEqual(error.exception.details['worktree_id'], 'fixture')
                reference = service.git.call_args_list[0].args[2]
                self.assertEqual(service.git.call_args_list[1].args, (Path('/fixture'), 'update-ref', '-d', reference, 'c' * 40))
                self.assertEqual(error.exception.details['ref'], reference)
                self.assertEqual(service._save.call_count, 1)

    def test_successful_cleanup_preserves_original_exception(self):
        original = OSError('Original storage 原文')
        row = {'id': 'fixture', 'base_commit': 'a' * 40}
        service = SimpleNamespace(_target=Mock(return_value=Path('/fixture')), git=Mock(),
            list=Mock(return_value=[row]), _save=Mock(side_effect=original))
        with patch('harness.app.worktree_delivery.snapshot_tree', return_value=('a' * 40, 'b' * 40)), \
             patch('harness.app.worktree_delivery.commit_tree', return_value='c' * 40):
            with self.assertRaises(OSError) as error:
                capture(service, row)
        self.assertIs(error.exception, original)
        self.assertEqual(service.git.call_count, 2)
