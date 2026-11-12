"""执行工具缺依赖与模型目录档位提示遵循调用时语言，不访问外部服务。"""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from harness.core.errors import ToolExecutionError
from harness.core.language import current_language
from harness.i18n import tr
from harness.tools.builtin.code_tools import _which, build_code_tools
from harness.tools.builtin.subagent_model_tools import build_list_subagent_models_tool


class ExecutionCatalogLanguageTests(unittest.IsolatedAsyncioTestCase):
    def test_windows_bash_skips_wsl_launcher_and_finds_native_interpreter(self):
        """WSL启动器与本机脚本解释器不能只按同名可执行文件混为一谈。"""
        import os
        from pathlib import Path
        launchers = (r'C:\Windows\System32\bash.exe', r'C:\Windows\Sysnative\bash.exe',
                     r'C:\fixture\Microsoft\WindowsApps\bash.exe')
        for launcher in launchers:
            for native in ('/fixture/native/bash.exe', None):
                def which(name, *, path=None):
                    if name == 'bash':
                        return launcher if path is None else native
                    return None
                with self.subTest(launcher=launcher, native=native), \
                     patch.dict(os.environ, {'SystemRoot': r'C:\Windows', 'LOCALAPPDATA': r'C:\fixture'}), \
                     patch('harness.tools.builtin.code_tools.sys.platform', 'win32'), \
                     patch('harness.tools.builtin.code_tools.shutil.which', side_effect=which):
                    self.assertEqual(_which('bash'), native)
        launcher = launchers[0]
        with patch.dict(os.environ, {'SystemRoot': r'C:\Windows'}), \
             patch('harness.tools.builtin.code_tools.sys.platform', 'win32'), \
             patch('harness.tools.builtin.code_tools.shutil.which', side_effect=[launcher, None, '/fixture/Git/cmd/git.exe']), \
             patch.object(Path, 'is_file', return_value=True):
            self.assertEqual(_which('bash'), str(Path('/fixture/Git/bin/bash.exe')))
        with patch('harness.tools.builtin.code_tools.sys.platform', 'win32'), \
             patch('harness.tools.builtin.code_tools.shutil.which', side_effect=[None, None, '/fixture/Git/cmd/git.exe']), \
             patch.object(Path, 'is_file', return_value=True):
            self.assertEqual(_which('bash'), str(Path('/fixture/Git/bin/bash.exe')))

    async def test_model_efforts_and_missing_interpreter_follow_language(self):
        code = build_code_tools(None)[0]
        models = build_list_subagent_models_tool(base_url='https://fixture.invalid', model='fixture/model')
        ctx = SimpleNamespace(services={}, session_id='fixture', cwd=None)
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                with patch('harness.tools.builtin.code_tools._which', return_value=None):
                    with self.assertRaises(ToolExecutionError) as caught:
                        await code.execute({'language': 'node', 'code': 'unused'}, ctx)
                self.assertEqual(any('\u4e00' <= c <= '\u9fff' for c in caught.exception.message), language == 'zh-CN')
                with patch('harness.tools.builtin.subagent_model_tools._fetch_catalog', return_value=[
                        {'id': 'fixture/model', 'supported_parameters': ['reasoning']} ]):
                    result = await models.execute({'provider': 'openrouter', 'model': 'fixture/model'}, ctx)
                for label in [row['label'] for row in result.data['reasoning_efforts']]:
                    self.assertEqual(any('\u4e00' <= c <= '\u9fff' for c in label), language == 'zh-CN')
                self.assertIn('推理强度档位：' if language == 'zh-CN' else 'Reasoning efforts:', result.content[0].text)

    def test_model_schema_description_uses_construction_language(self):
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language), self.subTest(language=language):
                tool = build_list_subagent_models_tool(base_url='https://fixture.invalid', model='fixture/model')
                self.assertEqual(tool.schema.description, tr('python.subagent_model_tools.af2589c32fe3'))

    async def test_unsupported_language_uses_current_list_separator_before_any_execution(self):
        tool = build_code_tools(None)[0]
        context = SimpleNamespace(services={}, session_id='fixture', cwd=None)
        for language in ('en', 'zh-CN', 'en'):
            with self.subTest(language=language), current_language.set(language), \
                 patch('harness.tools.builtin.code_tools._which') as which, \
                 patch('harness.tools.builtin.code_tools.tempfile.mkstemp') as temporary:
                with self.assertRaises(ToolExecutionError) as caught:
                    await tool.execute({'language': 'unknown', 'code': 'Original 原文'}, context)
                names = ['bash', 'node', 'nodejs', 'powershell', 'python', 'python3', 'sh']
                expected = (', ' if language == 'en' else '、').join(names)
                self.assertEqual(caught.exception.message,
                    tr('python.code_tools.0379b1ec016b', p0=repr('unknown'), p1=expected))
                self.assertEqual(caught.exception.details['language'], 'unknown')
                which.assert_not_called()
                temporary.assert_not_called()

    async def test_current_language_aliases_keep_literal_argv_and_remove_own_script(self):
        from pathlib import Path
        from unittest.mock import AsyncMock
        from harness.tools.registry import ToolRunContext
        tool = build_code_tools(None)[0]
        context = ToolRunContext(call_id='fixture', session_id='fixture', cwd=None, turn=1, step=1)
        original = 'Original script 原文 {file} $(not-a-shell-command)'
        captured = []
        async def create(*argv, **kwargs):
            script = Path(argv[-1])
            self.assertEqual(script.read_text(encoding='utf-8'), original)
            captured.append((argv, script))
            return object()
        for platform in ('darwin', 'win32'):
            for alias in ('node', 'nodejs', 'bash', 'sh'):
                with self.subTest(platform=platform, alias=alias), \
                     patch('harness.tools.builtin.code_tools.sys.platform', platform), \
                     patch('harness.tools.builtin.code_tools._which', return_value='/Original interpreter/with spaces'), \
                     patch('harness.tools.builtin.code_tools.asyncio.create_subprocess_exec', side_effect=create), \
                     patch('harness.tools.builtin.code_tools.collect_process', AsyncMock(return_value=(b'Original output', b'', 0, False))):
                    result = await tool.execute({'language': alias, 'code': original}, context)
                argv, script = captured[-1]
                self.assertEqual(argv, ('/Original interpreter/with spaces', str(script)))
                self.assertFalse(script.exists())
                self.assertEqual(result['language'], alias)
                self.assertEqual(result['exit_code'], 0)
                self.assertFalse(result['error'])

    async def test_every_missing_interpreter_hint_precedes_script_creation(self):
        tool = build_code_tools(None)[0]
        context = SimpleNamespace(services={}, session_id='fixture', cwd=None)
        cases = [('darwin', 'node', 'node'), ('darwin', 'bash', 'bash'),
                 ('darwin', 'powershell', 'pwsh'), ('win32', 'powershell', 'powershell')]
        from harness.tools.builtin.code_tools import _MISSING_HINT_KEYS
        for language in ('en', 'zh-CN', 'en'):
            for platform, name, token in cases:
                with self.subTest(language=language, platform=platform, token=token), \
                     current_language.set(language), \
                     patch('harness.tools.builtin.code_tools.sys.platform', platform), \
                     patch('harness.tools.builtin.code_tools._which', return_value=None), \
                     patch('harness.tools.builtin.code_tools.tempfile.mkstemp') as temporary:
                    with self.assertRaises(ToolExecutionError) as caught:
                        await tool.execute({'language': name, 'code': 'Original 原文 {file}'}, context)
                    self.assertEqual(caught.exception.message, tr(_MISSING_HINT_KEYS[token]))
                    self.assertEqual(caught.exception.details['interpreter'], token)
                    temporary.assert_not_called()
