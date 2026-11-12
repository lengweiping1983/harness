"""同目录发布复用同一临时原件，失败保留旧文件及外部文件。"""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from harness.core import fsatomic
from harness.session.store import SessionStore
from harness.web.attachment_store import AttachmentStore
from harness.web.attachments import admit


class AtomicWriteTests(unittest.TestCase):
    def test_text_bytes_and_session_header_roundtrip(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for value in ('原文😀\nnext', b'\x00\xfforiginal'):
                path = root / 'nested' / 'value'
                fsatomic.atomic_write(path, value)
                expected = value.replace('\n', os.linesep).encode('utf-8') if isinstance(value, str) else value
                self.assertEqual(path.read_bytes(), expected)
                self.assertEqual(list(path.parent.iterdir()), [path])
            store = SessionStore(root)
            session = store.create(title='原文😀')
            self.assertEqual(SessionStore(root).get(session.id).header.title, '原文😀')

    def test_failed_publication_and_flush_preserve_original_and_external_temp(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'value'
            external = path.with_name('.tmp-external.tmp')
            for operation in ('replace', 'fsync'):
                for failure in (OSError('failure'), KeyboardInterrupt()):
                    with self.subTest(operation=operation, failure=type(failure).__name__):
                        path.write_bytes(b'original')
                        external.write_bytes(b'external')
                        with patch.object(fsatomic.os, operation, side_effect=failure):
                            with self.assertRaises(type(failure)) as caught:
                                fsatomic.atomic_write(path, b'changed')
                        self.assertIs(caught.exception, failure)
                        self.assertEqual(path.read_bytes(), b'original')
                        self.assertEqual(external.read_bytes(), b'external')
                        self.assertEqual(set(path.parent.iterdir()), {path, external})

    def test_windows_retry_publishes_same_bytes_once_for_attachment_and_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            raw = b'original\x00\xff'
            attachment = admit(raw, name='fixture.pdf')
            store = AttachmentStore(folder)
            attempts = []
            def replace(source, target):
                attempts.append((source, target, source.read_bytes()))
                if len(attempts) <= 2:
                    failure = PermissionError('sharing violation')
                    failure.winerror = 32
                    raise failure
                os.replace(source, target)
            with patch.object(fsatomic, 'os', wraps=os) as native, patch.object(fsatomic.time, 'sleep') as sleep:
                native.name = 'nt'
                native.replace.side_effect = replace
                stored = store.put(raw, attachment)
                self.assertEqual(native.fdopen.call_count, 2)
                self.assertEqual(sleep.call_count, 2)
            self.assertEqual(attempts[0], attempts[1])
            self.assertEqual(attempts[1], attempts[2])
            self.assertEqual(store.read(attachment.attachment_id), raw)
            self.assertEqual(store.describe(attachment.attachment_id).attachment, attachment)
            self.assertEqual(set(stored.path.parent.iterdir()), {stored.path, stored.meta_path})

    def test_only_windows_transient_permission_errors_retry_with_finite_limit(self):
        for platform, code, count in [('posix', 32, 1), ('nt', 13, 1), ('nt', 5, 4), ('nt', 32, 4)]:
            with self.subTest(platform=platform, code=code), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / 'value'
                path.write_bytes(b'original')
                failure = PermissionError('denied')
                failure.winerror = code
                with patch.object(fsatomic, 'os', wraps=os) as native, patch.object(fsatomic.time, 'sleep') as sleep:
                    native.name = platform
                    native.replace.side_effect = failure
                    with self.assertRaises(PermissionError) as caught:
                        fsatomic.atomic_write(path, 'changed')
                    self.assertIs(caught.exception, failure)
                    self.assertEqual(native.replace.call_count, count)
                    self.assertEqual(native.fdopen.call_count, 1)
                    self.assertEqual(sleep.call_count, count - 1)
                self.assertEqual(path.read_bytes(), b'original')
                self.assertEqual(list(path.parent.iterdir()), [path])
