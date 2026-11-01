"""使用真实构建核对声明式入口和源码发行包中的资源分发。"""
from pathlib import Path
from email.parser import BytesParser
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]


class BuildDistributionTests(unittest.TestCase):
    def test_wheel_and_sdist_preserve_resources(self):
        """从源码及 sdist 构建 wheel，并核对资源字节与构建模块隔离。"""
        with tempfile.TemporaryDirectory(prefix='harness-build-test-') as temporary:
            root = Path(temporary)
            project = root / 'source'
            project.mkdir()
            for name in ('pyproject.toml', 'MANIFEST.in', 'build_support.py', 'README.md'):
                shutil.copy2(ROOT / name, project / name)
            resources = {
                'harness/__init__.py': b'',
                'harness/py.typed': b'',
                'harness/i18n/locales/zh.json': b'{}',
                'harness/tools/builtin/guest.mjs': b'export {};',
                'harness/llm/opencode_local.mjs': b'export {};',
                'harness/llm/runtime/bridge.mjs': b'export {};',
                'harness/llm/runtime/catalog.json': b'[]',
                'harness/llm/runtime/package.json': b'{"type":"module"}',
                'harness/llm/runtime/LICENSE.pi-ai': b'MIT',
                'harness/llm/runtime/THIRD-PARTY-NOTICES.txt': b'notices',
                'harness/academy/content/sample/course.json': b'{}',
                'harness/academy/content/sample/locales/en.json': b'{}',
                'harness/academy/content/sample/lessons/first/starter/agent.py': b'print(42)',
                'harness/academy/content/sample/lessons/first/check.py': b'assert True',
                '.harness/skills/probe/SKILL.md': b'# distribution probe',
                '.harness/skills/probe/nested/resource.txt': b'resource bytes',
            }
            # 核对实际团队课程的清单、双语、示例、检查与参考文件进入两种发行路径。
            for identity in ('python-foundations', 'llm-foundations'):
                course = ROOT / 'harness/academy/content' / identity
                for source in course.rglob('*'):
                    if source.is_file() and source.suffix in {'.json', '.md', '.py'}:
                        resources[source.relative_to(ROOT).as_posix()] = source.read_bytes()
            for name, content in resources.items():
                path = project / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            ignored = project / '.harness/skills/probe/cache.pyc'
            ignored.write_bytes(b'ignored')
            self.build(project, 'build_wheel')
            self.check_wheel(project, resources)
            # 在同一构建目录重建，已删除的技能不能从旧副本泄漏。
            removed = '.harness/skills/probe/nested/resource.txt'
            (project / removed).unlink()
            resources.pop(removed)
            self.build(project, 'build_wheel')
            self.check_wheel(project, resources)
            self.build(project, 'build_sdist')
            unpacked = root / 'unpacked'
            with tarfile.open(next((project / 'dist').glob('*.tar.gz'))) as archive:
                archive.extractall(unpacked, filter='data')
            restored = next(unpacked.iterdir())
            self.assertTrue((restored / 'build_support.py').is_file())
            self.assertFalse((restored / 'setup.py').exists())
            self.build(restored, 'build_wheel')
            self.check_wheel(restored, resources)
            shutil.rmtree(project / '.harness')
            self.build(project, 'build_wheel')
            self.check_wheel(project, {name: content for name, content in resources.items()
                                       if not name.startswith('.harness/')})

    def build(self, project, operation):
        """通过当前解释器调用 PEP 517 后端，不安装依赖或触及工作区。"""
        result = subprocess.run(
            [sys.executable, '-c',
             'import warnings; from setuptools.warnings import SetuptoolsDeprecationWarning; '
             'warnings.simplefilter("error", SetuptoolsDeprecationWarning); '
             f'from setuptools import build_meta; build_meta.{operation}("dist")'],
            cwd=project, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def check_wheel(self, project, resources):
        """核对包资源、技能目录和构建辅助模块的发行边界。"""
        with ZipFile(next((project / 'dist').glob('*.whl'))) as archive:
            metadata_path = next(name for name in archive.namelist()
                                 if name.endswith('.dist-info/METADATA'))
            metadata = BytesParser().parsebytes(archive.read(metadata_path))
            self.assertEqual(metadata['License-Expression'], 'MIT')
            self.assertIsNone(metadata['License'])
            self.assertFalse(any(value.startswith('License ::')
                                 for value in metadata.get_all('Classifier', [])))
            actual = {name: archive.read(name) for name in archive.namelist()
                      if '.dist-info/' not in name}
        self.assertEqual(actual, resources)
