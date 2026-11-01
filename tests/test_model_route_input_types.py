"""路由配置拒绝被强制转换的类型和布尔版本，保留原件。"""
import json
import tempfile
import unittest
from pathlib import Path
from harness.core.language import current_language
from harness.llm.routes import ModelRoutes, load_models


def model():
    return {'id': 'fixture', 'model': 'fixture-model', 'base_url': 'https://fixture.invalid/v1',
            'enabled': True, 'keys': [{'id': 'key', 'value': 'fixture-secret'}]}


class ModelRouteInputTypeTests(unittest.TestCase):
    def test_non_string_model_and_endpoint_cannot_be_normalized(self):
        for field in ('model', 'base_url'):
            for value in (None, True, 42, [], {}):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    ModelRoutes([{**model(), field: value}])
        routes = ModelRoutes([{**model(), 'model': ' fixture-model ', 'base_url': ' https://fixture.invalid/v1/ '}])
        self.assertEqual(routes.models[0]['model'], 'fixture-model')
        self.assertEqual(routes.models[0]['base_url'], 'https://fixture.invalid/v1')

    def test_invalid_persisted_versions_and_models_preserve_original_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            path = home / 'connection-secrets.json'
            cases = [{'version': version, 'models': [model()]} for version in (True, 1.0, '1')]
            cases.append({'version': 1, 'models': [{**model(), 'model': None}]})
            for language in ('en', 'zh-CN'):
                for payload in cases:
                    raw = json.dumps(payload).encode()
                    path.write_bytes(raw)
                    with self.subTest(language=language, payload=payload), current_language.set(language):
                        with self.assertRaises(ValueError) as error:
                            load_models(home, 'fallback', 'https://fallback.invalid', ['fallback-secret'])
                        self.assertNotIn('fixture-secret', str(error.exception))
                        self.assertEqual(any('\u4e00' <= c <= '\u9fff' for c in str(error.exception)), language == 'zh-CN')
                        self.assertEqual(path.read_bytes(), raw)

    def test_missing_persisted_identity_is_not_regenerated(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            path = home / 'connection-secrets.json'
            for target in ('model', 'key'):
                for invalid in (None, '', '   ', False, 0, 'missing'):
                    entry = model()
                    record = entry if target == 'model' else entry['keys'][0]
                    if invalid == 'missing':
                        del record['id']
                    else:
                        record['id'] = invalid
                    raw = json.dumps({'version': 1, 'models': [entry]}).encode()
                    path.write_bytes(raw)
                    with self.subTest(target=target, identity=invalid):
                        for _ in range(2):
                            with self.assertRaises(ValueError):
                                load_models(home, 'fallback', 'https://fallback.invalid', ['fallback'])
                            self.assertEqual(path.read_bytes(), raw)

    def test_new_entries_generate_ids_but_persisted_ids_stay_stable(self):
        entry = model()
        del entry['id']
        del entry['keys'][0]['id']
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            routes = ModelRoutes([entry], home)
            routes.update(routes.models)
            first = load_models(home, '', '', [])
            self.assertEqual(first, routes.models)
            self.assertEqual(load_models(home, '', '', []), first)
