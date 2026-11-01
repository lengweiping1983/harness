"""研究回归收集须核对原始文件字节，不把正常换行解码当成来源被修改。"""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.evaluate_research_team import main


class ResearchSourceBytesTests(unittest.TestCase):
    def collect(self, original, current):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case, workspace = root/'case', root/'workspace'
            case.mkdir()
            workspace.mkdir()
            (case/'task.json').write_text('{}', encoding='utf-8')
            (case/'expected.json').write_text(json.dumps({'C1': {'verdict': 'supported', 'source': 'facts.md'}}), encoding='utf-8')
            if current is not None:
                (workspace/'facts.md').write_bytes(current)
            manifest = {'base_url': 'http://127.0.0.1:5273', 'run_id': 'run-fixed',
                'session_id': 'session-fixed', 'workspace': str(workspace),
                'case_hashes': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in case.iterdir()},
                'source_hashes': {'facts.md': hashlib.sha256(original).hexdigest()}}
            path = root/'evaluation.json'
            path.write_text(json.dumps(manifest), encoding='utf-8')
            report = {'claims': [{'id': 'C1', 'verdict': 'supported',
                'sources': [{'path': 'facts.md', 'quote': '固定事实'}]}]}
            run = {'id': 'run-fixed', 'status': 'completed',
                   'result': {'text': '```json\n'+json.dumps(report, ensure_ascii=False)+'\n```'}}
            with patch('sys.argv', ['evaluate', '--collect', str(path), '--case', str(case)]), \
                 patch('scripts.evaluate_research_team.request', side_effect=[run, {'events': []}, {'agents': []}]) as request, \
                 contextlib.redirect_stdout(io.StringIO()):
                code = main()
            self.assertEqual(request.call_count, 3)
            self.assertTrue(all(len(call.args) == 2 for call in request.call_args_list))
            return code, json.loads((workspace/'result.json').read_text(encoding='utf-8'))['score']

    def test_unchanged_lf_crlf_and_mixed_newlines_pass(self):
        for text in ('# 来源\n固定事实\n', '# 来源\r\n固定事实\r\n', '# 来源\r\n固定事实\n'):
            with self.subTest(text=text):
                source = text.encode('utf-8')
                code, score = self.collect(source, source)
                self.assertEqual(code, 0, score)

    def test_actual_newline_change_content_change_and_deletion_are_rejected(self):
        original = '# 来源\r\n固定事实\r\n'.encode('utf-8')
        for current in (original.replace(b'\r\n', b'\n'), original+b'changed', None):
            with self.subTest(current=current):
                code, score = self.collect(original, current)
                self.assertEqual(code, 1)
                self.assertIn('source_changed:facts.md', score['errors'])
