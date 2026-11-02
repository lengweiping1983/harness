"""语音构建目录保护、固定依赖和平台分发标签。"""
from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from setuptools import Distribution
import build_support

ROOT = Path(__file__).resolve().parents[1]


def load_builder():
    """加载带连字符的独立脚本，不执行构建。"""
    spec = importlib.util.spec_from_file_location('hex_build', ROOT / 'scripts' / 'build-hex.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class HexBuildTests(unittest.TestCase):
    def test_clean_refuses_existing_shared_directory(self):
        builder = load_builder()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'shared'
            target.mkdir()
            original = target / 'original'
            original.write_bytes(b'preserve')
            with patch.object(builder.sys, 'argv', ['build-hex', '--clean-cache', '--target-dir', str(target)]):
                self.assertEqual(builder.main(), 2)
            self.assertEqual(original.read_bytes(), b'preserve')

    def test_build_uses_locked_dependencies_and_vendor_configuration(self):
        builder = load_builder()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'target'
            (target / 'release').mkdir(parents=True)
            name = 'hex.exe' if builder.sys.platform == 'win32' else 'hex'
            (target / 'release' / name).write_bytes(b'fixture executable')
            with patch.object(builder.sys, 'argv', ['build-hex', '--target-dir', str(target)]), \
                 patch.object(builder, 'require'), patch.object(builder, 'OUTPUT_ROOT', Path(tmp)/'output'), \
                 patch.object(builder.subprocess, 'run') as run:
                run.return_value.returncode = 0
                self.assertEqual(builder.main(), 0)
            self.assertIn('--locked', run.call_args.args[0])
            self.assertEqual(run.call_args.kwargs['cwd'], builder.HEX_MANIFEST.parent)
            self.assertEqual((Path(tmp)/'output'/builder.platform_tag()/name).read_bytes(), b'fixture executable')
            self.assertFalse((target / '.harness-hex-cache').exists())

    def test_binary_wheel_uses_native_platform_tag(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(build_support, '__file__', str(Path(tmp)/'build_support.py')):
            binary = Path(tmp) / 'harness/resources/hex/macos-arm64/hex'
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b'fixture executable')
            command = build_support.BdistWheel(Distribution())
            command.ensure_finalized()
            self.assertFalse(command.root_is_pure)
            self.assertEqual(command.get_tag()[:2], ('py3', 'none'))
            self.assertNotEqual(command.get_tag()[2], 'any')
