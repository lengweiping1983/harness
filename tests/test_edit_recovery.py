"""验证精确编辑失败后的可信重读与连续编辑反馈。"""
import json
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path

from harness import Harness, HarnessConfig
from harness.i18n import tr
from harness.llm.transport import Transport
from harness.llm.types import ToolUseBlock
from harness.session.events import AppendOp
from harness.session.store import SessionStore
from harness.tools.builtin import fs_observation
from harness.tools.builtin.fs_tools import build_fs_tools
from harness.tools.registry import ToolRegistry, ToolRunContext


class EditRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.sessions = SessionStore(str(self.root / 'sessions'))
        self.session = self.sessions.create()
        self.addCleanup(fs_observation.forget_session, self.session.id)
        self.path = self.root / 'Level.js'
        self.source = '  this.dust = new THREE.Points(geo, mat)\n  this.scene.add(this.dust)\n  this._track(this.dust)\n'
        self.path.write_bytes(self.source.encode())
        self.registry = ToolRegistry()
        for tool in build_fs_tools(str(self.root)):
            self.registry.register(tool)

    async def call(self, name, args=None, *, session=None, persist=False, sdk=False):
        session = session or self.session
        ctx = ToolRunContext(call_id='edit-recovery', session_id=session.id,
            cwd=str(self.root), turn=1, step=1, services={} if sdk else {'session': session})
        result = await self.registry.execute_call(ToolUseBlock('edit-recovery', name,
            {'path': str(self.path), **(args or {})}), ctx)
        if persist:
            await session.append('tool/result', {'name': name, 'results': [
                {'tool_use_id': 'edit-recovery', 'content': result.content[0].text, 'is_error': result.is_error}],
                'display': result.display,
                'file_observation': result.data.get('_fs_observation') if isinstance(result.data, dict) else None},
                surface_op=AppendOp())
        return result

    async def mismatch(self, **kwargs):
        return await self.call('edit', {'old_string': self.source.replace('this.dust =', 'const dust ='),
            'new_string': self.source.replace('this._track', 'this._trackDisposable')}, **kwargs)

    async def test_mismatch_revokes_edit_and_whole_file_overwrite_until_read(self):
        await self.call('read')
        failed = await self.mismatch()
        self.assertTrue(failed.is_error)
        self.assertIn('FS_EDIT_NOT_FOUND', failed.content[0].text)
        retry = await self.call('edit', {'old_string': 'this._track', 'new_string': 'this._trackDisposable'})
        self.assertTrue(retry.is_error, '匹配失败后不能未重读即继续编辑')
        self.assertIn('FS_NOT_OBSERVED', retry.content[0].text)
        overwrite = await self.call('write', {'content': 'incorrect overwrite'})
        self.assertTrue(overwrite.is_error)
        self.assertEqual(self.path.read_bytes(), self.source.encode())
        reread = await self.call('read', failed.display['details']['suggested_read'])
        self.assertFalse(reread.is_error, reread.content)
        fixed = await self.call('edit', {'old_string': 'this._track', 'new_string': 'this._trackDisposable'})
        self.assertFalse(fixed.is_error, fixed.content)
        self.assertIn('this._trackDisposable(this.dust)', self.path.read_text())

    async def test_failure_keeps_structured_diagnostics_without_appending_dict_to_model_text(self):
        await self.call('read')
        result = await self.mismatch()
        self.assertEqual(result.display.get('code'), 'FS_EDIT_NOT_FOUND')
        details = result.display['details']
        self.assertEqual(details['first_difference']['kind'], 'source_line_absent')
        self.assertFalse(details['suggested_read']['line_numbers'])
        self.assertNotIn("'candidate_ranges':", result.content[0].text)

    async def test_revoked_observation_remains_revoked_after_session_reload(self):
        await self.call('read', persist=True)
        await self.mismatch(persist=True)
        restored = SessionStore(str(self.root / 'sessions')).get(self.session.id)
        blocked = await self.call('edit', {'old_string': 'this._track', 'new_string': 'changed'}, session=restored)
        self.assertTrue(blocked.is_error)
        self.assertIn('FS_NOT_OBSERVED', blocked.content[0].text)

    async def test_sdk_recovery_requires_a_new_read(self):
        await self.call('read', sdk=True)
        await self.mismatch(sdk=True)
        blocked = await self.call('edit', {'old_string': 'this._track', 'new_string': 'changed'}, sdk=True)
        self.assertTrue(blocked.is_error)
        self.assertIn('FS_NOT_OBSERVED', blocked.content[0].text)

    async def test_raw_read_can_be_copied_into_edit_without_changing_source_characters(self):
        source = 'first\r\n  value = "a\u2028b"\r\nlast\r\n'
        self.path.write_bytes(source.encode())
        result = await self.call('read', {'offset': 2, 'limit': 1, 'line_numbers': False})
        self.assertFalse(result.is_error, result.content)
        self.assertEqual(result.data['content'], '  value = "a\u2028b"\n')
        fixed = await self.call('edit', {'old_string': result.data['content'], 'new_string': '  value = "updated"\n'})
        self.assertFalse(fixed.is_error, fixed.content)
        self.assertEqual(self.path.read_bytes(), b'first\r\n  value = "updated"\r\nlast\r\n')

    async def test_insert_returns_current_source_for_followup_edit(self):
        await self.call('read')
        result = await self.call('edit', {'insert_line': 1, 'new_string': '  this.dust.name = "dust"'})
        self.assertFalse(result.is_error, result.content)
        self.assertIn('```text\n' + self.path.read_text().rstrip('\n'), result.content[0].text)

    async def test_failure_does_not_invalidate_other_files_or_sessions(self):
        await self.call('read')
        other_path = self.root / 'other.js'
        other_path.write_text('other')
        await self.call('read', {'path': str(other_path)})
        other_session = self.sessions.create()
        self.addCleanup(fs_observation.forget_session, other_session.id)
        await self.call('read', session=other_session)
        await self.mismatch()
        result = await self.call('edit', {'path': str(other_path), 'old_string': 'other', 'new_string': 'updated'})
        self.assertFalse(result.is_error, result.content)
        result = await self.call('edit', {'old_string': 'this._track', 'new_string': 'this._trackDisposable'},
            session=other_session)
        self.assertFalse(result.is_error, result.content)

    async def test_new_text_elsewhere_is_not_treated_as_completed_edit(self):
        await self.call('read')
        result = await self.call('edit', {'old_string': 'missing original', 'new_string': 'this.dust'})
        self.assertTrue(result.is_error)
        self.assertTrue(result.display['details']['new_text_present'])
        self.assertEqual(self.path.read_bytes(), self.source.encode())

    async def test_recovery_read_restores_durable_observation_and_keeps_version_guard(self):
        await self.call('read', persist=True)
        await self.mismatch(persist=True)
        await self.call('read', {'line_numbers': False}, persist=True)
        restored = SessionStore(str(self.root / 'sessions')).get(self.session.id)
        result = await self.call('edit', {'old_string': 'this._track', 'new_string': 'this._trackDisposable'},
            session=restored)
        self.assertFalse(result.is_error, result.content)
        self.path.write_text('external change')
        result = await self.call('edit', {'old_string': 'external change', 'new_string': 'overwritten'},
            session=restored)
        self.assertTrue(result.is_error)
        self.assertEqual(result.display['code'], 'FS_STALE')
        self.assertEqual(self.path.read_text(), 'external change')

    async def test_explicitly_disabled_observation_policy_keeps_host_semantics(self):
        registry = ToolRegistry()
        for tool in build_fs_tools(str(self.root), observation_policy=False):
            registry.register(tool)
        self.registry = registry
        await self.mismatch()
        result = await self.call('edit', {'old_string': 'this._track', 'new_string': 'this._trackDisposable'})
        self.assertFalse(result.is_error, result.content)
        self.assertEqual(self.session.file_observations.records, {})

    async def test_raw_read_preserves_blank_lines_tabs_eof_and_fence_content(self):
        for source in ('', '\n', '\tfirst\n\nlast', '```\n\n````\n', 'first\r\nlast\r\n'):
            with self.subTest(source=repr(source)):
                self.path.write_bytes(source.encode())
                result = await self.call('read', {'line_numbers': False})
                self.assertFalse(result.is_error, result.content)
                self.assertEqual(result.data['content'], source.replace('\r\n', '\n'))
                self.assertFalse(result.data['truncated'])
                if '````' in source:
                    self.assertIn('`````text\n', result.content[0].text)

    async def test_numbered_read_uses_the_same_line_boundaries_as_edit(self):
        self.path.write_text('first\nvalue = "a\u2028b"\nlast\n', encoding='utf-8', newline='')
        result = await self.call('read', {'offset': 2, 'limit': 1})
        self.assertFalse(result.is_error, result.content)
        self.assertEqual(result.data['total_lines'], 3)
        self.assertTrue(result.data['content'].startswith('     2\tvalue = "a\u2028b"'))

    async def test_ambiguous_match_stays_rejected_and_explicit_replace_all_still_works(self):
        await self.call('read')
        result = await self.call('edit', {'old_string': 'this.dust', 'new_string': 'dust'})
        self.assertTrue(result.is_error)
        self.assertEqual(result.display['code'], 'FS_AMBIGUOUS_EDIT')
        self.assertEqual(self.path.read_bytes(), self.source.encode())
        result = await self.call('edit', {'old_string': 'this.dust', 'new_string': 'dust', 'replace_all': True})
        self.assertFalse(result.is_error, result.content)
        self.assertEqual(result.data['replacements'], 3)

    async def test_long_insert_explicitly_marks_incomplete_current_context(self):
        await self.call('read')
        result = await self.call('edit', {'insert_line': 1,
            'new_string': '\n'.join(f'  inserted_{index}()' for index in range(80))})
        self.assertFalse(result.is_error, result.content)
        self.assertIn('inserted_79()', self.path.read_text())
        self.assertNotIn('inserted_79()', result.content[0].text)
        self.assertIn(tr('python.fs_edit_diagnostics.context_incomplete'), result.content[0].text)

    async def test_raw_read_truncation_is_marked_outside_source(self):
        self.path.write_text('x' * 17000 + '\n', encoding='utf-8', newline='')
        result = await self.call('read', {'line_numbers': False})
        self.assertFalse(result.is_error, result.content)
        self.assertTrue(result.data['truncated'])
        self.assertEqual(result.data['content'], 'x' * 16000)
        self.assertTrue(result.data['notice'])
        self.assertIn('```' + result.data['notice'], result.content[0].text)


class EditRecoveryTransport(Transport):
    def __init__(self):
        self.requests = []
        self.calls = [
            ('read', {'path': 'file.js'}),
            ('edit', {'path': 'file.js', 'old_string': 'const dust = 1', 'new_string': 'this.dust = 2'}),
            ('edit', {'path': 'file.js', 'old_string': 'this.dust = 1', 'new_string': 'this.dust = 2'}),
            ('read', {'path': 'file.js', 'line_numbers': False}),
            ('edit', {'path': 'file.js', 'old_string': 'this.dust = 1', 'new_string': 'this.dust = 2'}),
        ]

    async def post_stream(self, url, *, headers, body, timeout):
        """顺序返回失配、未经重读的重试及正确恢复，不调用外部模型。"""
        step = len(self.requests)
        self.requests.append(json.loads(body))
        if step < len(self.calls):
            name, args = self.calls[step]
            delta = {'role': 'assistant', 'tool_calls': [{'index': 0, 'id': f'edit-{step}',
                'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}]}
            reason = 'tool_calls'
        else:
            delta, reason = {'role': 'assistant', 'content': 'recovery completed'}, 'stop'
        for chunk in ({'choices': [{'delta': delta, 'finish_reason': None}]},
                      {'choices': [{'delta': {}, 'finish_reason': reason}]}):
            yield ('data: ' + json.dumps(chunk) + '\n\n').encode()
        yield b'data: [DONE]\n\n'


class EditRecoveryAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_agent_persists_invalidation_and_returns_errors_to_the_next_model_request(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        path = root / 'file.js'
        path.write_bytes(b'this.dust = 1\r\n')
        flags = {item.name: False for item in fields(HarnessConfig) if item.name.startswith('enable_')}
        flags.update(enable_fs=True, enable_fs_observation_policy=True)
        transport = EditRecoveryTransport()
        app = Harness(HarnessConfig(api_key='fixture-only', home=root / 'state', cwd=root,
            model='fixture-model', max_steps=6, max_retries=0, tool_schema_mode='all', **flags), transport=transport)
        self.addAsyncCleanup(app.close)
        session = app.create_session()
        await app.run_async(session, '核对精确编辑恢复流程')
        self.assertEqual(path.read_bytes(), b'this.dust = 2\r\n')
        results = session.events_of_type('tool/result')
        self.assertEqual([row.data['results'][0]['is_error'] for row in results], [False, True, True, False, False])
        self.assertEqual(results[1].data['file_observation']['kind'], 'invalidated')
        self.assertEqual(results[1].data['display']['code'], 'FS_EDIT_NOT_FOUND')
        self.assertEqual(results[2].data['display']['code'], 'FS_NOT_OBSERVED')
        feedback = [message for message in transport.requests[3]['messages'] if message['role'] == 'tool']
        self.assertTrue(any('FS_NOT_OBSERVED' in str(message['content']) for message in feedback))
        restored = SessionStore(str(root / 'state')).get(session.id)
        self.assertEqual(restored.file_observations.records[fs_observation.target_key(path)][1],
            fs_observation.file_version(path))
