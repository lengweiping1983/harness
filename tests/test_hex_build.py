"""核对 Hex 唯一资源目录的平台分发边界。"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from setuptools import Distribution
import build_support


class BinaryWheelTests(unittest.TestCase):
    def test_binary_wheel_uses_native_platform_tag(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(build_support, '__file__', str(Path(tmp)/'build_support.py')):
            binary = Path(tmp) / 'harness/resources/hex' / build_support.hex_platform_tag() / ('hex.exe' if sys.platform == 'win32' else 'hex')
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b'fixture executable')
            command = build_support.BdistWheel(Distribution())
            command.ensure_finalized()
            self.assertFalse(command.root_is_pure)
            self.assertEqual(command.get_tag()[:2], ('py3', 'none'))
            self.assertNotEqual(command.get_tag()[2], 'any')

    def test_foreign_engines_and_readme_do_not_make_wheel_native(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(build_support, '__file__', str(Path(tmp)/'build_support.py')):
            root = Path(tmp) / 'harness/resources/hex'
            foreign = root / 'foreign-platform/hex'
            foreign.parent.mkdir(parents=True)
            foreign.write_bytes(b'foreign engine')
            (root / 'README.md').write_text('说明', encoding='utf-8')
            command = build_support.BdistWheel(Distribution())
            command.ensure_finalized()
            self.assertTrue(command.root_is_pure)
            self.assertEqual(command.get_tag()[2], 'any')

    def test_build_data_selects_each_platform_and_keeps_licenses(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(build_support, '__file__', str(Path(tmp)/'build_support.py')):
            root = Path(tmp) / 'harness/resources/hex'
            files = []
            for tag in ('macos-arm64', 'windows-x86_64'):
                for name in ('hex.exe' if tag.startswith('windows') else 'hex', 'engine.json', 'LICENSE', 'THIRD_PARTY_NOTICES.md'):
                    file = root / tag / name
                    file.parent.mkdir(parents=True, exist_ok=True)
                    file.write_bytes(name.encode())
                    files.append(str(file))
            files.append(str(root / 'README.md'))
            other = str(Path(tmp) / 'harness/i18n/locales/en.json')
            command = build_support.BuildPy(Distribution())
            with patch.object(build_support.build_py, 'find_data_files', return_value=files + [other]):
                for tag in ('macos-arm64', 'windows-x86_64'):
                    with self.subTest(tag=tag), patch.object(build_support, 'hex_platform_tag', return_value=tag):
                        self.assertEqual(command.find_data_files('harness', str(root.parent.parent)),
                                         [file for file in files if Path(file).parent.name == tag] + [other])

    def test_build_output_cannot_overlap_engine_source(self):
        """清理构建残留前拒绝与源码重叠的输出目录。"""
        with tempfile.TemporaryDirectory() as tmp, patch.object(build_support, '__file__', str(Path(tmp)/'build_support.py')):
            source = Path(tmp) / 'harness/resources/hex/macos-arm64/hex'
            source.parent.mkdir(parents=True)
            source.write_bytes(b'keep source')
            command = build_support.BuildPy(Distribution())
            command.build_lib = tmp
            with self.assertRaisesRegex(RuntimeError, 'overlaps Hex engine source'):
                command.run()
            self.assertEqual(source.read_bytes(), b'keep source')

    def test_wheel_and_sdist_use_single_source_without_foreign_or_stale_engines(self):
        """实际构建及重建发行包，确认源码保留全部平台而 wheel 只分发本机资源。"""
        with tempfile.TemporaryDirectory(prefix='harness-hex-build-') as tmp:
            project = Path(tmp) / 'source'
            project.mkdir()
            repository = Path(__file__).resolve().parents[1]
            for name in ('pyproject.toml', 'MANIFEST.in', 'build_support.py', 'README.md'):
                shutil.copy2(repository / name, project / name)
            (project / 'harness').mkdir()
            (project / 'harness/__init__.py').write_bytes(b'')
            tag = build_support.hex_platform_tag()
            filename = 'hex.exe' if sys.platform == 'win32' else 'hex'
            resources = {f'{tag}/{filename}': b'native binary', f'{tag}/LICENSE': b'license',
                         f'{tag}/engine.json': b'{}', f'{tag}/THIRD_PARTY_NOTICES.md': b'notices',
                         'foreign-platform/hex': b'foreign binary', 'README.md': b'resource documentation'}
            for relative, content in resources.items():
                file = project / 'harness/resources/hex' / relative
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_bytes(content)

            def build(root, operation):
                """只构建隔离的小型夹具，不安装依赖。"""
                result = subprocess.run([sys.executable, '-c',
                                         f'from setuptools import build_meta; build_meta.{operation}("dist")'],
                                        cwd=root, capture_output=True, text=True, encoding='utf-8')
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            def check_wheel(root, expected):
                """逐字节检查实际 wheel 的引擎资源与平台标签。"""
                wheel = next((root / 'dist').glob('*.whl'))
                self.assertFalse(wheel.name.endswith('-any.whl'))
                with ZipFile(wheel) as archive:
                    # 本机 wheel 的纯 Python 文件可放在 .data/purelib，安装时映射到包根。
                    installed = {name.split('.data/purelib/', 1)[-1]: archive.read(name)
                                 for name in archive.namelist()}
                    actual = {name.removeprefix('harness/resources/hex/'): content
                              for name, content in installed.items() if name.startswith('harness/resources/hex/')}
                self.assertEqual(actual, expected)

            expected = {name: content for name, content in resources.items() if name.startswith(tag + '/')}
            build(project, 'build_wheel')
            check_wheel(project, expected)
            # 复用构建目录时不得残留外平台引擎或已删除的本机资源。
            stale = project / 'build/lib/harness/resources/hex/foreign-platform/hex'
            stale.parent.mkdir(parents=True, exist_ok=True)
            stale.write_bytes(b'stale output')
            (project / 'harness/resources/hex' / tag / 'LICENSE').unlink()
            del expected[f'{tag}/LICENSE']
            build(project, 'build_wheel')
            check_wheel(project, expected)
            build(project, 'build_sdist')
            unpacked = Path(tmp) / 'unpacked'
            with tarfile.open(next((project / 'dist').glob('*.tar.gz'))) as archive:
                archive.extractall(unpacked, filter='data')
            restored = next(unpacked.iterdir())
            for relative, content in resources.items():
                if relative != f'{tag}/LICENSE':
                    self.assertEqual((restored / 'harness/resources/hex' / relative).read_bytes(), content)
            build(restored, 'build_wheel')
            check_wheel(restored, expected)
