"""按确定顺序流式匹配文件，剪除被忽略和无关的子树。"""

import fnmatch
import os
from pathlib import Path

if __package__:
    from .fs_errors import SearchError
else:
    from fs_errors import SearchError


def iter_files(root: Path, pattern: str, ignores: set[str], *, recursive: bool = False):
    pattern = pattern.replace('\\', '/') if os.name == 'nt' else pattern
    parts = tuple(part for part in pattern.split('/') if part not in {'', '.'})
    if pattern.startswith('/') or Path(pattern).drive or '..' in parts or any('**' in part and part != '**' for part in parts):
        raise SearchError('python.fs_walk.51f5eb2fa72f', code='FS_INVALID_GLOB')
    if not parts or pattern.endswith('/'):
        return
    if parts[-1] == '**':
        # 自行展开尾部递归匹配，所有受支持的 Python 版本保持相同行为。
        parts += ('*',)
    if recursive:
        parts = ('**',) + parts

    def close(states):
        states = set(states)
        for index in range(len(parts)):
            if index in states and parts[index] == '**':
                states.add(index + 1)
        return states

    def advance(states, name, *, follow_recursive=True):
        matched = set()
        for index in states:
            if index == len(parts):
                continue
            if parts[index] == '**':
                if follow_recursive:
                    matched.add(index)
            elif fnmatch.fnmatch(name, parts[index]):
                matched.add(index + 1)
        return close(matched)

    def walk(directory, states, ancestors):
        try:
            with os.scandir(directory) as scan:
                entries = sorted(scan, key=lambda entry: os.path.normcase(entry.name))
        except OSError:
            return
        for entry in entries:
            next_states = advance(states, entry.name)
            if not next_states:
                continue
            path = Path(entry.path)
            try:
                if entry.is_dir():
                    if entry.name in ignores:
                        continue
                    # 递归通配符不跟随符号链接目录。
                    # 显式路径段可以跟随符号链接，与 Path.glob 一致。
                    if entry.is_symlink():
                        next_states = advance(states, entry.name, follow_recursive=False)
                    if not any(index < len(parts) for index in next_states):
                        continue
                    actual = path.resolve()
                    if actual not in ancestors:
                        yield from walk(path, next_states, ancestors | {actual})
                elif len(parts) in next_states and entry.is_file():
                    yield path
            except OSError:
                continue

    yield from walk(root, close({0}), {root.resolve()})
