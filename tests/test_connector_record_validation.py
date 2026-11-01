"""连接器损坏声明不能被强制转换或隐式改写。"""
import json
import tempfile
import unittest
from pathlib import Path

from harness.connectors.store import ConnectorRecord, ConnectorStore
from harness.core.errors import ConnectorError
from harness.core.language import current_language


class ConnectorRecordValidationTests(unittest.TestCase):
    def test_invalid_types_are_rejected_and_file_preserved(self):
        invalid = {'enabled': 'false', 'bound': 1, 'args': 'npx', 'env': {'TOKEN': 123},
                   'headers': [], 'metadata': [], 'command': 1, 'transport': None}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'connectors.json'
            for language in ('en', 'zh-CN'):
                with current_language.set(language):
                    for key, value in invalid.items():
                        with self.subTest(language=language, field=key):
                            raw = json.dumps({'version': 1, 'connectors': {'fixture': {key: value}}})
                            path.write_text(raw, encoding='utf-8')
                            with self.assertRaises(ConnectorError):
                                ConnectorStore(directory).list()
                            self.assertEqual(path.read_text(encoding='utf-8'), raw)

    def test_current_record_round_trips_without_coercion(self):
        record = ConnectorRecord('fixture', args=['--value'], env={'TOKEN': '${TOKEN}'}, enabled=False)
        self.assertEqual(ConnectorRecord.from_dict(record.to_dict()), record)
        self.assertFalse(ConnectorRecord.from_dict({'name': 'fixture', 'enabled': False}).enabled)
