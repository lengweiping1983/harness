"""JSON 声明共用发布器，保持原字节格式及失败后的磁盘、内存快照。"""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from harness.core import fsatomic
from harness.connectors.store import ConnectorRecord, ConnectorStore
from harness.plugins.state import EscStateStore
from harness.web.api.settings import SettingsStore


def state_case(root, kind):
    """为三个真实存储提供等价的公开写入和快照入口。"""
    if kind == 'connectors':
        store = ConnectorStore(root)
        write = lambda value: store.upsert(ConnectorRecord(name='fixture', command=f'command-{value}',
                                                          metadata={'description': '原文😀'}))
        read = lambda: [record.to_dict() for record in store.list()]
        reopen = lambda: [record.to_dict() for record in ConnectorStore(root).list()]
        name = 'connectors.json'
    elif kind == 'esc':
        store = EscStateStore(root)
        write = lambda value: store.set_enabled('skill', 'fixture', enabled=not value)
        read = store.snapshot
        reopen = lambda: EscStateStore(root).snapshot()
        name = 'esc-state.json'
    else:
        store = SettingsStore(root)
        write = lambda value: store.update({'default_model': f'模型😀-{value}'})
        read = store.snapshot
        reopen = lambda: SettingsStore(root).snapshot()
        name = 'ui-settings.json'
    return root / name, write, read, reopen


class JsonStatePublicationTests(unittest.TestCase):
    def test_windows_retry_keeps_utf8_lf_and_publishes_memory_after_disk(self):
        for kind in ('connectors', 'esc', 'settings'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                path, write, read, reopen = state_case(root, kind)
                write(0)
                original, snapshot = path.read_bytes(), read()
                attempts = []
                def replace(source, target):
                    attempts.append((source, source.read_bytes()))
                    self.assertEqual(path.read_bytes(), original)
                    self.assertEqual(read(), snapshot)
                    if len(attempts) == 1:
                        error = PermissionError('sharing violation')
                        error.winerror = 32
                        raise error
                    os.replace(source, target)
                with patch.object(fsatomic, 'os', wraps=os) as native, patch.object(fsatomic.time, 'sleep') as sleep:
                    native.name = 'nt'
                    native.replace.side_effect = replace
                    write(1)
                    self.assertEqual(native.fdopen.call_count, 1)
                    sleep.assert_called_once_with(.02)
                self.assertEqual(attempts[0], attempts[1])
                raw = path.read_bytes()
                self.assertNotIn(b'\r', raw)
                self.assertEqual(raw, json.dumps(json.loads(raw.decode('utf-8')), ensure_ascii=False, indent=2).encode('utf-8'))
                self.assertEqual(read(), reopen())
                self.assertNotEqual(read(), snapshot)
                self.assertEqual(list(root.iterdir()), [path])

    def test_failed_flush_or_publish_keeps_original_disk_and_memory(self):
        for kind in ('connectors', 'esc', 'settings'):
            for operation in ('replace', 'fsync'):
                with self.subTest(kind=kind, operation=operation), tempfile.TemporaryDirectory() as folder:
                    root = Path(folder)
                    path, write, read, reopen = state_case(root, kind)
                    write(0)
                    original, snapshot = path.read_bytes(), read()
                    external = root / '.tmp-external.tmp'
                    external.write_bytes(b'external')
                    error = OSError('publication failed')
                    with patch.object(fsatomic.os, operation, side_effect=error):
                        with self.assertRaises(OSError) as caught:
                            write(1)
                    self.assertIs(caught.exception, error)
                    self.assertEqual(path.read_bytes(), original)
                    self.assertEqual(read(), snapshot)
                    self.assertEqual(reopen(), snapshot)
                    self.assertEqual(external.read_bytes(), b'external')
                    self.assertEqual(set(root.iterdir()), {path, external})
