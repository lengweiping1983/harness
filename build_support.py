"""将唯一系统技能目录作为 wheel 的同级资源分发。"""
from pathlib import Path
import shutil

from setuptools.command.build_py import build_py


class BuildPy(build_py):
    """保持源码、wheel 与桌面包的系统技能路径一致。"""
    def run(self):
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
