"""压缩协商按完整编码与质量值判断，不把小数权重误判为零。"""
import gzip
import unittest
import tempfile
from pathlib import Path
from harness.web.static_assets import StaticAssets
from harness.web.responses import accepts_gzip, json_response


class ResponseEncodingTests(unittest.TestCase):
    def test_quality_and_encoding_tokens(self):
        for header in ('gzip', 'GZIP;Q=0.5', 'br, gzip;q=0.001', '*;q=0.5', 'gzip;q=1.000'):
            with self.subTest(header=header):
                self.assertTrue(accepts_gzip(header))
        for header in ('', 'xgzip', 'gzip-extra', 'gzip;q=0', '*;q=1, gzip;q=0',
                       'gzip;q=nan', 'gzip;q=2', 'gzip;q=-1', 'gzip;q=0.0000', 'gzip;q=0.5;q=1'):
            with self.subTest(header=header):
                self.assertFalse(accepts_gzip(header))

    def test_positive_fraction_compresses_and_zero_preserves_original(self):
        payload = {'text': 'fixture ' * 1000}
        raw = json_response(payload)
        compressed = json_response(payload, accept_encoding='gzip;q=0.5')
        refused = json_response(payload, accept_encoding='*;q=1,gzip;q=0')
        self.assertEqual(compressed.headers['Content-Encoding'], 'gzip')
        self.assertEqual(gzip.decompress(compressed.body), raw.body)
        self.assertEqual(refused.body, raw.body)
        self.assertNotIn('Content-Encoding', refused.headers)

    def test_static_assets_share_the_same_quality_negotiation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = b'fixture static content ' * 1000
            (root / 'index.html').write_bytes(original)
            assets = StaticAssets(root)
            response = assets.serve('/', accept_encoding='gzip;q=0.5')
            self.assertEqual(response.headers['Content-Encoding'], 'gzip')
            self.assertEqual(gzip.decompress(response.body), original)
            response = assets.serve('/', accept_encoding='gzip;q=0')
            self.assertEqual(response.body, original)
            self.assertNotIn('Content-Encoding', response.headers)
