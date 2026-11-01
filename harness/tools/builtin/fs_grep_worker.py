"""独立搜索工作进程，不启动 Harness，也不依赖第三方库。"""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys

if __package__:
    from .fs_errors import SearchError
    from .fs_walk import iter_files
    from .fs_text import text_lines, decode_utf8_prefix
else:
    # -I 排除当前目录和 PYTHONPATH；共享遍历器仅需
    # 此已安装的可信目录，不导入整个 Harness 包。
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from fs_errors import SearchError
    from fs_walk import iter_files
    from fs_text import text_lines, decode_utf8_prefix

READ_BYTES = 256 * 1024
MAX_CONTEXT_LINES = 400


def read_text(path):
    with path.open('rb') as source:
        data = source.read(READ_BYTES + 1)
    if b'\x00' in data[:4096]:
        return None, False
    partial = len(data) > READ_BYTES
    return decode_utf8_prefix(data[:READ_BYTES], truncated=partial), partial


def resolve_scope(root, args):
    """把可选的 ``path`` 参数解析成搜索范围。

    返回 ``(scan_root, single_file)``：``path`` 指向目录时以其为扫描根
    （命中的展示路径仍相对工作目录根）；指向文件时只搜该文件；
    省略时扫描整个工作目录。
    """
    raw = args.get('path')
    if raw is None:
        return root, None
    if not isinstance(raw, str) or not raw.strip():
        raise SearchError('python.fs_grep_worker.6e5b86a9b568', code='FS_INVALID_PATH')
    candidate = Path(raw.strip())
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise SearchError('python.fs_grep_worker.cc6602f0a5af', message_values={'p0': raw}, code='FS_INVALID_PATH', path=raw)
    if candidate.is_file():
        return root, candidate
    if candidate.is_dir():
        return candidate, None
    raise SearchError('python.fs_grep_worker.ea2d286a441e', message_values={'p0': raw}, code='FS_NOT_FOUND', path=str(candidate))


def search_files(request):
    root = Path(request['root'])
    args = request['args']
    try:
        pattern = re.compile(args['pattern'])
    except re.error as exc:
        raise SearchError('python.fs_grep_worker.3f6b4c796ac0', message_values={'p0': str(exc)}, code='FS_INVALID_REGEX') from exc
    limit = max(0, min(int(args.get('limit', 200)), 200))
    scan_root, single_file = resolve_scope(root, args)
    if single_file is not None and limit > 0:
        candidates = iter((single_file,))
    elif limit > 0:
        candidates = iter_files(scan_root, args.get('glob', '**/*'), set(request['ignores']))
    else:
        candidates = ()
    context = args.get('context', 0)
    context_lines = []
    context_truncated = False
    matches, partial_files = [], []
    partial_file_count = 0
    skipped_files, skipped_file_count = [], 0
    for path in candidates:
        # 不能仅为区分恰好达到上限的结果而额外读取文件。
        if len(matches) >= limit:
            break
        # 对外相对路径统一使用正斜杠，与 git 输出及跨平台 API 契约一致。
        relative = path.relative_to(root).as_posix()
        try:
            text, partial = read_text(path)
        except (OSError, UnicodeError):
            skipped_file_count += 1
            if len(skipped_files) < 20:
                skipped_files.append(relative)
            continue
        if text is None:
            continue
        if partial:
            partial_file_count += 1
            if len(partial_files) < 20:
                partial_files.append(relative)
        file_lines = text_lines(text)
        file_matches = []
        for lineno, line in enumerate(file_lines, 1):
            if pattern.search(line):
                matches.append({'file': relative, 'line': lineno, 'text': line.strip()[:300]})
                file_matches.append(lineno)
                if len(matches) >= limit:
                    break
        if context:
            # 合并重叠窗口，命中行不计入额外上下文；全次查询共享输出上限。
            selected = set(file_matches)
            emitted = set()
            for lineno in file_matches:
                for nearby in range(max(1, lineno - context), min(len(file_lines), lineno + context) + 1):
                    if nearby in selected or nearby in emitted:
                        continue
                    if len(context_lines) >= MAX_CONTEXT_LINES:
                        context_truncated = True
                        break
                    emitted.add(nearby)
                    context_lines.append({'file': relative, 'line': nearby, 'text': file_lines[nearby - 1][:300]})
        if len(matches) >= limit:
            break
    return {'pattern': args['pattern'], 'count': len(matches), 'matches': matches,
            'truncated': len(matches) >= limit or bool(partial_file_count or skipped_file_count) or context_truncated,
            'context_lines': context_lines, 'context_truncated': context_truncated,
            'partial_files': partial_files, 'partial_file_count': partial_file_count,
            'skipped_files': skipped_files, 'skipped_file_count': skipped_file_count}


def main():
    try:
        request = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
        response = {'result': search_files(request)}
    except SearchError as exc:
        response = {'error': exc.to_dict()}
    except Exception as exc:
        response = {'error': SearchError('python.fs_grep_worker.e9602989da81',
                    message_values={'p0': str(exc)}, code='FS_SEARCH_FAILED').to_dict()}
    sys.stdout.buffer.write(json.dumps(response, ensure_ascii=False).encode('utf-8'))


if __name__ == '__main__':
    main()
