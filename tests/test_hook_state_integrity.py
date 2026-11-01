"""Hook 状态和完成回执拒绝损坏与重复字段，原件保持。"""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from harness.app.hooks import ProjectHooks, _exit_status
from harness.core.errors import PersistenceError
from harness.core.language import current_language
from harness.i18n import tr
from harness.session.session import Session
from harness.tools.processes import ProcessCleanupError


def run_row():
    return dict(id='fixture',root='/fixture',session_id='owner',call_id='call',event='PreToolUse',tool='FixtureTool',hook_id='hook',label='Original 原文',command='fixture',handler_type='command',source_scope='project',fingerprint='fixture',reason='',stdout='',stderr='',status='passed',turn=1,step=1,started=1.5,duration_ms=0,finished=2,plugin_id=None)


class HookStateIntegrityTests(unittest.IsolatedAsyncioTestCase):
    def test_invalid_state_is_localized_and_never_overwritten(self):
        changes=({'status':'unknown'},{'status':[]},{'turn':True},{'step':'1'},{'duration_ms':float('nan')},{'started':False},{'finished':float('inf')},{'root':None},{'event':'invalid'},{'handler_type':'invalid'})
        cases=[('hook-runs.json',json.dumps([{**run_row(),**change}]).encode()) for change in changes]
        cases += [('hook-trust.json',b'{"owner":true}'),('hook-trust.json',b'{"owner":"first","owner":"second"}')]
        for language in ('en','zh-CN','en'):
            for name,raw in cases:
                with self.subTest(language=language,name=name,raw=raw), current_language.set(language), tempfile.TemporaryDirectory() as directory:
                    root=Path(directory);path=root/name;path.write_bytes(raw)
                    with self.assertRaises(PersistenceError) as error:
                        ProjectHooks(SimpleNamespace(config=SimpleNamespace(home=root)))
                    self.assertEqual(error.exception.message,tr('python.audit.hook_state_invalid',path=str(path)))
                    self.assertEqual(path.read_bytes(),raw)

    def test_current_state_and_live_recovery_keep_original_record(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);path=root/'hook-runs.json'
            statuses=('passed','blocked','failed','timed_out','cancelled','interrupted','skipped','stopped','cleanup_failed')
            rows=[{**run_row(),'id':status,'status':status} for status in statuses]
            raw=json.dumps([*rows,{**run_row(),'id':'running','status':'running'}]).encode();path.write_bytes(raw)
            hooks=ProjectHooks(SimpleNamespace(config=SimpleNamespace(home=root)))
            self.assertEqual(hooks.runs[:-1],rows)
            self.assertEqual(hooks.runs[-1]['status'],'interrupted')
            self.assertEqual(path.read_bytes(),raw)

    def test_duplicate_decisions_cannot_become_plain_context_or_approval(self):
        for event in ('SessionStart','PreToolUse','PermissionRequest'):
            for text in ('{"continue":false,"continue":true}', '{"hookSpecificOutput":{"permissionDecision":"deny","permissionDecision":"allow"}}'):
                result=ProjectHooks._interpret(event,dict(stdout=text,stderr='',exit_code=0,timed_out=False,stdout_dropped=0))
                self.assertEqual(result['status'],'failed')
                self.assertEqual(result['context'],'')
                self.assertNotIn('permission',result)

    def test_completion_status_strict_integer_and_original_bytes(self):
        for language in ('en','zh-CN','en'):
            with current_language.set(language), tempfile.TemporaryDirectory() as directory:
                path=Path(directory)/'status.json'
                for text in ('[]','null','{"exit_code":true}','{"exit_code":"0"}','{"exit_code":0.0}','{"exit_code":0,"exit_code":1}','{"exit_code":0,"extra":true}'):
                    path.write_text(text,encoding='utf-8')
                    with self.assertRaises(ProcessCleanupError) as error:
                        _exit_status(path)
                    self.assertEqual(error.exception.message,tr('hooks.status.invalid'))
                    self.assertEqual(error.exception.code,'process/cleanup-failed')
                    self.assertEqual(path.read_text(encoding='utf-8'),text)
                for code in (0,1,2,-15,4294967295):
                    path.write_text(json.dumps({'exit_code':code}),encoding='utf-8')
                    self.assertEqual(_exit_status(path),code)

    async def test_session_start_is_not_replayed_after_unknown_dispatch_failure(self):
        hooks=ProjectHooks.__new__(ProjectHooks)
        hooks.started_sessions=set()
        hooks.project_root=Mock(return_value=Path('/fixture'))
        hooks.snapshot=Mock(return_value={'error':'fixture','rows':[]})
        hooks._restricted=Mock(return_value=False)
        hooks.harness=SimpleNamespace(bus=None)
        failure=OSError('Original accepted failure')
        hooks._dispatch=AsyncMock(side_effect=failure)
        session=Session()
        with self.assertRaises(OSError) as error:
            await hooks.lifecycle(None,session,'SessionStart',turn=1,step=1,extra={})
        self.assertIs(error.exception,failure)
        await hooks.lifecycle(None,session,'SessionStart',turn=1,step=1,extra={})
        hooks._dispatch.assert_awaited_once()
