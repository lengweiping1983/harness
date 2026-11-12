"""PowerShell错误适配只剔除确定的内部回显，保留用户和第三方原文。"""
import unittest
from unittest.mock import AsyncMock, patch
from xml.sax.saxutils import escape
from harness.tools.builtin import pwsh_tools
from harness.tools.registry import ToolRunContext


def clixml(*lines):
    return ('#< CLIXML\n<Objs>' + ''.join('<S S="Error">' + escape(line) + '</S>' for line in lines) + '</Objs>').encode('utf-8')


class PwshClixmlIdentityTests(unittest.IsolatedAsyncioTestCase):
    def test_embedded_internal_text_and_original_colons_are_preserved(self):
        for original in (': Original 原文', 'Original prefix ' + pwsh_tools._PREAMBLE + ' original suffix',
                         'Original prefix ' + pwsh_tools._EPILOGUE + ' original suffix', '+ CategoryInfo : Original 原文'):
            with self.subTest(original=original):
                self.assertEqual(pwsh_tools._strip_clixml(clixml(original)), (original + '\n').encode())

    def test_exact_internal_echoes_are_removed_and_generated_prefix_is_trimmed(self):
        for fragment in (pwsh_tools._PREAMBLE.strip(), pwsh_tools._EPILOGUE.strip()):
            for prefix in ('', '+ ', '- '):
                self.assertEqual(pwsh_tools._strip_clixml(clixml(prefix + fragment, 'Original failure 原文')), 'Original failure 原文\n'.encode())
                self.assertEqual(pwsh_tools._strip_clixml(clixml(prefix + fragment + ' : Original failure 原文')), 'Original failure 原文\n'.encode())

    def test_unknown_xml_and_nonxml_stderr_remain_original(self):
        for original in (b'Original stderr', b'#< CLIXML\n<Unknown>Original</Unknown>'):
            self.assertEqual(pwsh_tools._strip_clixml(original), original)
        value = f'At line:{pwsh_tools._PREAMBLE_LINES + 3} char:7'
        self.assertEqual(pwsh_tools._strip_clixml(clixml(value)), b'At line:3 char:7\n')

    async def test_actual_tool_exit_keeps_user_error_containing_internal_text(self):
        original = 'Original diagnostic ' + pwsh_tools._PREAMBLE + ' 原文'
        with patch.object(pwsh_tools, '_resolve_powershell', return_value=('fixture-pwsh', True)), \
             patch.object(pwsh_tools, '_launch', AsyncMock(return_value=object())), \
             patch.object(pwsh_tools, '_collect', AsyncMock(return_value=(b'Original output', clixml(original), 1, False))):
            result = await pwsh_tools.build_pwsh_tools()[0].execute({'command': "Write-Error 'Original'"}, ToolRunContext('fixture', 'owner', '.', 1, 1))
        self.assertEqual(result['stderr'], original + '\n')
        self.assertEqual(result['stdout'], 'Original output')
        self.assertEqual(result['exit_code'], 1)
