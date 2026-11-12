"""分发系统技能和本机平台的 Hex 语音资源。"""
from pathlib import Path
import platform
import shutil
import sys

from setuptools.command.build_py import build_py
from setuptools.command.bdist_wheel import bdist_wheel


def hex_platform_tag():
    """与运行时及桌面脚本使用相同的平台资源标签。"""
    system = {'darwin': 'macos', 'win32': 'windows', 'linux': 'linux'}.get(sys.platform, sys.platform)
    machine = platform.machine().lower()
    arch = {'aarch64': 'arm64', 'amd64': 'x86_64'}.get(machine, machine)
    return f'{system}-{arch}'


class BuildPy(build_py):
    """保持技能路径一致，避免 wheel 携带其他平台的引擎。"""

    def find_data_files(self, package, src_dir):
        """源码发行包保留全部引擎，wheel 仅选择当前平台子目录。"""
        root = Path(__file__).resolve().parent / 'harness' / 'resources' / 'hex'
        files = super().find_data_files(package, src_dir)
        return [file for file in files if not Path(file).resolve().is_relative_to(root)
                or Path(file).resolve().relative_to(root).parts[0] == hex_platform_tag()]

    def run(self):
        source_hex = Path(__file__).resolve().parent / 'harness' / 'resources' / 'hex'
        built_hex = Path(self.build_lib).resolve() / 'harness' / 'resources' / 'hex'
        if built_hex.resolve().is_relative_to(source_hex.resolve()) or source_hex.resolve().is_relative_to(built_hex.resolve()):
            raise RuntimeError('Build output overlaps Hex engine source')
        if any(directory.is_symlink() for directory in (built_hex, built_hex.parent, built_hex.parent.parent)):
            raise RuntimeError('Hex engine build output must not contain symbolic links')
        # 只刷新构建副本，防止切换平台或删除资源后复用旧引擎。
        if built_hex.exists():
            shutil.rmtree(built_hex)
        super().run()
        source = Path(__file__).resolve().parent / '.harness' / 'skills'
        if source.exists() and not source.is_dir():
            raise RuntimeError('System skill source must be a directory')
        if source.is_symlink() or source.parent.is_symlink() or any(path.is_symlink() for path in source.rglob('*')):
            raise RuntimeError('System skill resources must not contain symbolic links')
        destination = Path(self.build_lib).resolve() / '.harness' / 'skills'
        if destination.is_relative_to(source) or source.is_relative_to(destination):
            raise RuntimeError('Build output overlaps system skill source')
        if destination.is_symlink() or destination.parent.is_symlink():
            raise RuntimeError('System skill build output must not be a symbolic link')
        # 仅刷新构建输出副本，避免已删除的系统技能残留在下一次 wheel 中。
        if destination.exists():
            shutil.rmtree(destination)
        # 当前发行版允许没有预装技能；仅清理构建副本，不触碰用户安装目录。
        if source.is_dir():
            shutil.copytree(source, destination, ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.pyo', '.DS_Store'))

    def get_outputs(self, include_bytecode=True):
        """让构建记录包含实际分发的全部技能资源。"""
        destination = Path(self.build_lib) / '.harness' / 'skills'
        resources = [str(path) for path in destination.rglob('*') if path.is_file()]
        return super().get_outputs(include_bytecode=include_bytecode) + resources


class BdistWheel(bdist_wheel):
    """含本机语音二进制的 wheel 不得宣称可跨平台安装。"""

    def finalize_options(self):
        super().finalize_options()
        root = Path(__file__).resolve().parent / 'harness' / 'resources' / 'hex' / hex_platform_tag()
        if (root / ('hex.exe' if sys.platform == 'win32' else 'hex')).is_file():
            self.root_is_pure = False

    def get_tag(self):
        python, abi, platform = super().get_tag()
        if not self.root_is_pure:
            return 'py3', 'none', platform
        return python, abi, platform
