"""通过真实工具注册表验证换行和文件观察的防覆盖边界。"""
import tempfile
import unittest
from pathlib import Path

from harness.llm.types import ToolUseBlock
from harness.session.store import SessionStore
from harness.tools.builtin.fs_tools import build_fs_tools
from harness.tools.builtin import fs_observation
from harness.tools.registry import ToolRegistry, ToolRunContext


class FileWriteBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.sessions = SessionStore(str(self.root / 'sessions'))
        self.session = self.sessions.create()
        self.addCleanup(fs_observation.forget_session, self.session.id)
        self.registry = ToolRegistry()
        for tool in build_fs_tools(str(self.root)):
            self.registry.register(tool)

    async def call(self, name, args, session=None):
        session = session or self.session
        ctx = ToolRunContext(call_id='file-test', session_id=session.id,
            cwd=str(self.root), turn=1, step=1, services={'session': session})
        return await self.registry.execute_call(ToolUseBlock(id='file-test', name=name, input=args), ctx)

    async def success(self, name, args):
        result = await self.call(name, args)
        self.assertFalse(result.is_error, result.content)
        return result

    async def test_replace_preserves_line_endings_and_final_newline(self):
        for ending in ('\n', '\r\n', '\r'):
            for final in ('', ending):
                with self.subTest(ending=repr(ending), final=bool(final)):
                    path = self.root / 'replace.txt'
                    path.write_bytes(('one' + ending + 'two' + final).encode())
                    await self.success('read', {'path': str(path)})
                    await self.success('edit', {'path': str(path), 'old_string': 'one\ntwo', 'new_string': 'first\nsecond'})
                    self.assertEqual(path.read_bytes(), ('first' + ending + 'second' + final).encode())

    async def test_replace_preserves_mixed_line_endings_outside_the_match(self):
        path = self.root / 'mixed.txt'
        original = b'keep\r\nchange\nbare\rend'
        path.write_bytes(original)
        await self.success('read', {'path': str(path)})
        await self.success('edit', {'path': str(path), 'old_string': 'change', 'new_string': 'done'})
        self.assertEqual(path.read_bytes(), original.replace(b'change', b'done'))

    async def test_normalized_identity_edit_keeps_mixed_bytes_and_reports_no_change(self):
        path = self.root / 'identity.txt'
        for final in (b'', b'\r\n', b'\n', b'\r'):
            with self.subTest(final=final):
                original = b'one\r\ntwo\nthree\rfour' + final
                path.write_bytes(original)
                await self.success('read', {'path': str(path)})
                text = original.decode().replace('\r\n', '\n').replace('\r', '\n')
                result = await self.success('edit', {'path': str(path), 'old_string': text, 'new_string': text})
                self.assertFalse(result.data['changed'])
                self.assertEqual(path.read_bytes(), original)

    async def test_replace_all_uses_each_matches_original_line_endings(self):
        path = self.root / 'replace-all.txt'
        path.write_bytes(b'a\r\nb\nkeep\ra\nb\r\n')
        await self.success('read', {'path': str(path)})
        await self.success('edit', {'path': str(path), 'old_string': 'a\nb',
            'new_string': 'A\nB', 'replace_all': True})
        self.assertEqual(path.read_bytes(), b'A\r\nB\nkeep\rA\nB\r\n')

    async def test_new_line_endings_follow_local_style(self):
        path = self.root / 'local.txt'
        path.write_bytes(b'head\r\nitem\nlast\r')
        await self.success('read', {'path': str(path)})
        await self.success('edit', {'path': str(path), 'old_string': 'item', 'new_string': 'first\nsecond'})
        self.assertEqual(path.read_bytes(), b'head\r\nfirst\nsecond\nlast\r')

    async def test_edit_seams_do_not_merge_two_line_breaks(self):
        path = self.root / 'seams.txt'
        for old, new, expected in (
            ('middle', '', 'left\n\nright'),
            ('middle', '\nnext', 'left\n\nnext\nright'),
            ('\nmiddle', 'next\n', 'leftnext\n\nright'),
            ('left\nmiddle\nright', 'LEFT\n\nRIGHT', 'LEFT\n\nRIGHT'),
        ):
            with self.subTest(old=old, new=new):
                path.write_bytes(b'left\rmiddle\nright')
                await self.success('read', {'path': str(path)})
                await self.success('edit', {'path': str(path), 'old_string': old, 'new_string': new})
                result = await self.success('read', {'path': str(path), 'line_numbers': False})
                self.assertEqual(result.data['content'], expected)

    async def test_insert_preserves_existing_mixed_line_endings(self):
        path = self.root / 'insert.txt'
        original = b'one\r\ntwo\nthree\rfour'
        expected = (
            b'added\r\none\r\ntwo\nthree\rfour',
            b'one\r\nadded\r\ntwo\nthree\rfour',
            b'one\r\ntwo\nadded\nthree\rfour',
            b'one\r\ntwo\nthree\radded\rfour',
            b'one\r\ntwo\nthree\rfour\radded',
        )
        for line, content in enumerate(expected):
            with self.subTest(line=line):
                path.write_bytes(original)
                await self.success('read', {'path': str(path)})
                await self.success('edit', {'path': str(path), 'insert_line': line, 'new_string': 'added'})
                self.assertEqual(path.read_bytes(), content)

    async def test_insert_keeps_empty_files_and_final_newline_semantics(self):
        path = self.root / 'insert-boundary.txt'
        cases = [(b'', 0, b'added'), (b'one', 1, b'one\nadded')]
        for ending in (b'\n', b'\r\n', b'\r'):
            cases.extend(((b'one' + ending, 1, b'one' + ending + b'added' + ending),
                          (ending, 1, ending + b'added' + ending)))
        for original, line, expected in cases:
            with self.subTest(original=original):
                path.write_bytes(original)
                await self.success('read', {'path': str(path)})
                await self.success('edit', {'path': str(path), 'insert_line': line, 'new_string': 'added'})
                self.assertEqual(path.read_bytes(), expected)

    async def test_write_and_insert_do_not_double_translate_crlf(self):
        path = self.root / '中文 文件.txt'
        await self.success('write', {'path': str(path), 'content': 'first\r\nlast\r\n'})
        await self.success('edit', {'path': str(path), 'insert_line': 1, 'new_string': 'middle'})
        self.assertEqual(path.read_bytes(), b'first\r\nmiddle\r\nlast\r\n')

    async def test_external_change_after_read_rejects_edit_and_write(self):
        for name, args in (('edit', {'old_string': 'original', 'new_string': 'replacement'}),
                           ('write', {'content': 'replacement'})):
            with self.subTest(tool=name):
                path = self.root / 'stale.txt'
                path.write_bytes(b'original\r\n')
                await self.success('read', {'path': str(path)})
                path.write_bytes(b'external\r\n')
                result = await self.call(name, {'path': str(path), **args})
                self.assertTrue(result.is_error)
                self.assertIn('FS_STALE', result.content[0].text)
                self.assertEqual(path.read_bytes(), b'external\r\n')

    async def test_observed_absence_cannot_overwrite_new_external_file(self):
        path = self.root / 'appeared.txt'
        self.assertTrue((await self.call('read', {'path': str(path)})).is_error)
        path.write_bytes(b'external')
        result = await self.call('write', {'path': str(path), 'content': 'replacement'})
        self.assertTrue(result.is_error)
        self.assertEqual(path.read_bytes(), b'external')

    async def test_unobserved_existing_file_cannot_be_overwritten(self):
        path = self.root / 'unseen.txt'
        path.write_bytes(b'keep')
        result = await self.call('write', {'path': str(path), 'content': 'replacement'})
        self.assertTrue(result.is_error)
        self.assertIn('FS_EXISTS', result.content[0].text)
        self.assertEqual(path.read_bytes(), b'keep')

    async def test_observation_is_owned_by_the_reading_session(self):
        path = self.root / 'owned.txt'
        path.write_bytes(b'keep')
        await self.success('read', {'path': str(path)})
        other = self.sessions.create()
        self.addCleanup(fs_observation.forget_session, other.id)
        result = await self.call('edit', {'path': str(path), 'old_string': 'keep', 'new_string': 'replacement'}, other)
        self.assertTrue(result.is_error)
        self.assertIn('FS_NOT_OBSERVED', result.content[0].text)
        self.assertEqual(path.read_bytes(), b'keep')
