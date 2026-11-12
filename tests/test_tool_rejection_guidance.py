"""验证超时单位诊断与文件拒绝的明确恢复入口，保持原文件和输入。"""
import tempfile
import unittest
from pathlib import Path

from harness.core.errors import ToolArgsError
from harness.core.language import current_language
from harness.tools.builtin.shell_tools import build_shell_tools
from harness.tools.builtin.fs_observation import write_exists_error
from harness.tools.schema import validate_or_raise


class ToolRejectionGuidanceTests(unittest.TestCase):
    def test_timeout_rejection_includes_actual_seconds_contract_without_conversion(self):
        for language in ('en', 'zh-CN', 'en'):
            with self.subTest(language=language), current_language.set(language):
                schema = build_shell_tools()[0].schema.resolve_description(language)
                args = {'command': 'original', 'timeout': 120000}
                with self.assertRaises(ToolArgsError) as caught:
                    validate_or_raise(args, dict(schema.parameters), tool='bash')
                self.assertIn('120000', caught.exception.message)
                self.assertIn('120', caught.exception.message)
                self.assertIn('900', caught.exception.message)
                self.assertIn(schema.parameters['properties']['timeout']['description'], caught.exception.message)
                self.assertTrue(caught.exception.details['not_executed'])
                self.assertEqual(args['timeout'], 120000)
                self.assertEqual(validate_or_raise({'command':'original','timeout':120}, dict(schema.parameters))['timeout'], 120)

    def test_existing_file_recovery_is_read_only_and_preserves_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'main.ts'
            original = '中文 😀\r\noriginal'.encode('utf-8')
            path.write_bytes(original)
            error = write_exists_error(path)
            self.assertEqual(error.code, 'FS_EXISTS')
            self.assertFalse(error.details['changed'])
            self.assertEqual(error.details['recovery'], {'tool':'read','arguments':{'path':str(path)}})
            self.assertEqual(path.read_bytes(), original)
