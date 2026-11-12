"""持久化验证命令必须保持登记协议，不执行损坏记录。"""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from harness.core.errors import HarnessError
from harness.core.language import current_language
from harness.execution.config import ExecutionConfig, ProjectConfig
from harness.execution.runtime import ExecutionRuntime
from harness.i18n import tr


class VerificationRecordTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        project = root / 'project'
        project.mkdir()
        self.runtime = ExecutionRuntime(ExecutionConfig(root / 'state', {'project': ProjectConfig(project, commands=True)}))
        self.addAsyncCleanup(self.runtime.close)
        sid = self.runtime.store.open_session('local', 'project', 'fixture', str(project))['session_id']
        self.ctx = SimpleNamespace(session_id=sid, services={'principal': 'local'})
        self.args = {'session_id': sid, 'verification_id': 'check'}
        self.runtime._start_job = AsyncMock(return_value={'fixture': True})

    async def test_invalid_command_or_identifier_never_starts_job_or_rewrites_record(self):
        cases = [('check', '{'), ('check', 'null'), ('check', '"echo"'), ('check', '{"echo":true}'),
                 ('check', '[]'), ('check', '[true]'), ('check', '[null]'), ('check', '[""]'),
                 ('check', json.dumps(['x' * 4097])), ('check', json.dumps(['x'] * 257)),
                 ('invalid name', '["fixture"]'), ('', '["fixture"]')]
        for name, raw in cases:
            with self.runtime.store.db:
                self.runtime.store.db.execute('DELETE FROM session_verification WHERE session_id=?', (self.ctx.session_id,))
                self.runtime.store.db.execute('INSERT INTO session_verification VALUES(?,?,?)', (self.ctx.session_id, name, raw))
            for language in ('en', 'zh-CN', 'en'):
                with self.subTest(name=name, raw=raw, language=language), current_language.set(language):
                    with self.assertRaises(HarnessError) as error:
                        await self.runtime._verify(self.args, self.ctx)
                    self.assertEqual(error.exception.code, 'execution/store-invalid')
                    self.assertEqual(error.exception.message, tr('execution.store.invalid_record'))
                    self.runtime._start_job.assert_not_awaited()
            row = self.runtime.store.db.execute('SELECT name,command FROM session_verification WHERE session_id=?', (self.ctx.session_id,)).fetchone()
            self.assertEqual((row['name'], row['command']), (name, raw))

    async def test_valid_registration_preserves_argument_boundaries_and_session_scope(self):
        command = ['fixture-program', 'literal argument', '用户原文']
        await self.runtime._verification_register({**self.args, 'command': command}, self.ctx)
        self.assertEqual(self.runtime.store.verification(self.ctx.session_id), {'check': tuple(command)})
        self.assertEqual(self.runtime.store.verification('other'), {})
        result = await self.runtime._verify(self.args, self.ctx)
        self.assertEqual(result, {'fixture': True})
        self.assertEqual(self.runtime._start_job.await_args.args[2], command)
        self.runtime._start_job.assert_awaited_once()
