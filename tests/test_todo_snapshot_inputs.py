"""待办写入拒绝缺失必填字段，SDK快照不共享可变列表或证据。"""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace, MappingProxyType

from harness.core.language import current_language
from harness.session.store import SessionStore
from harness.tools.builtin.todo_tools import TodoItem, TodoStore, build_todo_tools


class TodoSnapshotInputTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_required_fields_do_not_write_or_clear_existing_lists(self):
        for language in ('en', 'zh-CN', 'en'):
            with tempfile.TemporaryDirectory() as folder, current_language.set(language):
                sessions = SessionStore(folder)
                session = sessions.create()
                store = TodoStore(session_of=sessions.get)
                write = next(tool for tool in build_todo_tools(store) if tool.schema.name == 'todo_write')
                ctx = SimpleNamespace(session_id=session.id, services={'session': session})
                saved = await write.execute({'todos': [{'content': 'Original task 原文', 'status': 'pending'}]}, ctx)
                version = saved.display['snapshot_token']
                before = session.event_count
                original = {str(p.relative_to(Path(folder))):p.read_bytes() for p in Path(folder).rglob('*') if p.is_file()}
                for args in ({'snapshot_token': version},
                             {'snapshot_token': version, 'todos': [{'content': 'Replacement task 原文'}]}):
                    with self.assertRaises(ValueError) as failure:
                        await write.execute(args, ctx)
                    self.assertEqual(any('\u4e00' <= c <= '\u9fff' for c in str(failure.exception)), language == 'zh-CN')
                    self.assertEqual(session.event_count, before)
                    self.assertEqual({str(p.relative_to(Path(folder))):p.read_bytes() for p in Path(folder).rglob('*') if p.is_file()}, original)
                cleared = await write.execute({'snapshot_token': version, 'todos': []}, ctx)
                self.assertFalse(cleared.is_error)
                self.assertEqual(store.get(session.id), [])

    async def test_memory_store_owns_input_output_and_nested_evidence(self):
        store = TodoStore()
        evidence = {'summary': 'Original proof 原文', 'metadata': {'values': ['original']}}
        item = TodoItem('Original task 原文', 'pending', evidence=(evidence,), id='todo_fixture')
        submitted = [item]
        returned = store.write('fixture', submitted)
        submitted.clear(); evidence['summary'] = 'Caller change'; evidence['metadata']['values'].clear()
        returned.clear()
        first = store.get('fixture')
        self.assertEqual(first[0].evidence[0]['summary'], 'Original proof 原文')
        self.assertEqual(first[0].evidence[0]['metadata']['values'], ['original'])
        first[0].evidence[0]['metadata']['values'].clear(); first.clear()
        self.assertEqual(store.get('fixture')[0].evidence[0]['metadata']['values'], ['original'])

    async def test_frozen_evidence_can_be_stored_and_exported_without_sharing(self):
        store = TodoStore()
        evidence = MappingProxyType({'summary': 'Original proof 原文',
                                     'metadata': MappingProxyType({'values': ('original',)})})
        item = TodoItem('Original task 原文', 'pending', evidence=(evidence,))
        returned = store.write('fixture', [item])
        exported = returned[0].to_dict()
        exported['evidence'][0]['metadata']['values'].clear()
        self.assertEqual(store.get('fixture')[0].evidence[0]['metadata']['values'], ['original'])
