"""持久Todo的SDK与工具快照读取拒绝同一损坏事件并保留原件。"""
import copy
import json
from types import SimpleNamespace, MappingProxyType
import unittest

from harness.core.errors import SessionInvariantError
from harness.core.language import current_language
from harness.i18n import tr
from harness.tools.builtin.todo_state import snapshots
from harness.tools.builtin.todo_tools import TodoStore


class TodoPersistedIntegrityTests(unittest.TestCase):
    def payload(self):
        return {'list_id':'list_fixture','todos':[{'id':'todo_fixture','content':'Original task 原文','status':'pending'}]}

    def reads(self, value):
        event = SimpleNamespace(seq=2,data=value)
        session = SimpleNamespace(id='fixture',events_of_type=lambda kind:[event])
        return (lambda:snapshots(session),lambda:TodoStore(session_of=lambda sid:session).get('fixture'))

    def test_invalid_current_event_shapes_fail_in_both_consumers_without_mutation(self):
        valid = self.payload()
        cases = [None, [], {}, {**valid,'todos':None}, {**valid,'list_id':True}, {**valid,'plan_id':[]},
                 {**valid,'extra':'sensitive-fixture'}, {**valid,'todos':[None]},
                 {**valid,'todos':[valid['todos'][0],valid['todos'][0]]}]
        for field,bad in [('id',None),('id',True),('content',{}),('content',''),('status','old-status'),
                          ('plan_id','other-plan'),('evidence',{}),('activeForm','old alias')]:
            cases.append({**valid,'todos':[{**valid['todos'][0],field:bad}]})
        for value in cases:
            original = json.dumps(value,ensure_ascii=False)
            for language in ('en','zh-CN','en'):
                with self.subTest(value=value,language=language),current_language.set(language):
                    for read in self.reads(value):
                        with self.assertRaises(SessionInvariantError) as error:read()
                        self.assertEqual(error.exception.message,tr('todo.snapshot.invalid',p0=2))
                        self.assertNotIn('sensitive-fixture',error.exception.message)
            self.assertEqual(json.dumps(value,ensure_ascii=False),original)

    def test_invalid_evidence_fields_are_not_projected_as_valid_sdk_items(self):
        evidence = {'call_id':'call','name':'read','source_seq':0,'summary':'Original proof 原文','truncated':False}
        for field,bad in [('call_id',None),('name',True),('source_seq',True),('source_seq',-1),
                          ('source_seq',2),('summary',{}),('truncated','false')]:
            value = self.payload();value['todos'][0]['evidence']=[{**evidence,field:bad}]
            original = copy.deepcopy(value)
            for read in self.reads(value):
                with self.assertRaises(SessionInvariantError):read()
            self.assertEqual(value,original)

    def test_frozen_current_event_preserves_original_evidence_and_isolates_sdk_result(self):
        evidence = MappingProxyType({'call_id':'call','name':'read','source_seq':0,'summary':'Original proof 原文','truncated':False})
        row = MappingProxyType({'id':'todo_fixture','content':'Original task 原文','status':'completed','plan_id':'plan_fixture','evidence':(evidence,)})
        value = MappingProxyType({'list_id':'list_fixture','plan_id':'plan_fixture','todos':(row,)})
        read_snapshots,read_store = self.reads(value)
        self.assertEqual(read_snapshots()[0].todos,(row,))
        result = read_store()
        self.assertEqual(result[0].content,'Original task 原文')
        result[0].evidence[0]['summary']='Caller change'
        self.assertEqual(read_store()[0].evidence[0]['summary'],'Original proof 原文')
