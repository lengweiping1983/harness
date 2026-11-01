"""图片原记录损坏时交付必须在下载和文件写入前停止。"""
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image
from harness.core.errors import HarnessError
from harness.core.language import current_language
from harness.execution.config import ExecutionConfig, ProjectConfig
from harness.execution.runtime import ExecutionRuntime
from harness.i18n import tr


class ExecutionImageIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name).resolve() / 'project'
        self.project.mkdir()
        self.runtime = ExecutionRuntime(ExecutionConfig(Path(temporary.name) / 'state', {'project': ProjectConfig(self.project, writable=True)}))
        self.addAsyncCleanup(self.runtime.close)
        session = self.runtime.store.open_session('local', 'project', 'fixture', str(self.project))
        self.ctx = SimpleNamespace(session_id=session['session_id'], services={'principal': 'local'})
        self.args = {'session_id': self.ctx.session_id, 'path': 'new.png', 'prompt': '用户原始提示词'}
        self.request = await self.runtime._image_request(self.args, self.ctx)
        self.deliver = {'session_id': self.ctx.session_id, 'image_id': self.request['image_id'],
                        'file': {'file_id': 'fixture-file', 'download_url': 'https://example.invalid/fixture', 'mime_type': 'image/png'}}

    async def test_malformed_identity_state_and_shape_preserve_record_without_side_effects(self):
        cases = [None, [], {}, {**self.request, 'image_id': 'other'}, {**self.request, 'status': 'unknown'},
                 {**self.request, 'created_at': True}, {**self.request, 'created_at': float('nan')},
                 {**self.request, 'path': None}, {**self.request, 'prompt': ''},
                 {**self.request, 'status': 'delivered'}, {**self.request, 'delivery': {}},
                 {**self.request, 'status': 'delivered', 'delivered_at': False, 'delivery': {}},
                 {**self.request, 'status': 'delivered', 'delivered_at': 1, 'delivery': []}]
        for value in cases:
            raw = json.dumps(value)
            with self.runtime.store.db:
                self.runtime.store.db.execute('UPDATE image_requests SET data=? WHERE id=?', (raw, self.request['image_id']))
            with patch('harness.execution.runtime.download_image') as download, patch('harness.execution.runtime.apply_changes') as write:
                for language in ('en', 'zh-CN', 'en'):
                    with self.subTest(value=value, language=language), current_language.set(language):
                        for read in (lambda: self.runtime.store.images(self.ctx.session_id),
                                     lambda: self.runtime.store.images(self.ctx.session_id, self.request['image_id'])):
                            with self.assertRaises(HarnessError) as error:
                                read()
                            self.assertEqual(error.exception.code, 'execution/store-invalid')
                            self.assertEqual(error.exception.message, tr('execution.store.invalid_record'))
                        with self.assertRaises(HarnessError) as error:
                            await self.runtime._image_deliver(self.deliver, self.ctx)
                        self.assertEqual(error.exception.code, 'execution/store-invalid')
                download.assert_not_called()
                write.assert_not_called()
            self.assertFalse((self.project / 'new.png').exists())
            self.assertEqual(self.runtime.store.db.execute('SELECT data FROM image_requests WHERE id=?', (self.request['image_id'],)).fetchone()['data'], raw)

    async def test_real_image_delivery_and_scope_keep_original_identity_and_prompt(self):
        content = io.BytesIO()
        Image.new('RGB', (2, 3)).save(content, format='PNG')
        with patch('harness.execution.runtime.download_image', return_value=content.getvalue()) as download:
            delivered = await self.runtime._image_deliver(self.deliver, self.ctx)
            download.assert_called_once()
        self.assertEqual((self.project / 'new.png').read_bytes(), content.getvalue())
        self.assertEqual(delivered['image_id'], self.request['image_id'])
        self.assertEqual(delivered['prompt'], self.args['prompt'])
        self.assertEqual(delivered['status'], 'delivered')
        self.assertEqual(delivered['delivery']['width'], 2)
        self.assertEqual(delivered['delivery']['height'], 3)
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                self.assertEqual(self.runtime.store.images(self.ctx.session_id, self.request['image_id']), [delivered])
        self.assertEqual(self.runtime.store.images('other'), [])
        with self.assertRaises(HarnessError) as error:
            self.runtime.store.images('other', self.request['image_id'])
        self.assertEqual(error.exception.code, 'execution/image-not-found')

    def png(self):
        content = io.BytesIO()
        Image.new('RGB', (2, 3)).save(content, format='PNG')
        return content.getvalue()

    async def test_final_record_failure_keeps_unknown_intent_and_never_replays_delivery(self):
        failure = OSError('Original final state failure')
        content = self.png()
        with patch('harness.execution.runtime.download_image', return_value=content), patch.object(
                self.runtime.store, 'image_delivered', side_effect=failure):
            with self.assertRaises(OSError) as error:
                await self.runtime._image_deliver(self.deliver, self.ctx)
        self.assertIs(error.exception, failure)
        self.assertEqual((self.project/'new.png').read_bytes(), content)
        saved = self.runtime.store.images(self.ctx.session_id, self.request['image_id'])[0]
        self.assertEqual(saved['status'], 'delivery_unconfirmed')
        self.assertEqual(saved['delivery']['bytes'], len(content))
        raw = self.runtime.store.db.execute('SELECT data FROM image_requests WHERE id=?',
                                          (self.request['image_id'],)).fetchone()['data']
        self.assertNotIn('download_url', raw)
        # 即使文件后来由用户移除，未知意图也不能被解释为重新交付授权。
        (self.project/'new.png').unlink()
        with patch('harness.execution.runtime.download_image') as download, patch('harness.execution.runtime.apply_changes') as write:
            for language in ('en', 'zh-CN', 'en'):
                with current_language.set(language):
                    with self.assertRaises(HarnessError) as error:
                        await self.runtime._image_deliver(self.deliver, self.ctx)
                    self.assertEqual(error.exception.code, 'execution/image-delivery-unconfirmed')
                    self.assertEqual(error.exception.message, tr('execution.runtime.image_delivery_unconfirmed'))
            download.assert_not_called();write.assert_not_called()
        self.assertEqual(self.runtime.store.db.execute('SELECT data FROM image_requests WHERE id=?',
                        (self.request['image_id'],)).fetchone()['data'], raw)

    async def test_intent_persistence_failure_prevents_file_write(self):
        failure = OSError('Original intent failure')
        with patch('harness.execution.runtime.download_image', return_value=self.png()), patch.object(
                self.runtime.store, 'image_delivery_begin', side_effect=failure), patch(
                'harness.execution.runtime.apply_changes') as write:
            with self.assertRaises(OSError) as error:
                await self.runtime._image_deliver(self.deliver, self.ctx)
            self.assertIs(error.exception, failure)
            write.assert_not_called()
        self.assertFalse((self.project/'new.png').exists())
        self.assertEqual(self.runtime.store.images(self.ctx.session_id)[0]['status'], 'pending')

    async def test_failed_file_write_preserves_unknown_intent_and_original_failure(self):
        failure = OSError('Original file failure')
        with patch('harness.execution.runtime.download_image', return_value=self.png()), patch(
                'harness.execution.runtime.apply_changes', side_effect=failure):
            with self.assertRaises(OSError) as error:
                await self.runtime._image_deliver(self.deliver, self.ctx)
        self.assertIs(error.exception, failure)
        self.assertEqual(self.runtime.store.images(self.ctx.session_id)[0]['status'], 'delivery_unconfirmed')
        self.assertFalse((self.project/'new.png').exists())

    async def test_deep_delivery_corruption_is_rejected_and_original_bytes_preserved(self):
        import copy
        with patch('harness.execution.runtime.download_image', return_value=self.png()):
            delivered = await self.runtime._image_deliver(self.deliver, self.ctx)
        changes = [('width', True), ('height', 0), ('bytes', -1), ('sha256', 'bad'),
                   ('mime_type', 'image/jpeg'), ('file_id', None), ('changed_files', ['other.png']),
                   ('versions', {'new.png': 'bad'}), ('backup_id', '../elsewhere')]
        for key, bad in changes:
            value = copy.deepcopy(delivered);value['delivery'][key] = bad
            raw = json.dumps(value)
            with self.runtime.store.db:
                self.runtime.store.db.execute('UPDATE image_requests SET data=? WHERE id=?',
                                              (raw, self.request['image_id']))
            for language in ('en', 'zh-CN', 'en'):
                with self.subTest(key=key, language=language), current_language.set(language):
                    with self.assertRaises(HarnessError) as error:
                        self.runtime.store.images(self.ctx.session_id)
                    self.assertEqual(error.exception.code, 'execution/store-invalid')
            self.assertEqual(self.runtime.store.db.execute('SELECT data FROM image_requests WHERE id=?',
                            (self.request['image_id'],)).fetchone()['data'], raw)
