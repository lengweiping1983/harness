"""附件只接受当前完整原件，错误不丢附件、不覆盖工作区已有文件。"""
import asyncio
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from harness.core.language import current_language
from harness.i18n import tr
from harness.web.attachment_store import AttachmentStore, AttachmentStorageError, is_attachment_id
from harness.web.attachments import AttachmentRejected, admit, sniff_media_type
from harness.web.materialize import materialize, resolve_refs
from harness.web.server import Handler
from harness.web.router import Request
from harness.web.api.attachments import build_router
from tests.filesystem_links import directory_link


class AttachmentIntegrityTests(unittest.TestCase):
    def test_utf8_probe_boundaries_preserve_complete_attachment_bytes(self):
        for language in ('en', 'zh-CN'):
            with current_language.set(language), tempfile.TemporaryDirectory() as folder:
                store = AttachmentStore(folder)
                for character in ('é', '中', '😀'):
                    for prefix in range(4):
                        raw = ('a' * prefix + character * 5000).encode()
                        self.assertEqual(sniff_media_type(raw), 'text/plain')
                        attachment = admit(raw, name='原文')
                        self.assertEqual(attachment.media_type, 'text/plain')
                        store.put(raw, attachment)
                        self.assertEqual(store.read(attachment.attachment_id), raw)
                for raw in (b'\xe4\xb8', b'\xf0\x9f', b'a\xffb', b'a\0b'):
                    self.assertIsNone(sniff_media_type(raw))
                    with self.assertRaises(AttachmentRejected) as failure:
                        admit(raw, name='original')
                    self.assertEqual(failure.exception.code, 'attachment/unsupported')

    def test_current_store_and_corrupt_originals_in_both_languages(self):
        for language in ('en', 'zh-CN'):
            with current_language.set(language), tempfile.TemporaryDirectory() as folder:
                store = AttachmentStore(folder)
                raw = '原始正文😀'.encode()
                attachment = admit(raw, name='原文.txt')
                row = store.put(raw, attachment)
                original_meta = row.meta_path.read_bytes()
                self.assertEqual(store.read(attachment.attachment_id), raw)
                self.assertEqual(store.describe(attachment.attachment_id).attachment, attachment)
                self.assertEqual(store.put(raw, attachment).path, row.path)
                self.assertFalse(is_attachment_id(attachment.attachment_id + '\n'))
                variants = [b'{broken', b'\xff', b'[]', b'{}']
                meta = attachment.to_json()
                for key, value in [('attachmentId', '0' * 64), ('name', '../outside.txt'), ('mediaType', 'text/html'), ('size', True), ('size', len(raw) + 1), ('isImage', 'false')]:
                    variants.append(json.dumps({**meta, key: value}).encode())
                variants.append(json.dumps({key: value for key, value in meta.items() if key != 'size'}).encode())
                for changed in variants:
                    row.meta_path.write_bytes(changed)
                    for action in (lambda: store.describe(attachment.attachment_id), lambda: store.put(raw, attachment)):
                        with self.assertRaises(AttachmentStorageError) as failure:
                            action()
                        self.assertEqual(failure.exception.message, tr('ui.collaboration.error.attachment/invalid-storage'))
                    self.assertEqual(row.path.read_bytes(), raw)
                    self.assertEqual(row.meta_path.read_bytes(), changed)
                row.meta_path.unlink()
                with self.assertRaises(AttachmentStorageError):
                    store.describe(attachment.attachment_id)
                self.assertFalse(row.meta_path.exists())
                row.meta_path.write_bytes(original_meta)
                row.path.write_bytes(b'corrupt')
                with self.assertRaises(AttachmentStorageError):
                    store.read(attachment.attachment_id)
                with self.assertRaises(AttachmentStorageError):
                    store.put(raw, attachment)
                self.assertEqual(row.path.read_bytes(), b'corrupt')
                self.assertEqual(row.meta_path.read_bytes(), original_meta)
                self.assertFalse(store.verify(attachment.attachment_id))

    def test_materialization_never_overwrites_collisions_and_preserves_binary(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = AttachmentStore(root / 'store')
            raw = b'\xff\x00original binary'
            attachment = admit(raw, name='fixture.txt')
            store.put(raw, attachment)
            dest = root / 'workspace'
            dest.mkdir()
            existing = dest / attachment.name
            existing.write_bytes(b'user original')
            suffixed = dest / ('fixture-' + attachment.attachment_id[:8] + '.txt')
            suffixed.write_bytes(b'user edited')
            with ThreadPoolExecutor(max_workers=4) as executor:
                paths = list(executor.map(lambda _: materialize(store, attachment, dest), range(8)))
            self.assertTrue(all(Path(path).read_bytes() == raw for path in paths))
            self.assertEqual(existing.read_bytes(), b'user original')
            self.assertEqual(suffixed.read_bytes(), b'user edited')
            refs = resolve_refs(store, [attachment.to_json()], workspace_root=root / 'second')
            self.assertIsNone(refs.parts[0].text)
            self.assertEqual(Path(refs.parts[0].local_path).read_bytes(), raw)
            linked = root / 'linked-workspace'
            linked.mkdir()
            directory_link(linked / '.harness', dest)
            with self.assertRaises(AttachmentStorageError):
                resolve_refs(store, [attachment.to_json()], workspace_root=linked)
            with patch.object(Path, 'mkdir', side_effect=PermissionError('private diagnostic')):
                with self.assertRaises(AttachmentStorageError) as failure:
                    materialize(store, attachment, root / 'unwritable')
                self.assertNotIn('private diagnostic', failure.exception.message)

    def test_fetch_and_submission_reject_corruption_and_scope_files_to_session(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = AttachmentStore(root / 'store')
            attachment = admit(b'binary\x00', name='fixture.pdf')
            row = store.put(b'binary\x00', attachment)
            session = SimpleNamespace(header=SimpleNamespace(cwd=str(root / 'session')))
            runtime = SimpleNamespace(submit=Mock())
            harness = SimpleNamespace(config=SimpleNamespace(language='en'), get_session=Mock(return_value=session), runtime=runtime)
            ctx = SimpleNamespace(harness=harness, shared_access=None, _attachment_store=store, workspace_root=lambda: root / 'other')
            handler = SimpleNamespace(ctx=ctx, harness=harness)
            handler._prepare_turn_input = lambda *args: Handler._prepare_turn_input(handler, *args)
            text, prompt = handler._prepare_turn_input('session', 'Original text', [attachment.to_json()])
            self.assertEqual(prompt['text'], 'Original text')
            self.assertTrue(Path(prompt['parts'][0]['local_path']).is_relative_to((root / 'session').resolve()))
            self.assertFalse((root / 'other').exists())
            fetch, params = build_router(ctx).resolve('GET', '/api/v1/attachments/' + attachment.attachment_id)
            request = Request('GET', '/', {}, {}, b'')
            self.assertEqual(fetch(request, **params).body, b'binary\x00')
            row.path.write_bytes(b'broken')
            with self.assertRaises(AttachmentStorageError):
                fetch(request, **params)
            for refs in ([attachment.to_json()], [{'attachmentId': '0' * 64}], ['invalid'], 'invalid'):
                conn = SimpleNamespace(send_json=AsyncMock())
                asyncio.run(Handler._handle_ws_payload_in_language(handler, conn, 'session', {'action':'send', 'text':'Original text', 'attachments':refs, 'request_id':'receipt'}))
                runtime.submit.assert_not_called()
                conn.send_json.assert_awaited_once()
                error = conn.send_json.call_args.args[0]
                self.assertFalse(error['submitted'])
                self.assertEqual(error['request_id'], 'receipt')
                self.assertTrue(error['code'].startswith('attachment/'))
