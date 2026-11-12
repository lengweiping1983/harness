"""独立grep工作进程的行号与read和edit的源码行一致。"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from harness.tools.builtin import fs_grep_worker


class GrepSourceLineTests(unittest.TestCase):
    def test_isolated_worker_preserves_unicode_separators_and_context(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / 'fixture.txt').write_text('first\r\nvalue=a\u2028b\u2029c\vother\fend\r\nneedle\r\nlast\r\n', encoding='utf-8', newline='')
            request = root / 'request.json'
            request.write_text(json.dumps({'root': str(root), 'args': {'path': 'fixture.txt',
                'pattern': 'needle', 'context': 1}, 'ignores': []}))
            response = subprocess.check_output([sys.executable, '-I', fs_grep_worker.__file__, str(request)])
            result = json.loads(response)['result']
            self.assertEqual(result['matches'], [{'file': 'fixture.txt', 'line': 3, 'text': 'needle'}])
            self.assertEqual([row['line'] for row in result['context_lines']], [2, 4])
            self.assertEqual(result['context_lines'][0]['text'], 'value=a\u2028b\u2029c\vother\fend')
