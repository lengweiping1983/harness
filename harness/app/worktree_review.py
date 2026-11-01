"""工作树交付的只读审查，不运行自定义 diff 或读取符号链接的目标。"""
from __future__ import annotations
from typing import Any
from harness.i18n import tr
from ..core.diff import unified_patch
from ..core.errors import HarnessError

MAX_FILE_BYTES = 256_000
MAX_DIFF_CHARS = 256_000


def review(service: Any, row: dict, *, path: str | None = None) -> dict:
    """按创建基线审查后续提交和编辑，基线缺失时明确报错。"""
    target = service._target(row)
    base = row.get('base_commit')
    if not isinstance(base, str) or not base:
        raise HarnessError(tr('worktree.review.missing_baseline'), code='git/review-format')
    base = service.git(target, 'rev-parse', '--verify', base + '^{commit}').strip()
    options = ['--no-ext-diff', '--no-textconv', '--no-color', '--no-renames']
    values = service._git_bytes(target, 'diff', '--name-status', '-z', *options, base, '--').decode('utf-8').split('\0')
    files = [{'status':values[index], 'path':values[index + 1], 'kind':'tracked'}
             for index in range(0, len(values)-1, 2)]
    untracked = service._git_bytes(target, 'ls-files', '--others', '--exclude-standard', '-z').decode('utf-8').split('\0')
    files.extend({'status':'?', 'path':name, 'kind':'untracked'} for name in untracked if name)
    result = {'id':row['id'], 'path':str(target), 'base_commit':base,
              'base_kind':'creation', 'files':files}
    if path is None:
        return result
    selected = next((item for item in files if item['path'] == path), None)
    if selected is None:
        raise HarnessError(tr('worktree.review.not_found'), code='git/review-file-not-found')
    file = target / path
    preview = {'path':path, 'diff':'', 'truncated':False, 'unavailable':None}
    result['selection'] = preview
    if file.is_symlink() or not file.resolve().is_relative_to(target):
        preview['unavailable'] = 'symlink'
        return result
    if file.exists() and not file.is_file():
        preview['unavailable'] = 'unsupported'
        return result
    size = file.stat().st_size if file.exists() else 0
    old_size = 0
    if selected['kind'] == 'tracked':
        object_size = service.run_git(target, 'cat-file', '-s', base + ':' + path, allowed_returncodes=(0, 128))
        if object_size.returncode == 0:
            old_size = int(object_size.stdout.strip())
    if max(size, old_size) > MAX_FILE_BYTES:
        preview['unavailable'] = 'large'
        return result
    if selected['kind'] == 'untracked':
        # 只读有上限的字节，文件在检查后变大也不会突破限制。
        with file.open('rb') as stream:
            data = stream.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            preview['unavailable'] = 'large'
            return result
        try:
            text = data.decode('utf-8')
            if '\0' in text:
                raise UnicodeError()
        except UnicodeError:
            preview['unavailable'] = 'binary'
            return result
        patch = unified_patch('', text, fromfile='/dev/null', tofile=path)
    else:
        raw = service._git_bytes(target, 'diff', *options, '--src-prefix=a/', '--dst-prefix=b/', base, '--', ':(top,literal)' + path)
        try:
            patch = raw.decode('utf-8')
        except UnicodeError:
            preview['unavailable'] = 'binary'
            return result
        if any(line.startswith('Binary files ') for line in patch.split('\n')):
            preview['unavailable'] = 'binary'
            return result
    preview['truncated'] = len(patch) > MAX_DIFF_CHARS
    preview['diff'] = patch[:MAX_DIFF_CHARS]
    return result
