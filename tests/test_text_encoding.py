"""UTF-8 默认、显式例外、严格输出与生命周期边界回归。"""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from harness.core.text_encoding import TextDecoder, decode_output, output_encoding, utf8_env, utf8_prefix
from harness.tools.builtin.code_tools import build_code_tools
from harness.tools.builtin.job_tools import JobRegistry
from harness.tools.builtin.pwsh_tools import _decode_pwsh_output
from harness.tools.builtin.web_tools import _decode_bytes
from harness.tools.builtin.terminal_tools import _StreamDecoder, TerminalSessionService
from harness.tools.process_output import OutputPreview
from harness.tools.registry import ToolRunContext
from harness.web.wsproto import Frame, OP_TEXT, ProtocolError, decode_close_payload
from scripts.check_text_encoding import text_io_issues


class EncodingContractTests(unittest.TestCase):
    def test_utf8_and_explicit_codecs_have_identical_chunked_and_final_text(self):
        text = '中文原文😀\r\n末行'
        for encoding in ('utf-8', 'utf-16', 'utf-16-le', 'gb18030'):
            raw = text.encode(encoding)
            for cut in range(len(raw) + 1):
                decoder = TextDecoder(encoding)
                actual = decoder.decode(raw[:cut]) + decoder.decode(raw[cut:], final=True)
                self.assertEqual(actual, text)
                self.assertIsNone(decoder.error)
            self.assertEqual(decode_output(raw, b'', encoding)['stdout'], text)

    def test_invalid_and_incomplete_utf8_preserve_prefix_without_replacement(self):
        for raw in (b'prefix\xffrest', b'prefix\xe4\xb8'):
            expected = decode_output(raw, b'')
            self.assertEqual(expected['stdout'], 'prefix')
            for cut in range(len(raw) + 1):
                decoder = TextDecoder()
                actual = decoder.decode(raw[:cut]) + decoder.decode(raw[cut:], final=True)
                self.assertEqual(actual, 'prefix')
                self.assertEqual(decoder.error['offset'], 6)
                self.assertEqual(decoder.decode(b'more'), '')
            self.assertEqual(expected['encoding_errors'][0]['offset'], 6)
        self.assertIn('encoding_errors', decode_output('原文'.encode('gbk'), b''))

    def test_invalid_codec_is_rejected_and_child_defaults_do_not_mutate_host(self):
        for value in (None, True, 8, '', 'missing-codec', 'base64_codec'):
            with self.assertRaises(ValueError):
                output_encoding(value)
        original = {'KEEP': 'original', 'PYTHONUTF8': '0'}
        self.assertEqual(utf8_env(original)['PYTHONUTF8'], '1')
        self.assertEqual(original['PYTHONUTF8'], '0')

    def test_sdk_import_does_not_change_host_and_bootstrap_restarts_with_utf8(self):
        root = Path(__file__).resolve().parent.parent
        source = '''import json,sys,os
before=(sys.stdout.encoding,sys.stdin.encoding,dict(os.environ))
import harness
assert before==(sys.stdout.encoding,sys.stdin.encoding,dict(os.environ))
print(json.dumps({'utf8':sys.flags.utf8_mode}))
'''
        environment = {**os.environ, 'PYTHONUTF8': '0', 'PYTHONIOENCODING': 'utf-8'}
        result = subprocess.run([sys.executable, '-X', 'utf8=0', '-c', source], cwd=root,
                                env=environment, capture_output=True, encoding='utf-8', check=True)
        self.assertEqual(json.loads(result.stdout)['utf8'], 0)
        bootstrap = "import sys;sys.path.insert(0,'scripts');from python_bootstrap import configure_utf8_process;configure_utf8_process();print(sys.flags.utf8_mode)"
        result = subprocess.run([sys.executable, '-X', 'utf8=0', '-c', bootstrap], cwd=root,
                                env=environment, capture_output=True, encoding='utf-8', check=True)
        self.assertEqual(result.stdout.strip(), '1')

    def test_bootstrap_preserves_application_arguments_and_unrelated_xoptions(self):
        root = Path(__file__).resolve().parent.parent
        source = "import json,sys;sys.path.insert(0,'scripts');from python_bootstrap import configure_utf8_process;configure_utf8_process();print(json.dumps({'argv':sys.argv[1:],'utf8':sys.flags.utf8_mode,'custom':sys._xoptions.get('utf8custom')}))"
        arguments = ['-X', 'utf8=0', '-Xutf8=0', '原始参数']
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / 'bootstrap_fixture.py'
            script.write_text(source, encoding='utf-8')
            for option in (['-X', 'utf8=0'], ['-Xutf8=0']):
                for entry in (['-c', source], [str(script)], ['--', str(script)], ['-m', 'bootstrap_fixture']):
                    with self.subTest(option=option, entry=entry[0]):
                        result = subprocess.run([sys.executable, *option, '-W', 'ignore', '-Xutf8custom=original', *entry, *arguments],
                            cwd=root, env={**os.environ, 'PYTHONUTF8':'0', 'PYTHONIOENCODING':'utf-8', 'PYTHONPATH':directory},
                            capture_output=True, encoding='utf-8', check=True)
                        self.assertEqual(json.loads(result.stdout), {'argv':arguments,'utf8':1,'custom':'original'})

    def test_declared_web_charset_and_clixml_keep_protocol_boundaries(self):
        raw = '原文'.encode('gbk')
        self.assertEqual(_decode_bytes(raw, 'text/html; charset=gbk')[0], '原文')
        partial = '中😀'.encode('utf-8')[:4]
        self.assertEqual(_decode_bytes(partial, 'text/html', truncated=True)[0], '中')
        self.assertEqual(utf8_prefix(partial, truncated=True), '中')
        with self.assertRaises(UnicodeDecodeError):
            utf8_prefix(b'prefix\xff', truncated=True)
        with self.assertRaises(UnicodeDecodeError):
            _decode_bytes(raw, 'text/html')
        value = _decode_pwsh_output(b'', b'#< CLIXML\n\xff', 'gbk')
        self.assertEqual(value['encoding_errors'][0]['encoding'], 'utf-8')
        self.assertEqual(value['encoding_errors'][0]['channel'], 'stderr')

    def test_protocol_utf8_rejects_invalid_bytes(self):
        with self.assertRaises(ProtocolError) as caught:
            Frame(OP_TEXT, b'\xff').text()
        self.assertEqual(caught.exception.code, 1007)
        with self.assertRaises(ProtocolError):
            decode_close_payload(b'\x03\xe8\xff')

    def test_source_gate_distinguishes_binary_and_explicit_text(self):
        self.assertTrue(text_io_issues("p.read_text()", 'harness/example.py'))
        self.assertFalse(text_io_issues("p.read_text(encoding='utf-8');open('x','rb')", 'harness/example.py'))
        self.assertTrue(text_io_issues("subprocess.run(['x'],text=True)", 'scripts/example.py'))
        self.assertTrue(text_io_issues("raw.decode('utf-8', errors='replace')", 'harness/example.py'))


class OutputLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_code_exit_and_bytes_are_not_replayed_after_decode_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / 'marker.txt'
            code = "import pathlib,sys;p=pathlib.Path('marker.txt');p.write_bytes(b'once');sys.stdout.buffer.write(b'prefix\\xffrest')"
            ctx = ToolRunContext('encoding-call', 'encoding-owner', directory, 1, 1)
            tool = build_code_tools(directory)[0]
            value = await tool.execute({'language': 'python', 'code': code}, ctx)
            self.assertEqual(value['exit_code'], 0)
            self.assertTrue(value['error'])
            self.assertEqual(value['stdout'], 'prefix')
            self.assertEqual(marker.read_bytes(), b'once')
            self.assertEqual(value['encoding_errors'][0]['offset'], 6)
            explicit = await tool.execute({'language': 'python', 'code': "import sys;sys.stdout.buffer.write('原文'.encode('gbk'))", 'output_encoding': 'gbk'}, ctx)
            self.assertFalse(explicit['error'])
            self.assertEqual(explicit['stdout'], '原文')
            with patch('harness.tools.builtin.code_tools.tempfile.mkstemp') as create:
                with self.assertRaises(ValueError):
                    await tool.execute({'code': 'pass', 'output_encoding': 'missing-codec'}, ctx)
                create.assert_not_called()

    @unittest.skipUnless(os.name == 'nt', '需要 Windows cmd 管道')
    async def test_windows_terminal_pipe_starts_with_utf8_before_cmd_caches_codepage(self):
        with tempfile.TemporaryDirectory(prefix='harness-encoding-terminal-') as directory:
            Path(directory, '原文文件.txt').write_bytes(b'fixture')
            service = TerminalSessionService(backends={'cmd': [os.environ['COMSPEC'], '/d']})
            service.handoff_grace = 0.05
            try:
                with patch('harness.tools.windows_conpty.conpty_api', side_effect=ImportError('fixture')):
                    view = await service.spawn(owner='owner', kind='cmd', cwd=directory)
                session = service.expect_owned('owner', view['sessionId'])
                await session.write_input('dir /b /a\r\n')
                async with asyncio.timeout(5):
                    while '原文文件.txt' not in session.peek()['text']:
                        self.assertIsNone(session.decoder.error)
                        session._output_changed.clear()
                        await session._output_changed.wait()
                self.assertIsNone(session.decoder.error)
            finally:
                await service.close_all()

    async def test_terminal_invalid_codec_is_rejected_before_creation(self):
        service = TerminalSessionService()
        with patch.object(service, '_spawn', new_callable=AsyncMock) as spawn:
            with self.assertRaises(ValueError):
                await service.spawn(owner='owner', kind='fixture', output_encoding='missing-codec')
            spawn.assert_not_called()

    async def test_preview_job_and_terminal_share_the_same_encoding_rules(self):
        raw = '原文😀'.encode('utf-8')
        preview = OutputPreview(None)
        terminal = _StreamDecoder()
        for value in (raw[:1], raw[1:5], raw[5:]):
            preview.feed('stdout', value)
        preview.feed('stdout', b'', final=True)
        self.assertEqual(preview.text['stdout'], terminal.feed(raw) + terminal.flush())
        self.assertEqual(preview.text['stdout'], '原文😀')
        class Process:
            returncode = 0
            def __init__(self):
                self.stdout, self.stderr = asyncio.StreamReader(), asyncio.StreamReader()
                self.stdout.feed_data(b'prefix\xffrest');self.stdout.feed_eof();self.stderr.feed_eof()
            async def wait(self):
                return 0
        registry = JobRegistry()
        async def spawn():
            return Process()
        job = await registry.spawn(kind='fixture', label='fixture', owner='owner', spawn=spawn)
        await registry.wait(job.id, 'owner', 2000)
        self.assertEqual(job.exit_code, 0)
        self.assertEqual(job.status, 'completed')
        self.assertEqual(registry.peek(job.id, 'owner')['stdout'], 'prefix')
        self.assertEqual(job.public()['encoding_errors'][0]['offset'], 6)
        await registry.cancel_all()
