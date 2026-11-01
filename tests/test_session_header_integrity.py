"""对话头在目录、加载和导入中使用同一当前格式校验。"""
import json
import tempfile
import unittest
from pathlib import Path

from harness.core.errors import SessionInvariantError, PersistenceError
from harness.session.events import AppendOp, SessionHeader, SESSION_FORMAT_VERSION
from harness.session.store import SessionStore


class SessionHeaderIntegrityTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_headers_are_not_listed_or_loaded_and_originals_remain(self):
        for changes in ({'version':True}, {'version':999}, {'created_at':True}, {'created_at':'1'},
                        {'created_at':-1}, {'id':'other'}, {'metadata':[]}, {'cwd':1}, {'title':{}}, {'parent_session':False}):
            with self.subTest(changes=changes), tempfile.TemporaryDirectory() as directory:
                session = SessionStore(directory).create()
                path = Path(directory)/'sessions'/session.id/'header.json'
                raw=json.dumps({**session.header.to_dict(),**changes}).encode()
                path.write_bytes(raw)
                store=SessionStore(directory)
                with self.assertLogs('harness.session.store',level='WARNING'):
                    self.assertEqual(store.list_sessions(),[])
                with self.assertRaises(SessionInvariantError):
                    store.get(session.id)
                self.assertEqual(path.read_bytes(),raw)

    async def test_corrupt_tail_retains_readable_prefix_and_refuses_all_new_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            session=SessionStore(directory).create()
            await session.append('user/message',{'text':'Original 原文'},surface_op=AppendOp())
            log=Path(directory)/'sessions'/session.id/'events.jsonl'
            log.write_bytes(log.read_bytes()+b'{"broken":\n')
            original=log.read_bytes()
            restored=SessionStore(directory).get(session.id)
            self.assertEqual(restored.event_count,1)
            self.assertEqual(restored.surface[0].data['text'],'Original 原文')
            self.assertTrue(restored.persistence_error)
            with self.assertRaises(PersistenceError):
                await restored.append('note',{'kind':'unchanged'})
            self.assertEqual(log.read_bytes(),original)

    async def test_current_optional_fields_and_nested_metadata_preserve_original_values(self):
        raw={'id':'current','created_at':0,'version':SESSION_FORMAT_VERSION,'cwd':None,'title':'Original 原文','metadata':{'vendor':{'name':'原文'}}}
        self.assertEqual(SessionHeader.from_dict(raw).to_dict()['metadata'],raw['metadata'])
