"""可信宿主的可选验证回执：绑定明确输入、真实前台命令及持久轮次。"""
from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath
import stat
import uuid

from ..core.errors import HarnessError
from ..core.file_handles import check_handle_root
from ..core.jsonutil import content_hash, to_jsonable
from ..i18n import tr
from .tool_activity import confirmed_tool_result, tool_invocations
from .turn_scope import turn_events

MAX_FILES = 32
MAX_CHECKS = 32
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 16 * 1024 * 1024


def invalid():
    """损坏回执明确拒绝，不推断为通过。"""
    return HarnessError(tr('verification.invalid'), code='verification/invalid')


def fingerprint(value):
    """对不可变协议快照稳定散列，不保存命令中的潜在秘密。"""
    return content_hash(value)


def _parts(name):
    if (not isinstance(name, str) or not name or '\\' in name or ':' in name
            or PurePosixPath(name).is_absolute() or any(part in ('', '.', '..') for part in name.split('/'))
            or any(part.casefold() == 'research' for part in name.split('/'))):
        raise ValueError(tr('verification.invalid'))
    return name.split('/')


def _read_version(base, name, budget):
    """固定句柄后核对普通文件及归属，读取前后核对文件身份。"""
    parts = _parts(name)
    descriptors = []
    try:
        if os.name == 'nt':
            path = base
            for part in parts:
                path = path / part
                info = path.lstat()
                if info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                    raise ValueError(tr('verification.invalid'))
            descriptor = os.open(path, os.O_RDONLY | os.O_BINARY | os.O_NOINHERIT)
        else:
            directory = os.open(base, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            descriptors.append(directory)
            for part in parts[:-1]:
                directory = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                descriptors.append(directory)
            descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        descriptors.append(descriptor)
        check_handle_root(descriptor, base)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > min(MAX_FILE_BYTES, budget[0]):
            raise ValueError(tr('verification.invalid'))
        digest = hashlib.sha256()
        count = 0
        while chunk := os.read(descriptor, min(65536, min(MAX_FILE_BYTES, budget[0]) - count + 1)):
            count += len(chunk)
            if count > MAX_FILE_BYTES or count > budget[0]:
                raise ValueError(tr('verification.invalid'))
            digest.update(chunk)
        after = os.fstat(descriptor)
        identity = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        if identity(before) != identity(after) or count != before.st_size:
            raise ValueError(tr('verification.invalid'))
        check_handle_root(descriptor, base)
        budget[0] -= count
        return digest.hexdigest()
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def input_versions(root, paths, *, cache=None, budget=None):
    """只读显式声明文件；每次聚合限制数量、总字节并去重读取。"""
    base = Path(root).resolve(strict=True)
    if any(part.casefold() == 'research' for part in base.parts):
        raise ValueError(tr('verification.invalid'))
    if (not isinstance(paths, (list, tuple)) or not 1 <= len(paths) <= MAX_FILES
            or not all(isinstance(name, str) for name in paths) or len(set(paths)) != len(paths)):
        raise ValueError(tr('verification.invalid'))
    cache = {} if cache is None else cache
    budget = [MAX_TOTAL_BYTES] if budget is None else budget
    rows = []
    for name in sorted(paths):
        _parts(name)
        key = (str(base), name)
        if key not in cache:
            if len(cache) >= MAX_FILES:
                raise ValueError(tr('verification.invalid'))
            cache[key] = _read_version(base, name, budget)
        rows.append((name, cache[key]))
    return tuple(rows)


def _invocation(row, tool, arguments_hash):
    """原始调用为参数事实源；结果携带冲突参数不能覆盖它。"""
    if not confirmed_tool_result(row) or tool not in ('bash', 'pwsh'):
        raise invalid()
    start, result, value = row['_start_event'], row['_result_event'], row['_result']
    if (start.data.get('name') != tool
            or not isinstance(start.data.get('input'), Mapping) or fingerprint(start.data['input']) != arguments_hash
            or 'input' in result.data and fingerprint(result.data['input']) != arguments_hash):
        raise invalid()
    display = result.data.get('display', {})
    exited = (isinstance(display, Mapping) and display.get('execution_state') == 'exited'
              and type(display.get('exit_code')) is int and not display.get('background'))
    if not exited or display.get('encoding_errors'):
        return 'unknown'
    return 'passed' if display['exit_code'] == 0 and value['is_error'] is False else 'failed'


def _receipts(session, events):
    start = events[0]
    receipts, seen = [], set()
    rows = {row['_result_event'].seq: row for row in tool_invocations(events) if row['_result_event'] is not None}
    for event in session.events_of_type('note'):
        data = event.data
        if (data.get('kind') != 'verification_result' or data.get('session_id') != session.id
                or data.get('turn') != start.data['turn'] or data.get('run_id') != start.data.get('run_id')):
            continue
        inputs = data.get('inputs')
        if (len(receipts) >= MAX_CHECKS or not isinstance(data.get('capture_id'), str) or not data['capture_id']
                or data['capture_id'] in seen or type(data.get('result_seq')) is not int or event.seq <= data['result_seq']
                or not isinstance(inputs, (list, tuple)) or not 1 <= len(inputs) <= MAX_FILES
                or any(not isinstance(row, (list, tuple)) or len(row) != 2 or not isinstance(row[0], str)
                       or not isinstance(row[1], str) or len(row[1]) != 64
                       or any(char not in '0123456789abcdef' for char in row[1]) for row in inputs)
                or len({row[0] for row in inputs}) != len(inputs)
                or data.get('status') not in ('passed', 'failed', 'stale', 'unknown')
                or not isinstance(data.get('check_id'), str) or not data['check_id'] or len(data['check_id']) > 200
                or not isinstance(data.get('root'), str)):
            raise invalid()
        original = _invocation(rows.get(data['result_seq']), data.get('tool'), data.get('arguments_hash'))
        if data['status'] != 'stale' and data['status'] != original:
            raise invalid()
        for name, _ in inputs:
            _parts(name)
        seen.add(data['capture_id'])
        receipts.append(event)
    return receipts


@dataclass(frozen=True)
class VerificationCapture:
    """命令之前由可信宿主捕获，模型工具参数不能创建验证通过声明。"""

    id: str
    session_id: str
    run_id: str
    turn: int
    after_seq: int
    root: str
    check_id: str
    tool: str
    arguments_hash: str
    inputs: tuple[tuple[str, str], ...]

    @classmethod
    def begin(cls, session, *, turn, check_id, tool, arguments, paths):
        """同步宿主登记入口；异步宿主应在线程中捕获文件，不阻塞运行循环。"""
        events = turn_events(session.events, turn=turn)
        if (not events or events[-1].type == 'turn/end' or tool not in ('bash', 'pwsh')
                or not isinstance(check_id, str) or not check_id.strip() or len(check_id) > 200
                or not isinstance(arguments, Mapping) or not isinstance(session.header.cwd, str) or not session.header.cwd):
            raise invalid()
        root = str(Path(session.header.cwd).resolve())
        return cls(uuid.uuid4().hex, session.id, events[0].data['run_id'], turn, events[-1].seq,
                   root, check_id, tool, fingerprint(arguments), input_versions(root, paths))

    async def complete(self, session, *, result_seq):
        """仅持久化既有结果；同一捕获可回查，未知写入不得重放执行。"""
        if session.id != self.session_id:
            raise invalid()
        events = turn_events(session.events, turn=self.turn)
        receipts = _receipts(session, events)
        expected = {'capture_id': self.id, 'session_id': self.session_id, 'run_id': self.run_id,
                    'turn': self.turn, 'result_seq': result_seq, 'check_id': self.check_id, 'tool': self.tool,
                    'arguments_hash': self.arguments_hash, 'root': self.root, 'inputs': [list(row) for row in self.inputs]}
        existing = next((event for event in receipts if event.data['capture_id'] == self.id), None)
        if existing:
            if any(to_jsonable(existing.data.get(key)) != value for key, value in expected.items()):
                raise invalid()
            return existing
        if len(receipts) >= MAX_CHECKS or events[0].data.get('run_id') != self.run_id:
            raise invalid()
        row = next((row for row in tool_invocations(events) if row['_result_event'] is not None and row['_result_event'].seq == result_seq), None)
        original = _invocation(row, self.tool, self.arguments_hash)
        if row['_start_event'].seq <= self.after_seq:
            raise invalid()
        current = await asyncio.to_thread(input_versions, self.root, [name for name, _ in self.inputs])
        if (turn_events(session.events, turn=self.turn)[0] is not events[0]
                or str(Path(session.header.cwd).resolve()) != self.root):
            raise invalid()
        refreshed = _receipts(session, turn_events(session.events, turn=self.turn))
        existing = [event for event in refreshed if event.data['capture_id'] == self.id]
        if existing:
            if any(to_jsonable(existing[0].data.get(key)) != value for key, value in expected.items()):
                raise invalid()
            return existing[0]
        if len(refreshed) >= MAX_CHECKS:
            raise invalid()
        return await session.append('note', {'kind': 'verification_result', **expected,
                                            'status': 'stale' if current != self.inputs else original})


def verification_summary(session, events):
    """读取时核对当前显式输入；不是文件系统事务或业务验收判断。"""
    checks, cache, budget = [], {}, [MAX_TOTAL_BYTES]
    for event in _receipts(session, events):
        data = event.data
        status = data['status']
        try:
            if str(Path(session.header.cwd).resolve()) != data['root']:
                status = 'stale'
            elif input_versions(data['root'], [row[0] for row in data['inputs']], cache=cache, budget=budget) != tuple(tuple(row) for row in data['inputs']):
                status = 'stale'
        except (OSError, ValueError, KeyError, TypeError):
            status = 'unknown'
        checks.append({'check_id': data['check_id'], 'status': status, 'source_seq': event.seq,
                       'result_seq': data['result_seq'], 'paths': [row[0] for row in data['inputs']]})
    status = next((state for state in ('unknown', 'failed', 'stale') if any(row['status'] == state for row in checks)), 'passed' if checks else 'unverified')
    return status, checks
