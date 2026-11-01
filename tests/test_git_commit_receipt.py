"""提交已受理后的状态读取失败不得描述为明确拒绝。"""
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from harness.app.git_operations import commit
from harness.core.errors import HarnessError


class GitCommitReceiptTests(unittest.TestCase):
    def test_post_commit_repository_failure_is_explicitly_uncertain(self):
        original=HarnessError('Original repository failure 原文',code='git/failed')
        service=SimpleNamespace(_git_bytes=Mock(return_value=b'[main abcdef1] Original\n'),git=Mock(return_value='a'*40),repository=Mock(side_effect=original))
        current={'root':'/fixture','conflicts':False,'staged_files':[{'path':'a.txt','status':'M'}]}
        with patch('harness.app.git_operations.checked',return_value=current):
            with self.assertRaises(HarnessError) as error:
                commit(service,'/fixture','Original commit','revision')
        self.assertEqual(error.exception.code,'git/commit-uncertain')
        self.assertIs(error.exception.__cause__,original)
        service._git_bytes.assert_called_once_with(Path('/fixture'),'commit','--file=-',input=b'Original commit')

    def test_post_commit_head_movement_keeps_original_commit_identity(self):
        current={'root':'/fixture','conflicts':False,'staged_files':[{'path':'a.txt','status':'M'}]}
        saved={'root':'/fixture','head':'b'*40}
        service=SimpleNamespace(_git_bytes=Mock(return_value=b'[main abcdef1] Original\n'),git=Mock(return_value='a'*40),repository=Mock(return_value=saved))
        with patch('harness.app.git_operations.checked',return_value=current):
            result=commit(service,'/fixture','Original','revision')
        self.assertEqual(result['commit'],'a'*40)
        self.assertEqual(result['repository'],saved)
