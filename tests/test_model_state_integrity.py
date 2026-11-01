"""轮换状态只接受当前完整格式，错误不泄露正文、不覆盖原件。"""
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from harness.core.language import current_language
from harness.i18n import tr
from harness.llm.key_pool import ApiKeyPool
from harness.llm.routes import ModelRoutes


def models():
    return [{'id': 'fixture', 'model': 'fixture', 'base_url': 'https://example.test',
             'keys': [{'id': 'first', 'value': 'fixture-first'}, {'id': 'second', 'value': 'fixture-second'}]}]


class ModelStateIntegrityTests(unittest.TestCase):
    def test_corrupt_and_old_formats_fail_in_both_languages_without_writing(self):
        for language in ('en', 'zh-CN'):
            for kind in ('pool', 'routes'):
                with self.subTest(language=language, kind=kind), tempfile.TemporaryDirectory() as folder:
                    home = Path(folder)
                    path = home / ('api-key-pool.json' if kind == 'pool' else 'model-route-state.json')
                    factory = (lambda: ApiKeyPool(['first', 'second'], state_path=path)) if kind == 'pool' else (lambda: ModelRoutes(models(), home))
                    value = factory()
                    value.mark_failed(0) if kind == 'pool' else value.reset()
                    existing = {item.name: item.read_bytes() for item in home.iterdir() if item != path}
                    valid = json.loads(path.read_text())
                    variants = [b'\xff', b'{"sensitive-fixture":', b'[]', b'{}']
                    for key, replacement in [('version', True), ('version', 0), ('failed', [True]), ('failed', [{}]),
                                             ('failed', ['sensitive-fixture']), ('failed', 'invalid')]:
                        variants.append(json.dumps({**valid, key: replacement}).encode())
                    variants.append(json.dumps({**valid, 'extra': 'sensitive-fixture'}).encode())
                    variants.append(json.dumps({key: value for key, value in valid.items() if key != 'current'}).encode())
                    changes = [('date', '20260101'), ('date', 'invalid'), ('current', True), ('current', 2), ('failed', [0, 0]), ('failed', [2])] if kind == 'pool' else [('cycle', True), ('cycle', 0), ('cycle', 1.5), ('current', {}), ('current', 'invalid'), ('failed', ['a' * 64] * 2)]
                    variants.extend(json.dumps({**valid, key: replacement}).encode() for key, replacement in changes)
                    for raw in variants:
                        path.write_bytes(raw)
                        with current_language.set(language), self.assertRaises(ValueError) as failure:
                            factory()
                        with current_language.set(language):
                            self.assertEqual(str(failure.exception), tr('llm.state.key_pool_invalid' if kind == 'pool' else 'llm.state.routes_invalid'))
                        self.assertNotIn('sensitive-fixture', str(failure.exception))
                        self.assertEqual(path.read_bytes(), raw)
                        self.assertEqual({item.name for item in home.iterdir()}, {path.name, *existing})
                        self.assertEqual({item.name: item.read_bytes() for item in home.iterdir() if item != path}, existing)
                    with patch.object(Path, 'read_text', side_effect=PermissionError('private diagnostic')):
                        with self.assertRaises(ValueError) as failure:
                            factory()
                        self.assertNotIn('private diagnostic', str(failure.exception))
                    self.assertEqual(path.read_bytes(), variants[-1])

    def test_pool_current_state_day_rollover_and_changed_credentials(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'pool.json'
            pool = ApiKeyPool(['first', 'second'], state_path=path)
            pool.mark_failed(0)
            self.assertEqual(ApiKeyPool(['first', 'second'], state_path=path).current(), (1, 'second'))
            saved = json.loads(path.read_text())
            yesterday = {**saved, 'date': (date.today() - timedelta(days=1)).isoformat()}
            path.write_text(json.dumps(yesterday))
            original = path.read_bytes()
            self.assertEqual(ApiKeyPool(['first', 'second'], state_path=path).current(), (0, 'first'))
            self.assertEqual(path.read_bytes(), original)
            path.write_text(json.dumps(saved))
            original = path.read_bytes()
            changed = ApiKeyPool(['replacement'], state_path=path)
            self.assertEqual(changed.current(), (0, 'replacement'))
            self.assertEqual(path.read_bytes(), original)
            changed.mark_failed(0)
            self.assertNotEqual(path.read_bytes(), original)

    def test_routes_restore_and_removed_credentials_are_not_reintroduced(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            pool = ModelRoutes(models(), home)
            selected = pool.select()
            pool.fail(selected[2], selected[3])
            path = home / 'model-route-state.json'
            original = path.read_bytes()
            restored = ModelRoutes(models(), home)
            self.assertEqual(restored.failed, {selected[2]})
            self.assertEqual(restored.select()[1]['id'], 'second')
            self.assertEqual(path.read_bytes(), original)
            replacement = models()
            replacement[0]['keys'] = [{'id': 'new', 'value': 'new-fixture'}]
            changed = ModelRoutes(replacement, home)
            self.assertEqual(changed.failed, set())
            self.assertEqual(changed.select()[1]['id'], 'new')
            self.assertEqual(path.read_bytes(), original)
