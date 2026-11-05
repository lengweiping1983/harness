"""内置文件工具：read / write / edit / glob / grep。

安全设计：
* 所有路径都基于会话工作目录 ``cwd`` 解析；
* 逃逸检查防止 ``../`` 穿越到工作目录之外；
* 大文件读取自动截断，避免撑爆上下文；
* 二进制文件被拒绝并给出明确原因。
"""

from __future__ import annotations

from harness.i18n import tr

import os
import hashlib
from itertools import islice
from pathlib import Path
from typing import Any

from ...core.errors import ToolExecutionError
from ...llm.types import TextBlock
from ..registry import ToolDefinition, ToolRunContext
from . import fs_observation
from .fs_edit_diagnostics import ambiguous_edit, edit_not_found, edited_context
from .fs_errors import SearchError
from .fs_text import normalize_eol as _normalize_eol, text_lines, fenced_source, splice_source, decode_utf8_prefix
from .fs_walk import iter_files as walk_files
from .file_diff import before_write, file_diff

#: 读取文件的最大行数
MAX_READ_LINES = 2000
#: 读取文件的最大字节数
MAX_READ_BYTES = 512 * 1024
#: 编辑必须完整读取；超过此上限则拒绝，不能以读取前缀覆盖原文件
MAX_EDIT_BYTES = 8 * 1024 * 1024
#: grep 结果的最大命中数
MAX_GREP_MATCHES = 200
#: 默认忽略的目录名
DEFAULT_IGNORES = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache", "dist", "build"}


def iter_files(*args, **kwargs):
    """在宿主边界渲染遍历错误，保持 worker 的依赖独立。"""
    try:
        yield from walk_files(*args, **kwargs)
    except SearchError as exc:
        raise ToolExecutionError(tr(exc.message_key, **exc.message_values),
                                 code=exc.code, **exc.details) from exc


def resolve_path(cwd: str | None, path: str) -> Path:
    """把模型的相对路径解析为绝对路径，并阻止逃出工作目录。

    :raises ToolExecutionError: 路径逃逸。
    """
    base = Path(cwd).expanduser().resolve() if cwd else Path.cwd().resolve()
    target = (base / path).expanduser().resolve() if not os.path.isabs(path) else Path(path).expanduser().resolve()
    # 允许访问工作目录之外的绝对路径（很多场景需要读 /tmp 等），
    # 但明确拒绝通过 ../ 相对穿越——这通常是模型幻觉产生的路径。
    if not os.path.isabs(path):
        try:
            target.relative_to(base)
        except ValueError as exc:
            raise ToolExecutionError(
                tr('python.fs_tools.305e014b24cd', p0=f'{path!r}', p1=f'{base}'), path=path, cwd=str(base)
            ) from exc
    return target


def _missing_parent_context(target: Path, root: Path) -> dict[str, Any]:
    """只在工作目录内列举最近父目录，不读取候选内容或建立文件观察。"""
    try:
        target.relative_to(root)
        parent = target.parent
        parent.relative_to(root)
        while not parent.is_dir():
            if parent == root:
                return {}
            parent = parent.parent
        entries: list[str] = []
        with os.scandir(parent) as scan:
            for entry in scan:
                if entry.name.startswith('.') or entry.name in DEFAULT_IGNORES:
                    continue
                suffix = '/' if entry.is_dir(follow_symlinks=False) else ''
                entries.append(entry.name + suffix)
                if len(entries) == 33:
                    break
        return {
            'parent': str(parent),
            'entries': sorted(entries[:32]),
            'entries_truncated': len(entries) > 32,
        }
    except (OSError, ValueError):
        # 诊断失败不能覆盖原始缺失错误，也不能将未知目录报告为空。
        return {}


def _not_found_error(target: Path, cwd: str | None) -> ToolExecutionError:
    """构造「文件不存在」错误，尽量附带可执行的恢复线索。

    模型凭直觉拼错路径时，干巴巴的「文件不存在」是死胡同——它只能
    继续猜，再错再报。这里在工作目录内按**同名文件**搜索候选
    （:func:`iter_files` 自带 node_modules 等忽略目录剪枝），
    命中时仅返回候选而不自动替换；未命中则附上有界的父目录定位信息。
    """
    root = Path(cwd).expanduser().resolve() if cwd else Path.cwd().resolve()
    candidates: list[str] = []
    try:
        target.relative_to(root)
    except ValueError:
        pass  # 目标在工作目录之外，搜工作目录没有意义
    else:
        if target.name:
            for match in islice(iter_files(root, target.name, DEFAULT_IGNORES, recursive=True), 5):
                try:
                    candidates.append(str(match.relative_to(root)))
                except ValueError:
                    candidates.append(str(match))
    if candidates:
        shown = tr('ui.common.list_separator').join(candidates)
        return ToolExecutionError(
            tr('python.fs_tools.0671152acd33', p0=f'{target}', p1=f'{shown}'),
            code='FS_NOT_FOUND',
            path=str(target),
            candidates=candidates,
        )
    context = _missing_parent_context(target, root)
    message = tr('python.fs_tools.0b7364a3f83d', p0=f'{target}', p1=f'{target.name}')
    if context:
        message += '\n' + tr('tools.read.missing_parent',
            parent=context['parent'], entries='\n'.join(context['entries']) or tr('tools.read.parent_empty'))
        if context['entries_truncated']:
            message += '\n' + tr('tools.read.parent_truncated')
    return ToolExecutionError(message, code='FS_NOT_FOUND', path=str(target), **context)


def _read_snapshot(path: Path, max_bytes: int = MAX_READ_BYTES, *, require_complete: bool = True):
    """对实际检查的字节计算哈希，CAS 也计入未展示的后缀。"""
    with path.open('rb') as source:
        before = os.fstat(source.fileno())
        data = source.read(max_bytes + 1)
        if len(data) > max_bytes and require_complete:
            raise ToolExecutionError(tr('python.fs_tools.2786fe89fd72'),
                                     code='FS_FILE_TOO_LARGE', path=str(path), max_bytes=max_bytes)
        digest = hashlib.sha256(data)
        size = len(data)
        while chunk := source.read(65536):
            digest.update(chunk)
            size += len(chunk)
        after = os.fstat(source.fileno())
    # Windows 上 Python 3.14 的 fstat 与 stat 所报告的 ctime 语义不同，
    # 分别表示变更时间和原先的创建时间；仅比较共有的
    # 标识、大小和 mtime 字段，并保留 POSIX 元数据变更检测。
    def identity(stat):
        shared = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
        return shared if os.name == 'nt' else (*shared, stat.st_ctime_ns)
    try:
        stable = identity(before) == identity(after) == identity(path.stat())
    except OSError:
        stable = False
    if not stable:
        raise ToolExecutionError(tr('python.fs_tools.1c67ace790c6'),
                                 code='FS_STALE', path=str(path))
    data = data[:max_bytes]
    if b"\x00" in data[:4096]:
        raise ToolExecutionError(tr('python.fs_tools.c32d9f4bfd79', p0=f'{path.name}'))
    try:
        text = decode_utf8_prefix(data, truncated=size > max_bytes)
    except UnicodeError as exc:
        raise ToolExecutionError(tr('python.fs_tools.3e9082939296'),
                                 code='FS_ENCODING', path=str(path)) from exc
    return text, digest.hexdigest()[:16], size > max_bytes


def _read_text(path: Path, max_bytes: int = MAX_READ_BYTES) -> str:
    """完整读取编辑原文，超限和非法编码均明确拒绝。"""
    return _read_snapshot(path, max_bytes)[0]


def _text_version(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]


def _write_text(path: Path, text: str, *, create_only: bool = False) -> None:
    """按原样写回文本，不做换行符转换。

    ``Path.write_text`` 在 Windows 上默认使用文本模式，会把每个 ``\n`` 再翻译成
    ``\r\n``。而 :func:`_read_text` 按字节读取、会保留文件原有的 ``\r\n``，
    两者叠加会让每次 ``edit`` 把行尾变成 ``\r\r\n``（每行多出一个空行）。
    这里用 ``newline=""`` 关闭翻译，保证「读到什么就写回什么」。
    """
    with path.open('x' if create_only else 'w', encoding="utf-8", newline="") as handle:
        handle.write(text)


def _write_if_changed(path: Path, text: str, *, expected_version: str = '', create_only: bool = False) -> bool:
    """按有界分块逐字节比较，内容相同则无需写入。"""
    data = text.encode("utf-8")
    if create_only:
        try:
            _write_text(path, text, create_only=True)
        except FileExistsError as exc:
            raise fs_observation.write_exists_error(path) from exc
        return True
    if path.is_file() and path.stat().st_size == len(data):
        with path.open("rb") as source:
            offset = 0
            while True:
                chunk = source.read(65536)
                if not chunk:
                    if offset == len(data):
                        fs_observation.check_fresh(path, expected_version)
                        return False
                    break
                if chunk != data[offset:offset + len(chunk)]:
                    break
                offset += len(chunk)
    fs_observation.check_fresh(path, expected_version)
    _write_text(path, text)
    return True


def file_progress_key(args: Any, value: Any) -> Any:
    """同一未变化文件状态只计数一次，不受路径写法影响。"""
    if value.get("changed") is not False:
        return None
    target = Path(value["path"])
    with target.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    operation = {key: value for key, value in args.items() if key not in {"path", "content"}}
    for key in ("old_string", "new_string"):
        if isinstance(operation.get(key), str):
            operation[key] = _normalize_eol(operation[key])
    return [fs_observation.target_key(target), digest, operation]

#: 单次 view 输出最多展示的字符数
MAX_VIEW_CHARS = 16000


def _truncate_without_splitting_surrogate_pair(text: str, max_chars: int) -> str:
    """按字符数截断，尽量不在代理对中间切开。"""
    head = text[:max_chars]
    if head and 0xD800 <= ord(head[-1]) <= 0xDBFF:
        head = head[:-1]
    return head


def maybe_truncate(content: str, max_output_chars: int = MAX_VIEW_CHARS) -> str:
    """输出超长时追加截断提示。"""
    if len(content) <= max_output_chars:
        return content
    return _truncate_without_splitting_surrogate_pair(content, max_output_chars) + "\n\n" + tr('owned.fs_tools.46b829d8ad9f')


def match_offsets(content: str, search: str) -> list[int]:
    """返回 ``search`` 在 ``content`` 中所有不重叠出现的起始下标。"""
    offsets: list[int] = []
    if not search:
        return offsets
    cursor = 0
    while True:
        found = content.find(search, cursor)
        if found < 0:
            return offsets
        offsets.append(found)
        cursor = found + len(search)


def line_numbers_at(content: str, offsets: list[int]) -> list[int]:
    """把字符下标换算成 1-based 行号。"""
    if not offsets:
        return []
    line_starts = [0]
    for index, char in enumerate(content):
        if char == "\n":
            line_starts.append(index + 1)
    numbers: list[int] = []
    for offset in offsets:
        low, high = 0, len(line_starts) - 1
        while low < high:
            mid = (low + high + 1) // 2
            if line_starts[mid] <= offset:
                low = mid
            else:
                high = mid - 1
        numbers.append(low + 1)
    return numbers


def _codepoint_key(name: str) -> tuple[int, ...]:
    """按码点排序。"""
    return tuple(ord(ch) for ch in name)


def view_directory(root: Path, depth: int = 2) -> str:
    """列举目录下 ``depth`` 层、非隐藏条目，``d\t`` 表示目录、``f\t`` 表示文件。"""
    rows: list[str] = []

    def walk(directory: Path, level: int) -> None:
        try:
            entries = sorted(directory.iterdir(), key=lambda item: _codepoint_key(item.name))
        except OSError as exc:
            raise ToolExecutionError(
                tr('fs.directory.read_failed', path=str(directory), reason=str(exc)),
                code='FS_DIRECTORY_READ', path=str(directory),
            ) from exc
        for entry in entries:
            if entry.name.startswith("."):
                continue
            try:
                is_dir = entry.is_dir()
            except OSError:
                is_dir = False
            rows.append(f"{'d' if is_dir else 'f'}\t{entry.relative_to(root).as_posix()}")
            if is_dir and level < depth:
                walk(entry, level + 1)

    walk(root, 1)
    return "\n".join(rows)


def normalize_view_range(view_range: Any, total: int, target: Path, *,
                         total_known: bool = True) -> tuple[int, int]:
    """校验只读范围；结束行超过可读边界时截断，起始行越界仍明确拒绝。"""
    details = dict(path=str(target), available_lines=total, total_lines_known=total_known)
    if (not isinstance(view_range, (list, tuple)) or len(view_range) != 2
            or any(isinstance(value, bool) or not isinstance(value, int) for value in view_range)):
        raise ToolExecutionError(
            tr('python.fs_tools.c9f583d8ebc3', p0=f'{view_range!r}'),
            code='FS_READ_RANGE', **details,
        )
    start, end = view_range
    if start < 1 or (end != -1 and end < start):
        raise ToolExecutionError(
            tr('tools.fs.invalid_range'),
            code='FS_READ_RANGE', **details,
        )
    # 空文件允许从第一行读取，返回空内容；不会虚构第一行。
    if start > max(1, total):
        raise ToolExecutionError(
            tr('tools.fs.range_file' if total_known else 'tools.fs.range_prefix', start=start, total=total),
            code='FS_READ_RANGE' if total_known else 'FS_READ_PREFIX_LIMIT', **details,
        )
    return start, total if end == -1 else min(end, total)


def read_range_notice(view_range: Any, end: int, *, total_known: bool) -> str:
    """向模型说明显式结束行的截断，区分文件末尾与加载前缀。"""
    if view_range is None or view_range[1] == -1 or view_range[1] <= end:
        return ''
    key = 'tools.read.range_eof' if total_known else 'tools.read.range_prefix'
    return tr(key, start=view_range[0], requested_end=view_range[1], actual_end=end)


def _finish_edit(target: Path, before_text: str, after_text: str, extra: dict[str, Any]) -> dict[str, Any]:
    """写回文件并统一组装 edit 工具的返回值。"""
    snapshot = before_write(target)
    changed = _write_if_changed(target, after_text, expected_version=_text_version(before_text))
    before, after = _normalize_eol(before_text).split("\n"), _normalize_eol(after_text).split("\n")
    payload: dict[str, Any] = {
        '_written_version': _text_version(after_text),
        "path": str(target),
        "lines_before": len(before),
        "lines_after": len(after),
        **file_diff(snapshot, after_text, target, changed=changed),
        "changed": changed,
        "message": (tr('tools.fs.edited', path=str(target)) if changed else
                    tr('python.fs_tools.a75a904fdb94', p0=f'{target}')),
    }
    payload.update(extra)
    if not changed:
        payload["replacements"] = 0
        if "inserted_lines" in payload:
            payload["inserted_lines"] = 0
    return payload


def apply_str_replace(target: Path, old: Any, new: Any, replace_all: bool, *, expected_version: str = '') -> dict[str, Any]:
    """``old_string`` -> ``new_string`` 的精确替换。"""
    if not isinstance(old, str) or old == "":
        raise ToolExecutionError(
            tr('python.fs_tools.cb0be60ea720'),
            path=str(target),
        )
    if not isinstance(new, str):
        raise ToolExecutionError(
            tr('tools.fs.invalid_replacement'),
            path=str(target),
        )
    raw = _read_text(target, MAX_EDIT_BYTES)
    fs_observation.check_fresh(target, expected_version)
    text = _normalize_eol(raw)
    old, new = _normalize_eol(old), _normalize_eol(new)
    offsets = match_offsets(text, old)
    if not offsets:
        raise edit_not_found(target, text, old, new)
    matched_lines = line_numbers_at(text, offsets)
    if len(offsets) > 1 and not replace_all:
        raise ambiguous_edit(target, matched_lines)
    selected = offsets if replace_all else offsets[:1]
    updated = splice_source(raw, [(offset, offset + len(old), new) for offset in selected])
    count = len(offsets) if replace_all else 1
    payload = _finish_edit(
        target, raw, updated, {"command": "str_replace", "replacements": count, "matched_lines": matched_lines}
    )
    if payload['changed']:
        payload['message'] += edited_context(_normalize_eol(updated), offsets[0], new)
    return payload


def apply_insert(target: Path, insert_line: Any, new: Any, *, expected_version: str = '') -> dict[str, Any]:
    """在 ``insert_line`` 行之后插入 ``new_string``。"""
    if isinstance(insert_line, bool) or not isinstance(insert_line, int):
        raise ToolExecutionError(
            tr('tools.fs.invalid_insert_line', value=repr(insert_line)), path=str(target)
        )
    if not isinstance(new, str):
        raise ToolExecutionError(
            tr('tools.fs.invalid_insertion'), path=str(target)
        )
    raw = _read_text(target, MAX_EDIT_BYTES)
    fs_observation.check_fresh(target, expected_version)
    text = _normalize_eol(raw)
    lines = text_lines(text)
    if insert_line < 0 or insert_line > len(lines):
        raise ToolExecutionError(
            tr('tools.fs.insert_range', value=insert_line, total=len(lines)),
            path=str(target),
        )
    payload = _normalize_eol(new).split("\n")
    offset = sum(len(line) + 1 for line in lines[:insert_line])
    addition = _normalize_eol(new) + '\n' if text else _normalize_eol(new)
    if text and insert_line == len(lines) and not text.endswith('\n'):
        offset = len(text)
        addition = '\n' + _normalize_eol(new)
    updated = splice_source(raw, [(offset, offset, addition)])
    result = _finish_edit(
        target,
        raw,
        updated,
        {
            "command": "insert",
            "replacements": len(payload),
            "inserted_line": insert_line,
            "inserted_lines": len(payload),
        },
    )
    if result['changed']:
        context_offset = sum(len(line) + 1 for line in lines[:insert_line])
        result['message'] += edited_context(_normalize_eol(updated), context_offset, _normalize_eol(new))
    return result


def build_fs_tools(cwd: str | None, *, observation_policy: bool = True) -> list[ToolDefinition]:
    """构造文件工具，观察策略固定属于本组定义，不影响其他实例。"""
    observation_policy = bool(observation_policy)
    observation_cache = fs_observation.ObservationCache()

    def read_tool(args: Any, ctx: ToolRunContext) -> Any:
        """读取文件内容（带行号）；传入目录则列举两层非隐藏条目。"""
        target = resolve_path(ctx.cwd or cwd, args["path"])
        if not target.exists():
            error = _not_found_error(target, ctx.cwd or cwd)
            error.file_observation = fs_observation.observe(ctx.session_id, target, 'absent', session=ctx.services.get('session'), enabled=observation_policy, cache=observation_cache)
            raise error
        if target.is_dir():
            listing = view_directory(target)
            entries = len([row for row in listing.split("\n") if row])
            return {
                "path": str(target),
                "kind": "directory",
                "entries": entries,
                "lines": 0,
                "total_lines": 0,
                "truncated": False,
                "content": maybe_truncate(listing),
            }
        text, version, byte_truncated = _read_snapshot(target, require_complete=False)
        lines = text_lines(text)
        view_range = args.get("view_range")
        if view_range is not None:
            start, end = normalize_view_range(view_range, len(lines), target,
                                              total_known=not byte_truncated)
        else:
            start = args.get("offset", 1)
            requested_end = start + args.get("limit", MAX_READ_LINES) - 1
            start, end = normalize_view_range([start, requested_end], len(lines), target,
                                              total_known=not byte_truncated)
        notice = read_range_notice(view_range, end, total_known=not byte_truncated)
        if view_range is not None and "limit" in args:
            end = min(end, start + args["limit"] - 1)
        selected = lines[start - 1 : end]
        truncated = len(selected) < len(lines[start - 1 :]) or byte_truncated
        numbered = args.get('line_numbers', True)
        if numbered:
            body = "\n".join(f"{start + i:6d}\t{line}" for i, line in enumerate(selected))
        else:
            body = '\n'.join(selected)
            if selected and (end < len(lines) or _normalize_eol(text).endswith('\n')):
                body += '\n'
        content = maybe_truncate(body) if numbered else _truncate_without_splitting_surrogate_pair(body, MAX_VIEW_CHARS)
        truncated = truncated or len(content) != len(body)
        source_notice = ''
        if not numbered and len(content) != len(body):
            source_notice += '\n' + tr('owned.fs_tools.46b829d8ad9f')
        if byte_truncated:
            source_notice += tr('python.fs_tools.44cff5472a5f', p0=f'{MAX_READ_BYTES}')
        if truncated and not byte_truncated:
            source_notice += (
                tr('python.fs_tools.5d41f2ed6066', p0=f'{len(lines)}')
            )
        source_notice += ('\n' + notice if notice else '')
        return {
            '_fs_observation': fs_observation.observe(ctx.session_id, target, 'present', version,
                                                     session=ctx.services.get('session'), enabled=observation_policy, cache=observation_cache),
            "path": str(target),
            "kind": "file",
            "lines": len(selected),
            "total_lines": len(lines),
            "total_lines_known": not byte_truncated,
            "truncated": truncated,
            "content": content + source_notice if numbered else content,
            "line_numbers": numbered,
            "notice": '' if numbered else source_notice,
        }

    def read_to_content(args: Any, value: Any) -> list[TextBlock]:
        """给模型看：路径 + 编号内容（或目录清单）。"""
        if value.get("kind") == "directory":
            return [TextBlock(text=tr('python.fs_tools.acaee1e82973', p0=f"{value['path']}", p1=f"{value['entries']}", p2=f"{value['content']}"))]
        key = 'fs.read.summary.total' if value.get('total_lines_known', True) else 'fs.read.summary.loaded'
        body = value['content'] if value.get('line_numbers', True) else fenced_source(value['content']) + value['notice']
        return [TextBlock(text=tr(key, path=value['path'], count=value['total_lines'], content=body))]

    def write_tool(args: Any, ctx: ToolRunContext) -> Any:
        """写入文件（自动创建父目录）。"""
        workspace_root = Path(ctx.cwd or cwd or Path.cwd()).expanduser().resolve()
        target = resolve_path(str(workspace_root), args["path"])
        session = ctx.services.get('session')
        fs_observation.guard_write(ctx.session_id, target, session=session, enabled=observation_policy, cache=observation_cache)
        prior = fs_observation.lookup(ctx.session_id, target, session=session, enabled=observation_policy, cache=observation_cache)
        target.parent.mkdir(parents=True, exist_ok=True)
        existed = target.exists()
        before = before_write(target)
        changed = _write_if_changed(target, args['content'],
            expected_version=prior[1] if prior and prior[0] == 'present' else '',
            create_only=bool(observation_policy and ctx.session_id and not (prior and prior[0] == 'present')))
        return {
            '_fs_observation': fs_observation.observe(ctx.session_id, target, 'present', _text_version(args['content']), session=session, enabled=observation_policy, cache=observation_cache),
            '_workspace_root': str(workspace_root),
            "path": str(target),
            "created": not existed,
            "bytes": target.stat().st_size,
            "changed": changed,
            **file_diff(before, args["content"], target, changed=changed, before_exists=existed),
        }

    def write_to_content(args: Any, value: Any) -> list[TextBlock]:
        if not value["changed"]:
            text = tr('python.fs_tools.fb4e54c1ba9b', p0=f"{value['path']}", p1=f"{value['bytes']}")
        else:
            key = 'fs.write.summary.created' if value['created'] else 'fs.write.summary.overwritten'
            text = tr(key, path=value['path'], size=value['bytes'])
        root = value['_workspace_root']
        if not Path(value['path']).is_relative_to(root):
            text += '\n' + tr('fs.write.outside_workspace', path=value['path'], root=root)
        return [TextBlock(text=text)]

    def edit_tool(args: Any, ctx: ToolRunContext) -> Any:
        """精确编辑文件：old_string 替换，或 insert_line 按行插入。"""
        target = resolve_path(ctx.cwd or cwd, args["path"])
        if not target.exists():
            error = _not_found_error(target, ctx.cwd or cwd)
            error.file_observation = fs_observation.observe(ctx.session_id, target, 'absent', session=ctx.services.get('session'), enabled=observation_policy, cache=observation_cache)
            raise error
        if target.is_dir():
            raise ToolExecutionError(tr('python.fs_tools.a4794cc84a12', p0=f'{target}'), path=str(target))
        version = fs_observation.require_observed(ctx.session_id, target, session=ctx.services.get('session'), enabled=observation_policy, cache=observation_cache)
        insert_line = args.get("insert_line")
        old = args.get("old_string")
        new = args.get("new_string")
        try:
            if insert_line is not None:
                payload = apply_insert(target, insert_line, new, expected_version=version)
            elif old is None:
                raise ToolExecutionError(
                    tr('python.fs_tools.3aae8cac4c5b'),
                    path=str(target),
                )
            else:
                payload = apply_str_replace(target, old, new, bool(args.get("replace_all", False)), expected_version=version)
        except ToolExecutionError as exc:
            if exc.code == 'FS_EDIT_NOT_FOUND':
                # 原文失配表明模型没有使用当前内容；撤销本会话的观察，
                # 不能通过换一个编辑参数或整文件写入绕过重读。
                exc.file_observation = fs_observation.observe(ctx.session_id, target, 'invalidated',
                    session=ctx.services.get('session'), enabled=observation_policy, cache=observation_cache)
            raise
        payload['_fs_observation'] = fs_observation.observe(ctx.session_id, target, 'present',
            payload.pop('_written_version'),
            session=ctx.services.get('session'), enabled=observation_policy, cache=observation_cache)
        return payload

    def edit_to_content(args: Any, value: Any) -> list[TextBlock]:
        if not value["changed"]:
            return [TextBlock(text=value["message"])]
        if value.get("command") == "insert":
            summary = (
                tr('python.fs_tools.a3a93a295338', p0=f"{value['path']}", p1=f"{value['inserted_line']}", p2=f"{value['inserted_lines']}", p3=f"{value['lines_before']}", p4=f"{value['lines_after']}")
            )
        else:
            summary = (
                tr('python.fs_tools.6fbcb4cecbc9', p0=f"{value['path']}", p1=f"{value['replacements']}", p2=f"{value['lines_before']}", p3=f"{value['lines_after']}")
            )
        return [TextBlock(text=f"{summary}\n{value['message']}")]

    def glob_tool(args: Any, ctx: ToolRunContext) -> Any:
        """按 glob 模式查找文件。"""
        root = Path(ctx.cwd or cwd or ".").expanduser().resolve()
        pattern = args["pattern"]
        limit = int(args.get("limit", 200))
        ignores = set(DEFAULT_IGNORES)
        matches: list[str] = []
        for path in islice(iter_files(root, pattern, ignores, recursive=True), max(0, limit)):
            matches.append(str(path.relative_to(root)))
        return {"pattern": pattern, "count": len(matches), "files": matches, "truncated": len(matches) >= limit}

    def glob_to_content(args: Any, value: Any) -> list[TextBlock]:
        if not value["files"]:
            return [TextBlock(text=tr('python.fs_tools.e62413202de8', p0=f"{value['pattern']}"))]
        text = "\n".join(value["files"])
        suffix = tr('python.fs_tools.1caa04da3255') if value["truncated"] else ""
        return [TextBlock(text=tr('python.fs_tools.13194958919d', p0=f"{value['pattern']}", p1=f"{value['count']}", p2=f'{text}', p3=f'{suffix}'))]

    async def grep_tool(args: Any, ctx: ToolRunContext) -> Any:
        """在文件中搜索正则表达式。"""
        root = Path(ctx.cwd or cwd or ".").expanduser().resolve()
        from .fs_grep import search
        return await search(root, args, DEFAULT_IGNORES, ctx)

    def grep_to_content(args: Any, value: Any) -> list[TextBlock]:
        note = ''
        if value.get('skipped_file_count'):
            note += tr('fs.grep.summary.skipped', count=value['skipped_file_count'],
                       files=tr('ui.common.list_separator').join(value['skipped_files']))
        if value.get('partial_file_count'):
            note += tr('fs.grep.summary.partial', count=value['partial_file_count'],
                       files=tr('ui.common.list_separator').join(value['partial_files']))
        if not value["matches"]:
            return [TextBlock(text=tr('python.fs_tools.b9069719aaba', p0=f"{value['pattern']}") + note)]
        if value.get('context_lines'):
            rows = [(m, ':') for m in value['matches']] + [(m, '-') for m in value['context_lines']]
            rows.sort(key=lambda row: (row[0]['file'], row[0]['line']))
            lines = [f"{m['file']}{marker}{m['line']}{marker} {m['text']}" for m, marker in rows]
        else:
            lines = [f"{m['file']}:{m['line']}: {m['text']}" for m in value["matches"]]
        if value.get('context_truncated'):
            note += tr('fs.grep.context_truncated')
        if value['count'] >= int(args.get('limit', MAX_GREP_MATCHES)):
            note += tr('python.fs_tools.b113144420f9')
        return [TextBlock(text="\n".join(lines) + note)]

    return [
        ToolDefinition(
            schema={
                "description_key": 'python.fs_tools.58f503cb7fbd',
                "parameter_keys": {
                    ('properties', 'path', 'description'): 'python.fs_tools.0437841ba2ea',
                    ('properties', 'offset', 'description'): 'python.fs_tools.fa927f173c08',
                    ('properties', 'limit', 'description'): 'python.fs_tools.0b24a757cafa',
                    ('properties', 'view_range', 'description'): 'python.fs_tools.271cb1b39a7a',
                    ('properties', 'line_numbers', 'description'): 'tools.read.line_numbers',
                },
                "name": "read",
                "description": (
                    tr('python.fs_tools.58f503cb7fbd')
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": tr('python.fs_tools.0437841ba2ea')},
                        "offset": {"type": "integer", "minimum": 1, "description": tr('python.fs_tools.fa927f173c08')},
                        "limit": {"type": "integer", "minimum": 1, "description": tr('python.fs_tools.0b24a757cafa')},
                        "view_range": {
                            "type": "array",
                            "items": {"type": "integer"},
                            "minItems": 2,
                            "maxItems": 2,
                            "description": tr('python.fs_tools.271cb1b39a7a'),
                        },
                        "line_numbers": {"type": "boolean", "description": tr('tools.read.line_numbers')},
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
            },
            execute=read_tool,
            argument_semantics={'path': 'path'},
            effect='read', idempotent=True,
            to_content=read_to_content,
            concurrency_safe=True,
        ),
        ToolDefinition(
            schema={
                "description_key": 'python.fs_tools.56485391272f',
                "parameter_keys": {
                    ('properties', 'path', 'description'): 'python.fs_tools.c71200b5952e',
                    ('properties', 'content', 'description'): 'python.fs_tools.1f3e2d71abf8',
                },
                "name": "write",
                "description": (
                    tr('python.fs_tools.56485391272f')
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": tr('python.fs_tools.c71200b5952e')},
                        "content": {"type": "string", "description": tr('python.fs_tools.1f3e2d71abf8')},
                    },
                    "required": ["path", "content"],
                    "additionalProperties": False,
                },
            },
            execute=write_tool,
            argument_semantics={'path': 'path', 'content': 'text'},
            hook_names=('Write',),
            write_paths=lambda args, ctx: [resolve_path(ctx.cwd or cwd, args['path'])],
            effect='workspace_write',
            progress=lambda args, value: value.get('changed'),
            progress_key=file_progress_key,
            to_content=write_to_content,
            describe=lambda args, value: {key: item for key, item in value.items() if not key.startswith('_')},
            concurrency_safe=False,
        ),
        ToolDefinition(
            schema={
                "description_key": 'python.fs_tools.ae1a600c4b82',
                "parameter_keys": {
                    ('properties', 'path', 'description'): 'python.fs_tools.c71200b5952e',
                    ('properties', 'old_string', 'description'): 'python.fs_tools.bffd37540162',
                    ('properties', 'new_string', 'description'): 'python.fs_tools.d936c074aa80',
                    ('properties', 'insert_line', 'description'): 'python.fs_tools.0aa5909cbebd',
                    ('properties', 'replace_all', 'description'): 'python.fs_tools.a41177f73d0b',
                },
                "name": "edit",
                "description": (
                    tr('python.fs_tools.ae1a600c4b82')
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": tr('python.fs_tools.c71200b5952e')},
                        "old_string": {
                            "type": ["string", "null"],
                            "description": tr('python.fs_tools.bffd37540162'),
                        },
                        "new_string": {
                            "type": ["string", "null"],
                            "description": tr('python.fs_tools.d936c074aa80'),
                        },
                        "insert_line": {
                            "type": ["integer", "null"],
                            "description": tr('python.fs_tools.0aa5909cbebd'),
                        },
                        "replace_all": {
                            "type": "boolean",
                            "description": tr('python.fs_tools.a41177f73d0b'),
                        },
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
            },
            execute=edit_tool,
            argument_semantics={'path': 'path', 'old_string': 'text', 'new_string': 'text'},
            hook_names=('Edit',),
            write_paths=lambda args, ctx: [resolve_path(ctx.cwd or cwd, args['path'])],
            effect='workspace_write',
            progress=lambda args, value: value.get('changed'),
            progress_key=file_progress_key,
            to_content=edit_to_content,
            describe=lambda args, value: {key: value[key] for key in ('path', 'diff', 'changed', 'diff_truncated', 'diff_unavailable', 'lines_before', 'lines_after') if key in value},
            concurrency_safe=False,
        ),
        ToolDefinition(
            schema={
                "description_key": 'python.fs_tools.bffb6fa7d601',
                "parameter_keys": {
                    ('properties', 'pattern', 'description'): 'python.fs_tools.e870f02b0bd9',
                    ('properties', 'limit', 'description'): 'python.fs_tools.89127626b49d',
                },
                "name": "glob",
                "description": tr('python.fs_tools.bffb6fa7d601'),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string", "description": tr('python.fs_tools.e870f02b0bd9')},
                        "limit": {"type": "integer", "description": tr('python.fs_tools.89127626b49d')},
                    },
                    "required": ["pattern"],
                    "additionalProperties": False,
                },
            },
            execute=glob_tool,
            argument_semantics={'pattern': 'path'},
            effect='read', idempotent=True,
            to_content=glob_to_content,
            concurrency_safe=True,
        ),
        ToolDefinition(
            schema={
                "description_key": 'python.fs_tools.12d07b96f522',
                "parameter_keys": {
                    ('properties', 'pattern', 'description'): 'python.fs_tools.af33e62808ca',
                    ('properties', 'context', 'description'): 'fs.grep.context',
                    ('properties', 'path', 'description'): 'python.fs_tools.534b2b9acbda',
                    ('properties', 'glob', 'description'): 'python.fs_tools.d78e18e95081',
                    ('properties', 'limit', 'description'): 'python.fs_tools.92adf15e5466',
                },
                "name": "grep",
                "description": tr('python.fs_tools.12d07b96f522'),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string", "description": tr('python.fs_tools.af33e62808ca')},
                        "context": {"type": "integer", "minimum": 0, "maximum": 20,
                                    "description": tr('fs.grep.context')},
                        "path": {"type": "string", "description": tr('python.fs_tools.534b2b9acbda')},
                        "glob": {"type": "string", "description": tr('python.fs_tools.d78e18e95081')},
                        "limit": {"type": "integer", "description": tr('python.fs_tools.92adf15e5466'), "minimum": 0, "maximum": 200},
                    },
                    "required": ["pattern"],
                    "additionalProperties": False,
                },
            },
            execute=grep_tool,
            argument_semantics={'path': 'path', 'glob': 'path', 'pattern': 'text'},
            timeout=40.0,
            cancellable=True,
            effect='read', idempotent=True,
            to_content=grep_to_content,
            concurrency_safe=True,
        ),
    ]


__all__ = [
    "MAX_VIEW_CHARS",
    "apply_insert",
    "apply_str_replace",
    "build_fs_tools",
    "line_numbers_at",
    "match_offsets",
    "maybe_truncate",
    "normalize_view_range",
    "resolve_path",
    "view_directory",
]
