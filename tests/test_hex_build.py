"""二进制 wheel 的平台分发标签。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from setuptools import Distribution
import build_support


class BinaryWheelTests(unittest.TestCase):
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
