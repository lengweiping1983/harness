"""环境回执损坏须保留原件，复制前明确拒绝。"""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from harness.app.worktree_environment_copy import copy_environment
from harness.core.errors import HarnessError
from harness.core.language import current_language
from harness.i18n import tr


class EnvironmentReceiptIntegrityTests(unittest.TestCase):
    def test_invalid_receipts_preserve_bytes_and_never_plan_or_copy(self):
        fingerprint, identifier = 'a' * 64, 'b' * 32
        valid = {'receipt_id':identifier,'worktree_id':'tree','fingerprint':fingerprint,
                 'source_fingerprint':fingerprint,'previous_receipt_id':None,'status':'ready',
                 'planned':[],'copied':[],'skipped':[]}
        invalid = [b'bad json', b'\xff', b'null', b'[]']
        invalid += [json.dumps({**valid, key:value}).encode() for key,value in (
            ('receipt_id','c'*32),('status','unknown'),('copied',None),('copied',[{}]),
            ('copied',[{'path':'../foreign','size':0,'mode':0,'sha256':fingerprint}]),
            ('planned',{}),('fingerprint',True),('worktree_id','foreign'))]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            receipts = root/'environment-receipts';receipts.mkdir()
            path = receipts/(identifier+'.json')
            service = SimpleNamespace(root=root,_target=Mock())
            row = {'id':'tree','status':'active','environment':{'receipt_id':identifier}}
            for language in ('en','zh-CN','en'):
                for raw in invalid:
                    with self.subTest(language=language,raw=raw), current_language.set(language), patch('harness.app.worktree_environment_copy.plan_environment') as plan:
                        path.write_bytes(raw)
                        with self.assertRaises(HarnessError) as caught:
                            copy_environment(service,row,expected_fingerprint=fingerprint)
                        self.assertEqual(caught.exception.code,'git/environment-invalid')
                        self.assertEqual(caught.exception.message,tr('ui.git.error.invalid'))
                        self.assertEqual(path.read_bytes(),raw)
                        plan.assert_not_called();service._target.assert_not_called()
            path.write_text(json.dumps(valid),encoding='utf-8')
            with patch('harness.app.worktree_environment_copy.plan_environment') as plan:
                self.assertEqual(copy_environment(service,row,expected_fingerprint=fingerprint),{**valid,'reused':True})
                plan.assert_not_called()

            identity = {'path':'.env','size':4,'mode':384,'sha256':fingerprint}
            populated = {**valid,'planned':[identity],'copied':[identity],'skipped':[{'path':'exists.env','reason':'exists'}]}
            path.write_text(json.dumps(populated),encoding='utf-8')
            with patch('harness.app.worktree_environment_copy._regular_file',return_value=(object(),None)), patch('harness.app.worktree_environment_copy.plan_environment') as plan:
                self.assertEqual(copy_environment(service,row,expected_fingerprint=fingerprint),{**populated,'reused':True})
                plan.assert_not_called()
